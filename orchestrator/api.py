"""FastAPI surface: submit tasks, inspect runs/nodes, stream events, stop runs."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints, field_validator

from .catalog import BUILTIN, Catalog, split_id
from .composio_source import ComposioSource
from .estimate import estimate_plan
from .executor import Runner, agent_limits, close_cancelled, execute_run, extract_usage, run_totals
from .live import live_ops
from .documents import files_dir
from .factory import MAX_AGENTS_HINT, apply_delivery, build_supervisor, get_model, plan_problems
from .pricing import estimate_cost
from .manager import RunManager
from typing import Annotated, Literal

from .models import DEFAULT_MODE, MODES, as_dicts, get_info, modes_as_dicts
from .schema import DAGPlan, NodeStatus, RunStatus
from .store import RunRecord, RunStore, mark_interrupted


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Queued runs had nothing in flight when the server stopped; put them back in line.
    for record in app.state.store.queued():
        app.state.manager.submit(record, _launch)
    yield


app = FastAPI(title="Dagwood", lifespan=lifespan)
DB_PATH = os.environ.get("ORCHESTRATOR_DB", "runs.db")
app.state.store = RunStore(DB_PATH)
_composio = ComposioSource.from_env()
app.state.catalog = Catalog(sources={_composio.name: _composio} if _composio else {}, db_path=DB_PATH)
app.state.runner: Runner | None = None  # test/dev override, bypasses the LLM
app.state.planner_model = None  # test/dev override for the supervisor's model
app.state.manager = RunManager()

# Hostnames this server answers to. There is no auth, so a web page that re-points its own domain at
# 127.0.0.1 (DNS rebinding) could otherwise read run history and submit tasks; its requests still
# carry the page's hostname in Host, which is refused here. "*" allows any host.
DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1,::1"
app.state.allowed_hosts = {
    h.strip().lower().strip("[]")
    for h in os.environ.get("ORCHESTRATOR_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",")
    if h.strip()
}


def _hostname(host: str) -> str:
    """The Host header without its port: "localhost:8000" -> "localhost", "[::1]:8000" -> "::1"."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


@app.middleware("http")
async def check_host(request: Request, call_next):
    allowed = app.state.allowed_hosts
    if "*" not in allowed and _hostname(request.headers.get("host", "")) not in allowed:
        return PlainTextResponse(
            "Host not allowed. Dagwood answers only to localhost; see ORCHESTRATOR_ALLOWED_HOSTS in .env.example.",
            status_code=400,
        )
    return await call_next(request)


# A kind of agent the user asks for, used as a step's role ("researcher", "fact checker").
AgentType = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30, pattern=r"^[A-Za-z][A-Za-z0-9 _-]*$")
]


