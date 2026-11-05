"""Run store with per-node details and an event log for SSE.

Live runs are plain in-memory RunRecords. When the store has a database path,
every emitted event also snapshots the record to SQLite, so runs survive a
restart; runs that were mid-flight at shutdown are marked interrupted on load.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from .models import DEFAULT_MODEL, LEGACY_MODEL, resolve_model
from .schema import DAGNode, DAGPlan, NodeDetail, NodeStatus, RunStatus

if TYPE_CHECKING:
    from .live import LiveFeed

INTERRUPTED = "interrupted by server restart"


def mark_interrupted(record: RunRecord) -> None:
    """Close out a run the server stopped (shutdown/restart), as opposed to one the user stopped."""
    for detail in record.nodes.values():
        if detail.status == NodeStatus.RUNNING:
            detail.status = NodeStatus.FAILED
            detail.error = INTERRUPTED
            detail.logs.append(f"failed: {INTERRUPTED}")
        elif detail.status == NodeStatus.PENDING:
            detail.status = NodeStatus.SKIPPED
            detail.logs.append(f"skipped: {INTERRUPTED}")
    record.status = RunStatus.FAILED
    record.error = INTERRUPTED
    record.emit("run_failed", data={"error": INTERRUPTED})
    record.emit("run_finished", data={"status": record.status.value})


class RunRecord:
    def __init__(
        self, task: str, plan: DAGPlan, owner: str = "default", model: str = DEFAULT_MODEL, mode: str | None = None
    ):
        self.run_id = uuid.uuid4().hex[:12]
        self.task = task
        self.owner = owner  # whose run-limit this counts against; becomes the user id with auth
        self.model = model  # the planner's model; also every agent's when mode is None
        self.mode = mode  # cost mode mapping agent tiers to models; None = one model for all agents
        self.status = RunStatus.PENDING
        self.error: str | None = None  # run-level failure (e.g. the planner failed), as opposed to a node's
        self.set_plan(plan)
        self.events: list[dict] = []
        self.created_at = time.time()
        self.on_change: Callable[[RunRecord], None] | None = None
        self.stop_requested = False  # set by a user stop; any other cancellation is a server interruption
        self.planner_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self.estimate: dict | None = None  # cost estimate snapshotted when the plan was approved
        self.deliver = "auto"  # the user's "deliver as" choice: auto / none / pdf / slides / pptx
        self.live: dict[str, LiveFeed] = {}  # running agents' live output; memory only, never persisted

    def totals(self) -> dict[str, float]:
        """Agents plus the planner call that produced the plan."""
        planner = self.planner_usage
        return {
            "total_input_tokens": planner["input_tokens"] + sum(n.input_tokens for n in self.nodes.values()),
            "total_output_tokens": planner["output_tokens"] + sum(n.output_tokens for n in self.nodes.values()),
            "total_cost_usd": planner["cost_usd"] + sum(n.cost_usd for n in self.nodes.values()),
        }

    def set_plan(self, plan: DAGPlan) -> None:
        """Install a plan, with a fresh pending NodeDetail per node."""
        self.plan = plan
        self.nodes: dict[str, NodeDetail] = {
            n.id: NodeDetail(
                node_id=n.id,
                role=n.role,
                goal=n.goal,
                instructions=n.instructions,
                tools=list(n.tools),
                tier=n.tier,
            )
            for n in plan.nodes
        }

    def model_for(self, node: DAGNode) -> str:
        return resolve_model(node, self.mode, self.model)

    def emit(self, event_type: str, node_id: str | None = None, data: dict | None = None) -> None:
        self.events.append(
            {
                "seq": len(self.events),
                "ts": time.time(),
                "type": event_type,
                "node_id": node_id,
                "data": data or {},
            }
        )
        if self.on_change is not None:
            self.on_change(self)

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "task": self.task,
                "owner": self.owner,
                "model": self.model,
                "mode": self.mode,
                "plan": self.plan.model_dump(),
                "status": self.status.value,
                "error": self.error,
                "planner_usage": self.planner_usage,
                "estimate": self.estimate,
                "deliver": self.deliver,
                "nodes": {k: v.model_dump() for k, v in self.nodes.items()},
                "events": self.events,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> RunRecord:
        data = json.loads(raw)
        record = cls(
            data["task"],
            DAGPlan.model_validate(data["plan"]),
            owner=data.get("owner", "default"),
            model=data.get("model", LEGACY_MODEL),
            mode=data.get("mode"),  # absent on runs from before routing: one model for all agents
        )
        record.run_id = data["run_id"]
        record.status = RunStatus(data["status"])
        record.error = data.get("error")
        record.planner_usage = data.get("planner_usage") or record.planner_usage
        record.estimate = data.get("estimate")
        record.deliver = data.get("deliver", "auto")
        record.nodes = {k: NodeDetail.model_validate(v) for k, v in data["nodes"].items()}
        record.events = data["events"]
        record.created_at = data["created_at"]
        return record


class RunStore:
    """In-memory when `path` is None; otherwise backed by a SQLite file."""

    def __init__(self, path: str | None = None) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._db: sqlite3.Connection | None = None
        if path is not None:
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "run_id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at REAL NOT NULL, data TEXT NOT NULL)"
            )
            self._db.commit()
            self._recover_interrupted()

    def create(
        self, task: str, plan: DAGPlan, owner: str = "default", model: str = DEFAULT_MODEL, mode: str | None = None
    ) -> RunRecord:
        record = RunRecord(task, plan, owner=owner, model=model, mode=mode)
        self._runs[record.run_id] = record
        if self._db is not None:
            record.on_change = self._save
            self._save(record)
        return record

    def get(self, run_id: str) -> RunRecord | None:
        record = self._runs.get(run_id)
        if record is None and self._db is not None:
            row = self._db.execute("SELECT data FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is not None:
                record = RunRecord.from_json(row[0])
                record.on_change = self._save
                self._runs[run_id] = record
        return record

    def queued(self) -> list[RunRecord]:
        """Runs waiting for an execution slot, oldest first (restored into the queue on startup)."""
        if self._db is not None:
            rows = self._db.execute(
                "SELECT run_id FROM runs WHERE status = ? ORDER BY created_at", (RunStatus.QUEUED.value,)
            ).fetchall()
            records = [self.get(r[0]) for r in rows]
        else:
            records = sorted(
                (r for r in self._runs.values() if r.status == RunStatus.QUEUED), key=lambda r: r.created_at
            )
        return [r for r in records if r is not None]

    def agent_usage(self) -> list[tuple[list[str], int, int]]:
        """(tools, input_tokens, output_tokens) for every agent that finished successfully."""
        if self._db is not None:
            rows = self._db.execute(
                "SELECT data FROM runs WHERE status IN (?, ?, ?)",
                (RunStatus.SUCCEEDED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value),
            ).fetchall()
            records = [self._runs.get(json.loads(raw)["run_id"]) or RunRecord.from_json(raw) for (raw,) in rows]
        else:
            records = [r for r in self._runs.values() if r.status.terminal]
        return [
            (list(d.tools), d.input_tokens, d.output_tokens)
            for r in records
            for d in r.nodes.values()
            if d.status == NodeStatus.SUCCEEDED and d.input_tokens > 0
        ]

    def list(self, limit: int = 50) -> list[dict]:
        """Newest-first run summaries for the run switcher."""
        if self._db is None:
            records = sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)[:limit]
        else:
            rows = self._db.execute(
                "SELECT run_id, data FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            # Prefer the live in-memory record: the database copy lags until the next event.
            records = [self._runs.get(run_id) or RunRecord.from_json(raw) for run_id, raw in rows]
        return [
            {
                "run_id": r.run_id,
                "task": r.task,
                "status": r.status.value,
                "created_at": r.created_at,
                "model": r.model,
                "mode": r.mode,
                "total_cost_usd": r.totals()["total_cost_usd"],
            }
            for r in records
        ]

    def _save(self, record: RunRecord) -> None:
        assert self._db is not None
        self._db.execute(
            "INSERT OR REPLACE INTO runs (run_id, task, status, created_at, data) VALUES (?, ?, ?, ?, ?)",
            (record.run_id, record.task, record.status.value, record.created_at, record.to_json()),
        )
        self._db.commit()

    def _recover_interrupted(self) -> None:
        """Runs left planning/pending/running by a previous process can never finish: close them out.

        Queued runs are untouched: nothing of theirs was in flight, so they are re-queued on startup.
        """
        assert self._db is not None
        rows = self._db.execute(
            "SELECT data FROM runs WHERE status IN (?, ?, ?)",
            (RunStatus.PLANNING.value, RunStatus.PENDING.value, RunStatus.RUNNING.value),
        ).fetchall()
        for (raw,) in rows:
            record = RunRecord.from_json(raw)
            record.on_change = self._save
            mark_interrupted(record)
