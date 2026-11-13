"""LangGraph executor: compiles a DAGPlan into a runnable graph."""

from __future__ import annotations

import asyncio
import json
import operator
import os
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import RetryPromptPart, ThinkingPart, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import RunUsage, UsageLimits

from .catalog import Catalog
from .composio_source import expand_tool_call
from .documents import files_dir, guard_charts, parse_spec, write_deliverable
from .factory import build_worker, get_model
from .live import LiveFeed, stream_handler
from .models import get_info
from .pricing import estimate_cost
from .schema import DAGNode, DAGPlan, InputRecord, NodeStatus, RunStatus, ToolCallRecord
from .store import RunRecord, mark_interrupted
from .tools import NodeDeps

MAX_DEP_CHARS = 4000
MAX_PUBLISH_DEP_CHARS = 16000  # a publishing step presents its inputs, so it sees more of them
OUTPUT_TOOL = "final_result"  # pydantic-ai's tool for structured output; not shown as a tool call
MAX_RESULT_CHARS = 20000  # per tool result kept in the trace; search payloads can be huge
STOPPED = "stopped by user"


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def agent_limits(model_id: str | None = None, external_tools: bool = True) -> UsageLimits:
    """Per-agent ceilings, so one agent can't run up an unbounded bill.

    ORCHESTRATOR_MAX_TOOL_CALLS_PER_AGENT (default 8) and ORCHESTRATOR_MAX_INPUT_TOKENS_PER_AGENT
    (default 300k). Tool-heavy agents re-read every earlier tool result on each step, so their
    input tokens grow much faster than their number of calls. Pricey models may set a lower
    input cap of their own (ModelInfo.input_cap); the lower of the two applies.

    The tool-call cap is for external toolkits (search results are large). Agents with only
    built-in tools (calc and your custom tools: free, tiny results) may batch many calls in one
    reply, so they get ORCHESTRATOR_MAX_BUILTIN_TOOL_CALLS_PER_AGENT (default 40) instead; the
    input-token cap still bounds a loop.
    """
    input_cap = _env_int("ORCHESTRATOR_MAX_INPUT_TOKENS_PER_AGENT", 300_000)
    info = get_info(model_id) if model_id else None
    if info and info.input_cap:
        input_cap = min(input_cap, info.input_cap)
    return UsageLimits(
        tool_calls_limit=_env_int("ORCHESTRATOR_MAX_TOOL_CALLS_PER_AGENT", 8)
        if external_tools
        else _env_int("ORCHESTRATOR_MAX_BUILTIN_TOOL_CALLS_PER_AGENT", 40),
        input_tokens_limit=input_cap,
        request_limit=None,
    )


def max_parallel_agents() -> int:
    """Agents of one run that may execute at the same time (ORCHESTRATOR_MAX_PARALLEL_AGENTS, default 3)."""
    try:
        return max(1, int(os.environ.get("ORCHESTRATOR_MAX_PARALLEL_AGENTS", "3")))
    except ValueError:
        return 3

# Test/dev override: async fn(node, prompt) -> output. Bypasses the LLM.
Runner = Callable[[DAGNode, str], Awaitable[str]]


class FlowState(TypedDict):
    task: str
    outputs: Annotated[dict[str, str], operator.or_]


def clip(output: str, limit: int = MAX_DEP_CHARS) -> str:
    return output if len(output) <= limit else output[:limit] + "…"


def dep_limit(node: DAGNode) -> int:
    return MAX_PUBLISH_DEP_CHARS if node.deliverable else MAX_DEP_CHARS


def render_prompt(task: str, node: DAGNode, deps: dict[str, str]) -> str:
    lines = [f"Task: {task}", f"Your goal for this step: {node.goal}"]
    if deps:
        lines.append("Inputs from previous steps:")
        for dep_id, output in deps.items():
            lines.append(f"- {dep_id}: {clip(output, dep_limit(node))}")
    lines.append("Produce your result.")
    return "\n".join(lines)