class TaskRequest(BaseModel):
    task: str
    plan: DAGPlan | None = None  # inject a plan to skip the LLM planner
    require_approval: bool = False  # pause after planning until POST /runs/{id}/approve
    model: str | None = None  # a listed model id: the planner and every agent run on it (no tier routing)
    mode: str | None = None  # cost mode routing agents by tier (GET /modes); default balanced
    # auto: the planner adds a publishing step when the task asks for a file; none: text only;
    # pdf / slides / pptx: always end with a publishing step producing that file
    deliver: Literal["auto", "none", "pdf", "slides", "pptx"] = "auto"
    # Optional hints for the planner (ignored with an injected plan): how many agents the plan should
    # have, and which kinds. The planner follows them where the task allows; neither is enforced.
    agents: int | None = Field(default=None, ge=1, le=MAX_AGENTS_HINT)
    agent_types: list[AgentType] = Field(default_factory=list, max_length=6)

    @field_validator("agent_types")
    @classmethod
    def _unique_types(cls, types: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for t in types:
            seen.setdefault(t.lower(), t)
        return list(seen.values())


class TaskResponse(BaseModel):
    run_id: str


class ToolkitRef(BaseModel):
    id: str  # catalog id, e.g. "composio:github"


class RunEdit(BaseModel):
    """Give exactly one: a mode routes agents by tier, a model runs every agent (the planner has run)."""

    model: str | None = None
    mode: str | None = None


class NodeEdit(BaseModel):
    """Fields a reviewer may change on a node while the plan awaits approval; None = unchanged."""

    goal: str | None = None
    instructions: str | None = None
    tools: list[str] | None = None
    tier: Literal["light", "standard", "heavy"] | None = None
    model: str | None = None  # pin this agent to a model; "" = back to its tier's model
    deliverable: Literal["pdf", "slides", "pptx", ""] | None = None  # "" = not a publishing step


def _store() -> RunStore:
    return app.state.store


def _catalog() -> Catalog:
    return app.state.catalog


def _manager() -> RunManager:
    return app.state.manager


def _current_user() -> str:
    """Owner of new runs; the per-user run limit is keyed on this. Replace with the auth identity."""
    return "default"


def _validate_plan(plan: DAGPlan) -> None:
    """422 unless the plan is a non-empty DAG whose nodes only use enabled toolkits."""
    problems = plan_problems(plan, _catalog())
    if problems:
        raise HTTPException(422, "; ".join(problems))


def _launch(record: RunRecord):
    return execute_run(record, None, app.state.runner, _catalog(), stream=True)


def _fail(record: RunRecord, error: str) -> None:
    record.status = RunStatus.FAILED
    record.error = error
    record.emit("run_failed", data={"error": error})
    record.emit("run_finished", data={"status": record.status.value})


def _after_planning(record: RunRecord, require_approval: bool) -> None:
    if require_approval:
        record.status = RunStatus.AWAITING_APPROVAL
        record.emit("awaiting_approval")
    else:
        record.status = RunStatus.PENDING
        _manager().submit(record, _launch)


async def _plan_run(record: RunRecord, require_approval: bool) -> None:
    try:
        model = app.state.planner_model or get_model(record.model)
        supervisor = build_supervisor(
            _catalog(), model=model, deliver=record.deliver, agents=record.agents, agent_types=record.agent_types
        )
        result = await supervisor.run(f"Task: {record.task}\nReturn the DAG plan.")
        tokens_in, tokens_out = extract_usage(result)
        record.planner_usage = {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "cost_usd": estimate_cost(getattr(model, "model_name", "unknown"), tokens_in, tokens_out),
        }
    except asyncio.CancelledError:
        if record.stop_requested:
            close_cancelled(record)
        else:
            mark_interrupted(record)
        raise
    except KeyError as e:
        _fail(record, f"planner unavailable (missing env var: {e})")
        return
    except Exception as e:
        _fail(record, f"planner failed: {type(e).__name__}: {e}")
        return
    plan = apply_delivery(result.output, record.deliver)
    problems = plan_problems(plan, _catalog())
    if problems:
        _fail(record, "planner returned an invalid plan: " + "; ".join(problems))
        return
    record.set_plan(plan)
    record.emit("plan_ready", data={"nodes": len(plan.nodes)})
    _after_planning(record, require_approval)


@app.post("/tasks", response_model=TaskResponse)
async def submit_task(req: TaskRequest):
    """Returns immediately. Without an injected plan the run starts in `planning`."""
    if req.mode is not None and req.mode not in MODES:
        raise HTTPException(422, f"unknown mode {req.mode!r}; see GET /modes")
    if req.model is not None and get_info(req.model) is None:
        raise HTTPException(422, f"unknown model {req.model!r}; see GET /models")
    # A model alone means one model for everything; otherwise route by mode.
    mode = req.mode if req.mode is not None or req.model is not None else DEFAULT_MODE
    model = req.model or MODES[mode]["planner"]
    if req.plan is not None:
        plan = apply_delivery(req.plan, req.deliver)
        _validate_plan(plan)
        record = _store().create(req.task, plan, owner=_current_user(), model=model, mode=mode)
        record.deliver = req.deliver
        _after_planning(record, req.require_approval)
    else:
        record = _store().create(
            req.task, DAGPlan(task=req.task, nodes=[]), owner=_current_user(), model=model, mode=mode
        )
        record.deliver = req.deliver
        record.agents, record.agent_types = req.agents, req.agent_types
        record.status = RunStatus.PLANNING
        record.emit("planning_started")
        _manager().plan(record, _plan_run(record, req.require_approval))
    return TaskResponse(run_id=record.run_id)


def _awaiting(run_id: str):
    record = _store().get(run_id)
    if record is None:
        raise HTTPException(404, "unknown run")
    if record.status != RunStatus.AWAITING_APPROVAL:
        raise HTTPException(409, f"run is {record.status.value}, not awaiting approval")
    return record


@app.post("/runs/{run_id}/approve")
async def approve_run(run_id: str):
    record = _awaiting(run_id)
    _validate_plan(record.plan)  # toolkits may have been disabled since the plan was built
    record.status = RunStatus.PENDING  # set before scheduling so a second approve gets 409
    record.estimate = _estimate(record)  # kept so the result can be compared with the forecast
    record.emit("plan_approved")
    _manager().submit(record, _launch)  # may queue behind the user's other runs
    return {"run_id": run_id, "status": record.status.value}


def _estimate(record: RunRecord) -> dict:
    return estimate_plan(
        record.plan,
        _store(),
        input_cap=agent_limits().input_tokens_limit,
        model_id=record.model,
        models={n.id: record.model_for(n) for n in record.plan.nodes},
    )


@app.get("/runs/{run_id}/estimate")
async def run_estimate(run_id: str):
    """Forecast cost of executing the current plan, learned from this server's past agents."""
    record = _store().get(run_id)
    if record is None:
        raise HTTPException(404, "unknown run")
    return {**_estimate(record), "planner_cost_usd": record.planner_usage["cost_usd"]}


@app.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Hard stop: finished agents keep their results, running ones are cancelled, the rest skipped."""
    record = _store().get(run_id)
    if record is None:
        raise HTTPException(404, "unknown run")
    if record.status.terminal:
        raise HTTPException(409, f"run is already {record.status.value}")
    await _manager().cancel(record)
    return {"run_id": run_id, "status": record.status.value}


@app.post("/runs/{run_id}/reject")
async def reject_run(run_id: str):
    record = _awaiting(run_id)
    for detail in record.nodes.values():
        detail.status = NodeStatus.SKIPPED
        detail.logs.append("skipped: plan rejected")
    record.status = RunStatus.REJECTED
    record.emit("plan_rejected")
    record.emit("run_finished", data={"status": record.status.value})
    return {"run_id": run_id, "status": record.status.value}


@app.patch("/runs/{run_id}")
async def edit_run(run_id: str, edit: RunEdit):
    """Change the agents' mode or model while the plan awaits approval; the estimate re-prices to match."""
    record = _awaiting(run_id)
    if (edit.model is None) == (edit.mode is None):
        raise HTTPException(422, "give exactly one of model or mode")
    if edit.mode is not None:
        if edit.mode not in MODES:
            raise HTTPException(422, f"unknown mode {edit.mode!r}; see GET /modes")
        previous, record.mode = record.mode, edit.mode
        record.emit("mode_changed", data={"from": previous, "to": edit.mode})
    else:
        if get_info(edit.model) is None:
            raise HTTPException(422, f"unknown model {edit.model!r}; see GET /models")
        previous, record.model, record.mode = record.model, edit.model, None
        record.emit("model_changed", data={"from": previous, "to": edit.model})
    return {"run_id": run_id, "model": record.model, "mode": record.mode}


@app.patch("/runs/{run_id}/nodes/{node_id}")
async def edit_node(run_id: str, node_id: str, edit: NodeEdit):
    record = _awaiting(run_id)
    node = next((n for n in record.plan.nodes if n.id == node_id), None)
    if node is None:
        raise HTTPException(404, "unknown node")
    if edit.goal is not None and not edit.goal.strip():
        raise HTTPException(422, "goal cannot be empty")
    changes = edit.model_dump(exclude_none=True)
    if changes.get("model") == "":
        changes["model"] = None  # clear the override
    if changes.get("deliverable") == "":
        changes["deliverable"] = None  # no longer a publishing step
    edited = node.model_copy(update=changes)
    _validate_plan(DAGPlan(task=record.plan.task, nodes=[edited if n is node else n for n in record.plan.nodes]))
    record.plan.nodes[record.plan.nodes.index(node)] = edited
    detail = record.nodes[node_id]
    detail.goal, detail.instructions, detail.tools = edited.goal, edited.instructions, list(edited.tools)
    detail.tier = edited.tier
    detail.logs.append(f"edited by reviewer: {', '.join(changes)}")
    record.emit("plan_updated", node_id, {"edited": list(changes)})
    return {"run_id": run_id, "node_id": node_id}


@app.delete("/runs/{run_id}/nodes/{node_id}")
async def delete_node(run_id: str, node_id: str):
    record = _awaiting(run_id)
    node = next((n for n in record.plan.nodes if n.id == node_id), None)
    if node is None:
        raise HTTPException(404, "unknown node")
    if len(record.plan.nodes) == 1:
        raise HTTPException(422, "a plan needs at least one node")
    # Children inherit the deleted node's parents so execution order is preserved.
    remaining = []
    for n in record.plan.nodes:
        if n is node:
            continue
        if node_id in n.depends_on:
            deps = [p for d in n.depends_on for p in (node.depends_on if d == node_id else [d])]
            n = n.model_copy(update={"depends_on": list(dict.fromkeys(deps))})
        remaining.append(n)
    plan = DAGPlan(task=record.plan.task, nodes=remaining)
    _validate_plan(plan)
    record.plan = plan
    del record.nodes[node_id]
    record.emit("plan_updated", node_id, {"deleted": True})
    return {"run_id": run_id, "deleted": node_id}


@app.get("/models")
async def list_models():
    """Models a run may use, with prices; exactly one is flagged default."""
    return as_dicts()


@app.get("/modes")
async def list_modes():
    """Cost modes: which model each agent tier runs on; exactly one is flagged default."""
    return modes_as_dicts()


@app.get("/tools")
async def list_tools():
    """Toolkits the planner and plan editor may assign: built-ins plus enabled external ones."""
    return [asdict(t) for t in _catalog().enabled()]


def _external_source(name: str):
    try:
        return _catalog().source(name)
    except KeyError:
        raise HTTPException(503, f"tool source {name!r} is not configured (for composio, set COMPOSIO_API_KEY)")


@app.get("/tools/sources")
async def list_sources():
    return {"configured": sorted(_catalog().sources)}


@app.get("/tools/catalog")
async def browse_catalog(source: str = "composio", search: str | None = None, limit: int = 30):
    """Toolkits available from an external source, flagged with whether they are enabled."""
    src = _external_source(source)
    try:
        items = await asyncio.to_thread(src.search, search, limit)
    except Exception as e:
        raise HTTPException(502, f"{source} catalog failed: {type(e).__name__}: {e}")
    return [{**asdict(t), "enabled": _catalog().is_enabled(t.id)} for t in items]


@app.post("/tools/enabled")
async def enable_toolkit(ref: ToolkitRef):
    source, slug = split_id(ref.id)
    if source == BUILTIN:
        raise HTTPException(422, "built-in tools are always enabled")
    src = _external_source(source)
    try:
        info = await asyncio.to_thread(src.info, slug)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"{source} lookup failed: {type(e).__name__}: {e}")
    _catalog().enable(info)
    return asdict(info)


