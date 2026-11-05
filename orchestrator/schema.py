"""DAG schema: node/plan models, run state, and DAG validation."""

from __future__ import annotations

from collections import deque
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"  # was running when the user stopped the run


class RunStatus(str, Enum):
    PLANNING = "planning"  # supervisor is building the plan
    AWAITING_APPROVAL = "awaiting_approval"  # plan built, waiting for a human to approve or reject
    QUEUED = "queued"  # approved, waiting for a free execution slot (per-user run limit)
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"  # stopped by the user

    @property
    def terminal(self) -> bool:
        return self in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.REJECTED, RunStatus.CANCELLED)


# File formats a publishing step can produce; "slides" is a self-contained HTML deck.
Deliverable = Literal["pdf", "slides", "pptx"]


class DAGNode(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    role: str = "worker"
    goal: str
    instructions: str = ""
    tools: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    tier: Literal["light", "standard", "heavy"] = "standard"  # difficulty; the run's mode maps it to a model
    model: str | None = None  # reviewer's per-agent override; the planner never sets it
    deliverable: Deliverable | None = None  # a publishing step: turns its inputs into this file format


class DAGPlan(BaseModel):
    task: str
    nodes: list[DAGNode]

    def order(self) -> list[str]:
        """Topological order of node ids.

        Raises ValueError on duplicate ids, unknown refs, or cycles.
        """
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate node ids")
        known = set(ids)
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in known:
                    raise ValueError(f"node {node.id!r} depends on unknown node {dep!r}")
        indegree = {n.id: len(n.depends_on) for n in self.nodes}
        children: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                children[dep].append(node.id)
        queue: deque[str] = deque(i for i, d in indegree.items() if d == 0)
        result: list[str] = []
        while queue:
            current = queue.popleft()
            result.append(current)
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(result) != len(self.nodes):
            raise ValueError("cycle detected in DAG")
        return result

    def edges(self) -> list[dict[str, str]]:
        return [{"from": dep, "to": n.id} for n in self.nodes for dep in n.depends_on]


class ToolCallRecord(BaseModel):
    """One tool invocation by a worker: name, JSON args, and the returned value."""

    name: str
    args: str = ""
    result: str | None = None
    ok: bool | None = None  # None while no result has come back
    kind: str = "action"  # "action" does work; "discovery" only looks up which tools exist
    via: str | None = None  # wrapper tool this action was executed through, if any
    empty: bool = False  # succeeded but found nothing (e.g. a search with no results)
    note: str | None = None  # the tool's own explanation, shown alongside the result


class InputRecord(BaseModel):
    """What one upstream node passed to this node, exactly as it appeared in the prompt."""

    from_node: str
    content: str
    original_chars: int  # > len(content) when the output was clipped


class FileRecord(BaseModel):
    """A file a publishing step produced, stored under the run's files directory."""

    name: str
    format: Deliverable
    media_type: str
    size: int


class NodeDetail(BaseModel):
    """Full execution record for one node; shown in the UI detail panel."""

    node_id: str
    role: str = "worker"
    goal: str = ""
    status: NodeStatus = NodeStatus.PENDING
    input: str = ""
    output: str = ""
    instructions: str = ""
    tools: list[str] = Field(default_factory=list)
    tier: str = "standard"
    model: str | None = None  # the model this agent runs (or ran) on
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    thoughts: list[str] = Field(default_factory=list)
    inputs: list[InputRecord] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    logs: list[str] = Field(default_factory=list)
    files: list[FileRecord] = Field(default_factory=list)
    error: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
