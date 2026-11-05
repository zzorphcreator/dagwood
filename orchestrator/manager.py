"""Run lifecycle: background tasks, a per-user limit on executing runs, and stopping.

Every planning or executing run is an asyncio task held here, so it can be cancelled
(hard stop). At most `limit` runs per owner execute at once; further approved runs wait
in a FIFO queue (status QUEUED) and start as slots free up. Planning doesn't count
against the limit: it is one short planner call.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from collections.abc import Awaitable, Callable

from .executor import close_cancelled
from .schema import NodeStatus, RunStatus
from .store import RunRecord

Launch = Callable[[RunRecord], Awaitable[object]]


def max_runs_per_user() -> int:
    """ORCHESTRATOR_MAX_RUNS_PER_USER, default 2."""
    try:
        return max(1, int(os.environ.get("ORCHESTRATOR_MAX_RUNS_PER_USER", "2")))
    except ValueError:
        return 2


class RunManager:
    def __init__(self, limit: int | None = None) -> None:
        self.limit = limit or max_runs_per_user()
        self._tasks: dict[str, asyncio.Task] = {}  # run_id -> planning or executing task
        self._executing: dict[str, set[str]] = {}  # owner -> run_ids holding a slot
        self._queue: dict[str, deque[tuple[RunRecord, Launch]]] = {}  # owner -> waiting runs

    # --- queries ----------------------------------------------------------------------------

    def active(self, owner: str) -> int:
        return len(self._executing.get(owner, ()))

    def position(self, run_id: str) -> int | None:
        """1-based place in its owner's queue, or None if not queued."""
        for queue in self._queue.values():
            for i, (record, _) in enumerate(queue, start=1):
                if record.run_id == run_id:
                    return i
        return None

    # --- starting work ----------------------------------------------------------------------

    def plan(self, record: RunRecord, coro: Awaitable[object]) -> None:
        """Track a planning coroutine so it can be stopped."""
        self._track(record, asyncio.ensure_future(coro), holds_slot=False)

    def submit(self, record: RunRecord, launch: Launch) -> None:
        """Execute now if the owner has a free slot, otherwise queue."""
        if self.active(record.owner) < self.limit:
            self._start(record, launch)
            return
        queue = self._queue.setdefault(record.owner, deque())
        queue.append((record, launch))
        record.status = RunStatus.QUEUED
        record.emit("queued", data={"position": len(queue), "limit": self.limit})

    def _start(self, record: RunRecord, launch: Launch) -> None:
        self._executing.setdefault(record.owner, set()).add(record.run_id)
        self._track(record, asyncio.ensure_future(launch(record)), holds_slot=True)

    def _track(self, record: RunRecord, task: asyncio.Task, holds_slot: bool) -> None:
        self._tasks[record.run_id] = task
        task.add_done_callback(lambda t, r=record: self._finished(r, t, holds_slot))

    def _finished(self, record: RunRecord, task: asyncio.Task, holds_slot: bool) -> None:
        # A planning task may hand its run straight to execution, so only drop our own handle.
        if self._tasks.get(record.run_id) is task:
            del self._tasks[record.run_id]
        if holds_slot:
            self._executing.get(record.owner, set()).discard(record.run_id)
            self._dispatch(record.owner)

    def _dispatch(self, owner: str) -> None:
        queue = self._queue.get(owner)
        while queue and self.active(owner) < self.limit:
            record, launch = queue.popleft()
            record.emit("dequeued")
            self._start(record, launch)

    # --- stopping ---------------------------------------------------------------------------

    async def cancel(self, record: RunRecord) -> None:
        """Hard stop. Waits (briefly) for the task to record its cancelled state."""
        queue = self._queue.get(record.owner)
        if queue:
            for item in list(queue):
                if item[0].run_id == record.run_id:
                    queue.remove(item)
                    close_cancelled(record)
                    return
        record.stop_requested = True
        task = self._tasks.get(record.run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=10)
            if not record.status.terminal:  # the task couldn't record it itself
                close_cancelled(record)
            return
        if record.status == RunStatus.AWAITING_APPROVAL:
            for detail in record.nodes.values():
                if detail.status == NodeStatus.PENDING:
                    detail.status = NodeStatus.SKIPPED
            close_cancelled(record)
            return
        if not record.status.terminal:  # e.g. pending with no task (shouldn't happen): don't strand it
            close_cancelled(record)
