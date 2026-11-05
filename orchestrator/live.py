"""Live agent output: what a running agent is doing, built from pydantic-ai's stream events.

A LiveFeed per running agent holds an ordered transcript of segments (thinking, tool calls,
text) plus a one-line phase. It lives only in memory on the RunRecord: it is never persisted
or added to the event log; the SSE stream sends it as transient diffs (see live_ops), and the
finished agent's thoughts, tool calls and output replace it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterable
from dataclasses import dataclass, field

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
)

from .composio_source import DISCOVERY_TOOLS, MULTI_EXECUTE, expand_tool_call
from .schema import ToolCallRecord

log = logging.getLogger(__name__)

MAX_LIVE_CHARS = 120_000  # per agent; every SSE reconnect resends the whole transcript
MAX_ARGS_CHARS = 300
OUTPUT_TOOL = "final_result"  # pydantic-ai's structured-output tool (a publishing step's document)


@dataclass
class Segment:
    kind: str  # thinking | text | tool
    text: str = ""
    meta: dict = field(default_factory=dict)  # tool: args, status (running/ok/failed), kind
    rev: int = 0  # bumped when anything but appended text changes, so the segment is resent whole


class LiveFeed:
    def __init__(self) -> None:
        self.segments: list[Segment] = []
        self.phase = "starting"
        self.truncated = False
        self._size = 0
        self._parts: dict[int, int] = {}  # part index in the current model response -> segment
        self._calls: dict[str, list[int]] = {}  # tool_call_id -> its segments

    # --- events -----------------------------------------------------------------------------

    def apply(self, event: object) -> None:
        if isinstance(event, PartStartEvent):
            if event.index == 0:
                self._parts = {}  # a new model response
            self._start(event.index, event.part)
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, (TextPartDelta, ThinkingPartDelta)) and delta.content_delta:
                kind = "text" if isinstance(delta, TextPartDelta) else "thinking"
                seg = self._parts.get(event.index)
                if seg is None or self.segments[seg].kind != kind:
                    seg = self._parts[event.index] = self._new(kind)
                self._append(seg, delta.content_delta)
        elif isinstance(event, FunctionToolCallEvent):
            self._tool_called(event.part)
        elif isinstance(event, FunctionToolResultEvent):
            self._tool_returned(event.part)

    def _start(self, index: int, part: object) -> None:
        if isinstance(part, TextPart):
            self.phase = "writing"
            self._parts[index] = self._new("text")
            self._append(self._parts[index], part.content)
        elif isinstance(part, ThinkingPart):
            self.phase = "thinking"
            self._parts[index] = self._new("thinking")
            self._append(self._parts[index], part.content)
        elif isinstance(part, ToolCallPart):
            # Arguments are still streaming; the row appears once the call is complete.
            self.phase = _calling(part.tool_name)

    def _tool_called(self, part: ToolCallPart) -> None:
        if part.tool_name.startswith(OUTPUT_TOOL):
            return
        # Same unwrapping as the finished trace, so live rows match the final tool list.
        for record in expand_tool_call(ToolCallRecord(name=part.tool_name, args=part.args_as_json_str())):
            meta = {"args": _short_args(record.args), "status": "running", "kind": record.kind}
            self._calls.setdefault(part.tool_call_id, []).append(self._new("tool", record.name, meta))
        rows = [self.segments[i] for i in self._calls.get(part.tool_call_id, [])]
        names = [r.text for r in rows if r.meta.get("kind") != "discovery"]
        if names:
            self.phase = f"calling {', '.join(dict.fromkeys(names))}"
        elif rows:
            self.phase = "finding tools"

    def _tool_returned(self, part: object) -> None:
        call_id = getattr(part, "tool_call_id", None)
        status = "failed" if isinstance(part, RetryPromptPart) else "ok"
        for i in self._calls.get(call_id, []):
            seg = self.segments[i]
            seg.meta = {**seg.meta, "status": status}
            seg.rev += 1
        self.phase = "waiting for the model"

    # --- helpers ------------------------------------------------------------------------------

    def _new(self, kind: str, text: str = "", meta: dict | None = None) -> int:
        self.segments.append(Segment(kind=kind, text=text, meta=meta or {}))
        self._size += len(text)
        return len(self.segments) - 1

    def _append(self, seg: int, text: str) -> None:
        if not text or self.truncated:
            return
        room = MAX_LIVE_CHARS - self._size
        if len(text) > room:
            text = text[: max(room, 0)] + "\n… (live view truncated; the full result appears when the agent finishes)"
            self.truncated = True
        self.segments[seg].text += text
        self._size += len(text)


def _calling(tool_name: str) -> str:
    """Phase for a tool call whose arguments are still streaming (Composio's wrappers named by what they do)."""
    if tool_name.startswith(OUTPUT_TOOL):
        return "writing the document"
    if tool_name in DISCOVERY_TOOLS:
        return "finding tools"
    if tool_name == MULTI_EXECUTE:
        return "running tools"
    return f"calling {tool_name}"


def _short_args(args: str | None) -> str:
    if not args:
        return ""
    try:
        args = json.dumps(json.loads(args), ensure_ascii=False)
    except ValueError:
        pass
    return args if len(args) <= MAX_ARGS_CHARS else args[:MAX_ARGS_CHARS] + "…"


def live_ops(feed: LiveFeed, sent: dict[int, tuple[int, int]]) -> list[dict]:
    """What changed since `sent` (segment -> (chars sent, rev)), as ops the browser applies; updates `sent`.

    A new or changed segment goes whole ({i, at: 0, text, kind, meta}); a growing one sends only
    the new text ({i, at, text}). An empty `sent` (a new connection) gets everything.
    """
    ops = []
    for i, seg in enumerate(feed.segments):
        prev = sent.get(i)
        if prev is None or prev[1] != seg.rev:
            ops.append({"i": i, "at": 0, "text": seg.text, "kind": seg.kind, "meta": seg.meta})
        elif len(seg.text) > prev[0]:
            ops.append({"i": i, "at": prev[0], "text": seg.text[prev[0] :]})
        else:
            continue
        sent[i] = (len(seg.text), seg.rev)
    return ops


def stream_handler(feed: LiveFeed):
    """An event_stream_handler for Agent.run that feeds `feed`. Never raises: a live-view bug
    must not fail the agent, so the first error is logged and the feed stops updating."""

    async def handle(ctx, events: AsyncIterable) -> None:
        broken = False
        async for event in events:  # always drain, so the run itself is unaffected
            if broken:
                continue
            try:
                feed.apply(event)
            except Exception:
                broken = True
                log.exception("live feed stopped updating")

    return handle