@app.delete("/tools/enabled/{toolkit_id}")
async def disable_toolkit(toolkit_id: str):
    if not _catalog().disable(toolkit_id):
        raise HTTPException(404, "toolkit is not enabled")
    return {"disabled": toolkit_id}


@app.post("/tools/connect")
async def connect_toolkit(ref: ToolkitRef):
    """A link where the user authorizes this toolkit's account; open it in a browser."""
    source, slug = split_id(ref.id)
    src = _external_source(source)
    try:
        url = await asyncio.to_thread(src.connect_link, slug)
    except Exception as e:
        raise HTTPException(502, f"{source} connect failed: {type(e).__name__}: {e}")
    return {"redirect_url": url}


@app.get("/runs/{run_id}/connections")
async def run_connections(run_id: str):
    """Connection status of every external toolkit the run's plan uses."""
    record = _store().get(run_id)
    if record is None:
        raise HTTPException(404, "unknown run")
    ids = list(dict.fromkeys(t for n in record.plan.nodes for t in n.tools))
    try:
        statuses = await _catalog().connections_for(ids)
    except KeyError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"connection check failed: {type(e).__name__}: {e}")
    return [asdict(s) for s in statuses]


@app.get("/runs")
async def list_runs(limit: int = 50):
    return _store().list(limit)


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    record = _store().get(run_id)
    if record is None:
        raise HTTPException(404, "unknown run")
    planned = {p.id: p for p in record.plan.nodes}
    return {
        "run_id": record.run_id,
        "task": record.task,
        "status": record.status.value,
        "model": record.model,
        "mode": record.mode,
        "deliver": record.deliver,
        "agents": record.agents,
        "agent_types": record.agent_types,
        "error": record.error,
        "queue_position": _manager().position(record.run_id),
        "created_at": record.created_at,
        "planner": record.planner_usage,
        "estimate": record.estimate,
        **run_totals(record),
        "nodes": [
            {
                "node_id": n.node_id,
                "role": n.role,
                "goal": n.goal,
                "tools": n.tools,
                "status": n.status.value,
                "error": n.error,
                "input_tokens": n.input_tokens,
                "output_tokens": n.output_tokens,
                "cost_usd": n.cost_usd,
                "tier": n.tier,
                # what it ran on once started; until then what it would run on now
                "model": n.model or (record.model_for(planned[n.node_id]) if n.node_id in planned else record.model),
                "model_pinned": bool(n.node_id in planned and planned[n.node_id].model),
                "deliverable": planned[n.node_id].deliverable if n.node_id in planned else None,
                "files": [f.model_dump() for f in n.files],
            }
            for n in record.nodes.values()
        ],
        "edges": [{**e, "chars": _chars_passed(record, e["from"], e["to"])} for e in record.plan.edges()],
    }


