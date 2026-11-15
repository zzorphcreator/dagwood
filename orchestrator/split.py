"""What splitting a task into agents bought one finished run, from figures the run recorded.

Three comparisons, shown under the run header:

- time: how long the agents worked in total against how long the run took (parallel steps overlap);
- context: the most text any one agent was handed by other agents against all the text agents wrote;
- cost: what the run cost against the same token counts at other providers' flagship prices.

The first two are measured. The third is a guess and must be worded as one wherever it is shown:
a different model would write and re-read a different number of tokens, and a single agent doing
the whole task would not make the same calls. It gives a rough sense of scale, nothing more.
"""

from __future__ import annotations

from .models import COMPARISON_MODELS, COMPARISON_PRICES_CHECKED, MODELS
from .schema import DAGNode, DAGPlan
from .store import RunRecord

BASELINE_NODE = "solo"


def split_summary(record: RunRecord) -> dict | None:
    """None until the run has ended and at least two agents ran: one agent is not a split."""
    if not record.status.terminal:
        return None
    ran = [n for n in record.nodes.values() if n.started_at is not None and n.ended_at is not None]
    if len(ran) < 2:
        return None

    totals = record.totals()
    tokens_in, tokens_out = totals["total_input_tokens"], totals["total_output_tokens"]
    flagships = [m for m in COMPARISON_MODELS if m.flagship]
    prices = [(tokens_in * m.input_per_1m + tokens_out * m.output_per_1m) / 1_000_000 for m in flagships]

    return {
        "agents": len(ran),
        "time": {
            "agent_seconds": sum(n.ended_at - n.started_at for n in ran),
            "wall_seconds": max(n.ended_at for n in ran) - min(n.started_at for n in ran),
        },
        "context": {
            "largest_handoff_chars": max(sum(len(i.content) for i in n.inputs) for n in ran),
            "output_chars": sum(len(n.output) for n in ran),
        },
        # No tokens recorded (a stubbed run) means nothing to price.
        "cost": {
            "actual_usd": totals["total_cost_usd"],
            "frontier_low_usd": min(prices),
            "frontier_high_usd": max(prices),
            "frontier_models": [m.label for m in flagships],
            "prices_checked": COMPARISON_PRICES_CHECKED,
        }
        if prices and tokens_in + tokens_out > 0
        else None,
    }


def baseline_model() -> str:
    """The strongest model on the list stands in for "one big model": the first premium one,
    else the most expensive."""
    premium = [m for m in MODELS if m.premium]
    return (premium[0] if premium else max(MODELS, key=lambda m: m.output_per_1m)).id


def baseline_plan(record: RunRecord) -> DAGPlan:
    """The same task as one agent holding every tool the split plan handed out.

    It runs under the same per-agent limits as any other agent (tool calls, input tokens), which
    is the honest setup for this harness but means a search-heavy task can stop early.
    """
    tools = list(dict.fromkeys(t for n in record.plan.nodes for t in n.tools))
    solo = DAGNode(
        id=BASELINE_NODE,
        role="generalist",
        goal="Do the whole task yourself, start to finish, and give the complete final answer.",
        tools=tools,
        tier="heavy",
    )
    return DAGPlan(task=record.task, nodes=[solo])


def run_brief(record: RunRecord) -> dict:
    """One run's headline figures, for showing a split run and its baseline side by side."""
    ran = [n for n in record.nodes.values() if n.started_at is not None and n.ended_at is not None]
    return {
        "run_id": record.run_id,
        "status": record.status.value,
        "agents": len(record.plan.nodes),
        "model": record.model,
        "mode": record.mode,
        "total_cost_usd": record.totals()["total_cost_usd"],
        "seconds": max(n.ended_at for n in ran) - min(n.started_at for n in ran) if ran else None,
        "error": next((n.error for n in record.nodes.values() if n.error), record.error),
    }
