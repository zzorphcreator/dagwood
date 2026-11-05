"""Live agent output: the per-node feed built from pydantic-ai stream events, and its SSE delivery."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
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
    ToolReturnPart,
)

from orchestrator import api as api_module
from orchestrator.executor import execute_run
from orchestrator.live import MAX_LIVE_CHARS, LiveFeed, live_ops
from orchestrator.schema import DAGNode, DAGPlan, NodeStatus, RunStatus
from orchestrator.store import RunStore


def feed_with(*events) -> LiveFeed:
    feed = LiveFeed()
    for e in events:
        feed.apply(e)
    return feed


# --- the feed ---------------------------------------------------------------------------


def test_feed_records_thinking_tool_calls_and_text_in_order():
    feed = feed_with(
        PartStartEvent(index=0, part=ThinkingPart(content="Let me ")),
        PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="compute.")),
        PartStartEvent(index=1, part=ToolCallPart(tool_name="calc", args={"expression": "2+3"}, tool_call_id="c1")),
    )
    assert feed.phase == "calling calc"
    feed.apply(FunctionToolCallEvent(part=ToolCallPart(tool_name="calc", args={"expression": "2+3"}, tool_call_id="c1")))
    feed.apply(FunctionToolResultEvent(part=ToolReturnPart(tool_name="calc", content="5", tool_call_id="c1")))
    assert feed.phase == "waiting for the model"
    feed.apply(PartStartEvent(index=0, part=TextPart(content="The answer")))
    feed.apply(PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" is 5")))

    kinds = [s.kind for s in feed.segments]
    assert kinds == ["thinking", "tool", "text"]
    assert feed.segments[0].text == "Let me compute."
    tool = feed.segments[1]
    assert tool.text == "calc"
    assert "2+3" in tool.meta["args"]
    assert tool.meta["status"] == "ok"
    assert feed.segments[2].text == "The answer is 5"
    assert feed.phase == "writing"


def test_feed_marks_failed_tools_and_hides_the_output_tool():
    feed = feed_with(
        FunctionToolCallEvent(part=ToolCallPart(tool_name="lookup", args={}, tool_call_id="t")),
        FunctionToolResultEvent(part=RetryPromptPart(content="upstream timeout", tool_name="lookup", tool_call_id="t")),
        PartStartEvent(index=0, part=ToolCallPart(tool_name="final_result", args="{", tool_call_id="f")),
    )
    assert feed.segments[0].meta["status"] == "failed"
    assert len(feed.segments) == 1  # structured output is not a tool call the user made
    assert feed.phase == "writing the document"


def test_feed_unwraps_composio_multi_execute_into_its_actions():
    args = {"tools": [{"tool_slug": "COMPOSIO_SEARCH_WEB", "arguments": {"query": "gold price"}}]}
    feed = feed_with(PartStartEvent(index=0, part=ToolCallPart(tool_name="COMPOSIO_SEARCH_TOOLS", args="{")))
    assert feed.phase == "finding tools"
    feed.apply(PartStartEvent(index=0, part=ToolCallPart(tool_name="COMPOSIO_MULTI_EXECUTE_TOOL", args="{")))
    assert feed.phase == "running tools"
    feed.apply(
        FunctionToolCallEvent(
            part=ToolCallPart(tool_name="COMPOSIO_MULTI_EXECUTE_TOOL", args=args, tool_call_id="m")
        )
    )
    assert [s.text for s in feed.segments] == ["COMPOSIO_SEARCH_WEB"]
    assert feed.phase == "calling COMPOSIO_SEARCH_WEB"
    assert "gold price" in feed.segments[0].meta["args"]


def test_feed_stops_growing_past_its_cap():
    feed = LiveFeed()
    feed.apply(PartStartEvent(index=0, part=TextPart(content="")))
    for _ in range(50):
        feed.apply(PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="x" * (MAX_LIVE_CHARS // 10))))
    assert sum(len(s.text) for s in feed.segments) <= MAX_LIVE_CHARS + 200
    assert feed.truncated


# --- diffs sent over SSE -----------------------------------------------------------------


def test_live_ops_send_whole_segments_then_appends_then_resends_on_change():
    feed = feed_with(PartStartEvent(index=0, part=TextPart(content="Hel")))
    sent: dict = {}
    first = live_ops(feed, sent)
    assert first == [{"i": 0, "at": 0, "text": "Hel", "kind": "text", "meta": {}}]
    assert live_ops(feed, sent) == []  # nothing new

    feed.apply(PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="lo")))
    assert live_ops(feed, sent) == [{"i": 0, "at": 3, "text": "lo"}]

    feed.apply(FunctionToolCallEvent(part=ToolCallPart(tool_name="calc", args={}, tool_call_id="c")))
    live_ops(feed, sent)
    feed.apply(FunctionToolResultEvent(part=ToolReturnPart(tool_name="calc", content="1", tool_call_id="c")))
    (op,) = live_ops(feed, sent)  # status changed: the tool row is sent whole again
    assert op["i"] == 1 and op["at"] == 0 and op["meta"]["status"] == "ok"

    assert len(live_ops(feed, {})) == 2  # a new connection gets everything from the start


# --- streaming runs ------------------------------------------------------------------------


def streaming_model(chunks: list[str], seen: list[dict] | None = None, record_ref: list | None = None):
    from pydantic_ai.models.function import FunctionModel

    async def stream_fn(messages, info):
        for chunk in chunks:
            if seen is not None and record_ref:
                record = record_ref[0]
                seen.append({k: [s.text for s in f.segments] for k, f in record.live.items()})
            yield chunk

    return FunctionModel(stream_function=stream_fn)


def test_streaming_run_shows_live_text_counts_usage_and_cleans_up():
    plan = DAGPlan(task="t", nodes=[DAGNode(id="w", goal="write")])
    record = RunStore().create("t", plan)
    seen: list[dict] = []
    model = streaming_model(["Hello ", "streaming ", "world"], seen, [record])
    asyncio.run(execute_run(record, model=model, stream=True))

    detail = record.nodes["w"]
    assert detail.status == NodeStatus.SUCCEEDED
    assert detail.output == "Hello streaming world"
    assert detail.input_tokens > 0 and detail.output_tokens > 0
    assert any("Hello " in "".join(snapshot.get("w", [])) for snapshot in seen)  # visible while running
    assert record.live == {}  # nothing left behind once the agent finishes


def test_a_broken_live_feed_never_fails_the_agent(monkeypatch):
    def boom(self, event):
        raise RuntimeError("feed bug")

    monkeypatch.setattr(LiveFeed, "apply", boom)
    plan = DAGPlan(task="t", nodes=[DAGNode(id="w", goal="write")])
    record = RunStore().create("t", plan)
    asyncio.run(execute_run(record, model=streaming_model(["fine"]), stream=True))
    assert record.nodes["w"].status == NodeStatus.SUCCEEDED
    assert record.nodes["w"].output == "fine"


def test_streaming_budget_limit_still_fails_the_agent(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MAX_INPUT_TOKENS_PER_AGENT", "1")
    plan = DAGPlan(task="t", nodes=[DAGNode(id="w", goal="write")])
    record = RunStore().create("t", plan)
    asyncio.run(execute_run(record, model=streaming_model(["too ", "long"]), stream=True))
    detail = record.nodes["w"]
    assert detail.status == NodeStatus.FAILED
    assert "budget limit reached" in (detail.error or "")
    assert record.live == {}


def test_streaming_failed_agent_is_cleaned_up():
    from pydantic_ai.models.function import FunctionModel

    async def stream_fn(messages, info):
        yield "partial"
        raise RuntimeError("connection dropped")

    plan = DAGPlan(task="t", nodes=[DAGNode(id="w", goal="write")])
    record = RunStore().create("t", plan)
    asyncio.run(execute_run(record, model=FunctionModel(stream_function=stream_fn), stream=True))
    assert record.nodes["w"].status == NodeStatus.FAILED
    assert record.live == {}


def test_streaming_publisher_still_renders_its_file(tmp_path, monkeypatch):
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    monkeypatch.setenv("ORCHESTRATOR_FILES_DIR", str(tmp_path))
    spec = {"title": "Report", "sections": [{"heading": "Intro", "body": "hello"}]}

    async def stream_fn(messages, info):
        name = info.output_tools[0].name if info.output_tools else "final_result"
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(spec), tool_call_id="out")}

    plan = DAGPlan(task="t", nodes=[DAGNode(id="publish", goal="publish", deliverable="pdf")])
    record = RunStore().create("t", plan)
    asyncio.run(execute_run(record, model=FunctionModel(stream_function=stream_fn), stream=True))
    detail = record.nodes["publish"]
    assert detail.status == NodeStatus.SUCCEEDED, detail.error
    assert detail.files and detail.files[0].name.endswith(".pdf")
    assert "# Report" in detail.output


def test_live_feed_is_not_persisted():
    plan = DAGPlan(task="t", nodes=[DAGNode(id="w", goal="write")])
    record = RunStore().create("t", plan)
    record.live["w"] = feed_with(PartStartEvent(index=0, part=TextPart(content="secret draft")))
    assert "secret draft" not in record.to_json()


# --- SSE -------------------------------------------------------------------------------------


def test_stream_sends_live_events_for_running_agents(monkeypatch):
    monkeypatch.setattr(api_module, "STREAM_MAX_SECONDS", 0.5)
    store = RunStore()
    monkeypatch.setattr(api_module.app.state, "store", store)
    plan = DAGPlan(task="t", nodes=[DAGNode(id="w", goal="write")])
    record = store.create("t", plan)
    record.status = RunStatus.RUNNING
    record.nodes["w"].status = NodeStatus.RUNNING
    record.live["w"] = feed_with(PartStartEvent(index=0, part=TextPart(content="drafting…")))

    with TestClient(api_module.app) as client:
        body = client.get(f"/runs/{record.run_id}/stream").text

    blocks = [b for b in body.split("\n\n") if b.startswith("event: live")]
    assert blocks, body
    assert not any(line.startswith("id:") for line in blocks[0].splitlines())  # transient: never resumed by id
    payload = json.loads(blocks[0].split("data: ", 1)[1])
    assert payload["node_id"] == "w"
    assert payload["phase"] == "writing"
    assert payload["ops"][0]["text"] == "drafting…"