def _cap(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return f"{text[:MAX_RESULT_CHARS]}… (truncated, {len(text):,} chars total)"


def _ok_from_result(text: str) -> bool:
    """Tools that answer with a JSON envelope report success there; anything else counts as success."""
    try:
        payload = json.loads(text)
    except ValueError:
        return True
    return bool(payload.get("successful", True)) if isinstance(payload, dict) else True


def extract_trace(result) -> tuple[list[str], list[ToolCallRecord]]:
    """(thoughts, tool calls with args and results) from a pydantic-ai run result.

    A tool that raised (or returned an error the toolset turned into a retry) comes back as a
    RetryPromptPart rather than a ToolReturnPart; both are recorded so failures stay visible.
    """
    thoughts: list[str] = []
    calls: dict[str, ToolCallRecord] = {}
    try:
        for message in result.all_messages():
            for part in getattr(message, "parts", []) or []:
                if isinstance(part, ThinkingPart):
                    if part.content.strip():
                        thoughts.append(part.content)
                elif isinstance(part, ToolCallPart) and not part.tool_name.startswith(OUTPUT_TOOL):
                    calls[part.tool_call_id] = ToolCallRecord(name=part.tool_name, args=part.args_as_json_str())
                elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                    text = part.model_response_str()
                    calls[part.tool_call_id].result = text
                    calls[part.tool_call_id].ok = _ok_from_result(text)
                elif isinstance(part, RetryPromptPart) and part.tool_call_id in calls:
                    content = part.content if isinstance(part.content, str) else json.dumps(part.content, default=str)
                    calls[part.tool_call_id].result = content
                    calls[part.tool_call_id].ok = False
    except Exception:
        pass
    expanded = [r for record in calls.values() for r in expand_tool_call(record)]
    for record in expanded:
        if record.result is not None:
            record.result = _cap(record.result)
    return thoughts, expanded


def extract_usage(result) -> tuple[int, int]:
    """(input_tokens, output_tokens) from a pydantic-ai run result; (0, 0) if unavailable."""
    try:
        usage = result.usage() if callable(result.usage) else result.usage
    except Exception:
        return 0, 0
    inp = getattr(usage, "input_tokens", None)
    if inp is None:  # legacy alias
        inp = getattr(usage, "request_tokens", 0)
    out = getattr(usage, "output_tokens", None)
    if out is None:  # legacy alias
        out = getattr(usage, "response_tokens", 0)
    return int(inp or 0), int(out or 0)


async def publish(node: DAGNode, output: object, prompt: str, record: RunRecord) -> str:
    """Render a publishing step's spec to its file; returns the text version shown as its output.

    Charts are checked against the prompt (what the step actually saw) before rendering.
    """
    detail = record.nodes[node.id]
    spec, dropped = guard_charts(parse_spec(output), prompt)
    for title in dropped:
        detail.logs.append(f"chart dropped: {title!r} uses numbers not found in the step's inputs")
    file = await asyncio.to_thread(write_deliverable, spec, node.deliverable, files_dir() / record.run_id, node.id)
    detail.files = [file]
    detail.logs.append(f"wrote {file.name} ({file.size:,} bytes)")
    return spec.to_markdown()


def build_graph(
    plan: DAGPlan,
    record: RunRecord,
    model=None,
    runner: Runner | None = None,
    catalog: Catalog | None = None,
    stream: bool = False,
):
    """`stream` makes agents stream their model responses into record.live (the live view)."""
    catalog = catalog or Catalog()
    plan.order()  # validate before compiling
    # Each agent runs on its own model (override, run-wide model, or its tier's); a passed-in
    # model (tests) replaces all of them. One model object per distinct id.
    forced = (getattr(model, "model_name", None) or "unknown") if model else None
    model_ids = {n.id: forced or record.model_for(n) for n in plan.nodes}
    resolved: dict[str, object] = {}
    if runner is None:
        for model_id in dict.fromkeys(model_ids.values()):
            resolved[model_id] = model or get_model(model_id)
    agents = {} if runner else {n.id: build_worker(n, resolved[model_ids[n.id]]) for n in plan.nodes}

    def make_fn(node: DAGNode):
        async def run_node(state: FlowState):
            detail = record.nodes[node.id]
            model_id = model_ids[node.id]
            blocked = [
                d for d in node.depends_on if record.nodes[d].status in (NodeStatus.FAILED, NodeStatus.SKIPPED)
            ]
            if blocked:
                detail.status = NodeStatus.SKIPPED
                detail.logs.append(f"skipped: upstream {', '.join(blocked)} did not succeed")
                record.emit("node_skipped", node.id, {"blocked_by": blocked})
                return {"outputs": {node.id: ""}}
            detail.status = NodeStatus.RUNNING
            detail.model = model_id if runner is None else None
            detail.started_at = time.time()
            record.emit("node_started", node.id)
            deps = {d: state["outputs"].get(d, "") for d in node.depends_on}
            detail.inputs = [
                InputRecord(from_node=d, content=clip(o, dep_limit(node)), original_chars=len(o)) for d, o in deps.items()
            ]
            prompt = render_prompt(state["task"], node, deps)
            detail.input = prompt
            detail.logs.append(f"prompt built ({len(prompt)} chars, {len(node.depends_on)} deps)")
            # Shared with the agent so tokens are counted however the run ends (success, failure,
            # budget limit, stop); only a request cut off mid-flight goes unreported.
            usage = RunUsage()

            def count_usage() -> None:
                if runner is None:
                    detail.input_tokens, detail.output_tokens = usage.input_tokens, usage.output_tokens
                    detail.cost_usd = estimate_cost(model_id, usage.input_tokens, usage.output_tokens)

            try:
                if runner is not None:
                    output = await runner(node, prompt)
                else:
                    # External toolkits open here, after the skip check, so skipped nodes cost nothing.
                    toolsets = await catalog.toolsets_for(node.tools)
                    if toolsets:
                        detail.logs.append(f"opened {len(toolsets)} external toolset(s)")
                    live = {}
                    if stream:
                        record.live[node.id] = feed = LiveFeed()
                        live = {"event_stream_handler": stream_handler(feed)}
                    result = await agents[node.id].run(
                        prompt,
                        deps=NodeDeps(record=record, node_id=node.id),
                        toolsets=toolsets,
                        usage=usage,
                        usage_limits=agent_limits(model_id, external_tools=bool(toolsets)),
                        **live,
                    )
                    output = result.output
                    thoughts, tool_calls = extract_trace(result)
                    detail.thoughts.extend(thoughts)
                    detail.tool_calls.extend(tool_calls)
                    count_usage()  # before node_succeeded so the event carries real numbers
                if node.deliverable:
                    output = await publish(node, output, prompt, record)
                output = str(output)
                detail.output = output
                detail.status = NodeStatus.SUCCEEDED
                detail.logs.append(f"done ({len(output)} chars)")
                record.emit(
                    "node_succeeded",
                    node.id,
                    {
                        "input_tokens": detail.input_tokens,
                        "output_tokens": detail.output_tokens,
                        "cost_usd": detail.cost_usd,
                        "files": [f.name for f in detail.files],
                    },
                )
                return {"outputs": {node.id: output}}
            except Exception as e:
                detail.status = NodeStatus.FAILED
                if isinstance(e, UsageLimitExceeded):
                    detail.error = f"budget limit reached: {e}"
                else:
                    detail.error = f"{type(e).__name__}: {e}"
                detail.logs.append(f"failed: {detail.error}")
                record.emit("node_failed", node.id, {"error": detail.error})
                return {"outputs": {node.id: f"ERROR: {detail.error}"}}
            finally:
                record.live.pop(node.id, None)
                detail.ended_at = time.time()
                if runner is None:
                    count_usage()
                    detail.logs.append(
                        f"tokens: {usage.input_tokens} in / {usage.output_tokens} out (~${detail.cost_usd:.6f})"
                    )

        return run_node

    graph: StateGraph = StateGraph(FlowState)
    for node in plan.nodes:
        graph.add_node(node.id, make_fn(node))
    for node in plan.nodes:
        if not node.depends_on:
            graph.add_edge(START, node.id)
        else:
            # List form makes LangGraph wait for ALL parents; one edge per parent would
            # fire the node as soon as the first parent finishes.
            graph.add_edge(list(dict.fromkeys(node.depends_on)), node.id)
    depended = {d for n in plan.nodes for d in n.depends_on}
    for node in plan.nodes:
        if node.id not in depended:
            graph.add_edge(node.id, END)
    return graph.compile(checkpointer=InMemorySaver())


def run_totals(record: RunRecord) -> dict[str, float]:
    return record.totals()


def close_cancelled(record: RunRecord, reason: str = STOPPED) -> None:
    """Mark a run stopped: finished nodes keep their results, in-flight ones are cancelled, the rest skipped."""
    now = time.time()
    for detail in record.nodes.values():
        if detail.status == NodeStatus.RUNNING:
            detail.status = NodeStatus.CANCELLED
            detail.error = reason
            detail.ended_at = detail.ended_at or now
            detail.logs.append(f"cancelled: {reason}")
        elif detail.status == NodeStatus.PENDING:
            detail.status = NodeStatus.SKIPPED
            detail.logs.append(f"skipped: {reason}")
    record.status = RunStatus.CANCELLED
    record.emit("run_cancelled", data={"reason": reason})
    record.emit("run_finished", data={"status": record.status.value, **run_totals(record)})


async def execute_run(
    record: RunRecord,
    model=None,
    runner: Runner | None = None,
    catalog: Catalog | None = None,
    max_parallel: int | None = None,
    stream: bool = False,
) -> RunRecord:
    record.status = RunStatus.RUNNING
    record.emit("run_started")
    try:
        app = build_graph(record.plan, record, model=model, runner=runner, catalog=catalog, stream=stream)
        await app.ainvoke(
            {"task": record.task, "outputs": {}},
            config={
                "configurable": {"thread_id": record.run_id},
                "max_concurrency": max_parallel or max_parallel_agents(),
            },
        )
        failed = [n for n in record.nodes.values() if n.status == NodeStatus.FAILED]
        record.status = RunStatus.FAILED if failed else RunStatus.SUCCEEDED
    except asyncio.CancelledError:
        # In-flight LLM/tool calls were abandoned at their await points. A user stop is a
        # cancellation; anything else (server shutdown/reload) is an interruption.
        if record.stop_requested:
            close_cancelled(record)
        else:
            mark_interrupted(record)
        raise
    except Exception as e:
        record.status = RunStatus.FAILED
        record.error = f"{type(e).__name__}: {e}"
        record.emit("run_failed", data={"error": record.error})
    record.emit("run_finished", data={"status": record.status.value, **run_totals(record)})
    return record