def _chars_passed(record, source: str, target: str) -> int | None:
    """Size of what `source` handed `target`, or None if the target hasn't received it yet."""
    detail = record.nodes.get(target)
    received = next((i for i in detail.inputs if i.from_node == source), None) if detail else None
    return len(received.content) if received else None


@app.get("/runs/{run_id}/files/{name}")
async def get_file(run_id: str, name: str, download: bool = False):
    """A file a publishing step wrote. Only names recorded on the run are served, never a client path.

    Generated HTML quotes untrusted text (agents read the web), so it runs in a sandbox: an opaque
    origin that can't reach this API. PDFs get no sandbox (Chrome won't render them in one).
    """
    record = _store().get(run_id)
    file = next((f for n in record.nodes.values() for f in n.files if f.name == name), None) if record else None
    path = files_dir() / run_id / name if file else None
    if file is None or not path.is_file():
        raise HTTPException(404, "unknown file")
    headers = {"X-Content-Type-Options": "nosniff"}
    if file.format == "slides":
        headers["Content-Security-Policy"] = "sandbox allow-scripts"
    disposition = "attachment" if download or file.format == "pptx" else "inline"
    return FileResponse(path, media_type=file.media_type, filename=name, content_disposition_type=disposition, headers=headers)


@app.get("/runs/{run_id}/nodes/{node_id}")
async def get_node(run_id: str, node_id: str):
    record = _store().get(run_id)
    if record is None:
        raise HTTPException(404, "unknown run")
    detail = record.nodes.get(node_id)
    if detail is None:
        raise HTTPException(404, "unknown node")
    return detail.model_dump()


# Each stream closes after this long and the browser's EventSource reconnects, resuming via
# Last-Event-ID. Uvicorn waits for open responses before it shuts down (including on --reload),
# so a stream left open on a run awaiting approval would otherwise hang the server forever.
STREAM_MAX_SECONDS = 15.0


@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request, after: int = 0):
    record = _store().get(run_id)
    if record is None:
        raise HTTPException(404, "unknown run")
    last_id = request.headers.get("last-event-id", "")
    start = int(last_id) + 1 if last_id.isdigit() else after

    async def gen():
        cursor = start
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        # Live output goes as named "live" events without ids: it is transient, so a reconnect
        # starts these from scratch (resending each running agent's transcript) rather than resuming.
        sent: dict[str, dict] = {}
        phases: dict[str, str] = {}
        yield "retry: 1000\n\n"  # reconnect after 1s when we close
        while True:
            for event in record.events[cursor:]:
                cursor += 1
                yield f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"
            for node_id, feed in list(record.live.items()):
                ops = live_ops(feed, sent.setdefault(node_id, {}))
                if ops or phases.get(node_id) != feed.phase:
                    phases[node_id] = feed.phase
                    payload = {"node_id": node_id, "phase": feed.phase, "ops": ops}
                    yield f"event: live\ndata: {json.dumps(payload)}\n\n"
            if record.status.terminal and cursor >= len(record.events):
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream")
