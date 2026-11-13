"""Cost forecast for a plan, learned from this server's own finished agents.

Agents are grouped by kind: "search" (has an external toolkit), "tools" (built-ins only)
and "plain". Each kind's typical cost is the median of past agents of that kind and its
high end the 90th percentile, both clamped to the per-agent input-token cap, since an
agent can no longer exceed it. With fewer than MIN_SAMPLES past agents a kind falls back
to DEFAULTS. Tool-using agents vary a lot (each tool step re-reads earlier results), so the
forecast is a range, not a promise.
"""

from __future__ import annotations

from .catalog import BUILTIN, split_id
from .models import (
    COMPARISON_MODELS,
    COMPARISON_PRICES_CHECKED,
    DEFAULT_MODEL,
    MODELS,
    MODES,
    PRICES_CHECKED,
    get_info,
    resolve_model,
)
from .pricing import estimate_cost
from .schema import DAGNode, DAGPlan
from .store import RunStore

MIN_SAMPLES = 5
KINDS = ("search", "tools", "plain")

# kind -> (typical input, typical output, high input, high output); None = the input cap
DEFAULTS: dict[str, tuple[int, int, int | None, int]] = {
    "plain": (3_000, 1_500, 8_000, 4_000),
    "tools": (4_500, 2_300, 16_000, 4_500),
    "search": (150_000, 6_000, None, 20_000),
}


def kind_of(tools: list[str]) -> str:
    if any(split_id(t)[0] != BUILTIN for t in tools):
        return "search"
    return "tools" if tools else "plain"


def _percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def _profiles(store: RunStore, input_cap: int) -> tuple[dict[str, tuple[int, int, int, int]], dict[str, int]]:
    samples: dict[str, list[tuple[int, int]]] = {k: [] for k in KINDS}
    for tools, inp, out in store.agent_usage():
        samples[kind_of(tools)].append((inp, out))
    profiles = {}
    for kind in KINDS:
        rows = samples[kind]
        if len(rows) >= MIN_SAMPLES:
            ins, outs = [r[0] for r in rows], [r[1] for r in rows]
            profile = (_median(ins), _median(outs), _percentile(ins, 0.9), _percentile(outs, 0.9))
        else:
            t_in, t_out, h_in, h_out = DEFAULTS[kind]
            profile = (t_in, t_out, h_in if h_in is not None else input_cap, h_out)
        t_in, t_out, h_in, h_out = profile
        profiles[kind] = (min(t_in, input_cap), t_out, min(max(h_in, t_in), input_cap), max(h_out, t_out))
    return profiles, {k: len(v) for k, v in samples.items()}


def _node_estimate(node: DAGNode, profile: tuple[int, int, int, int], model_id: str) -> dict:
    t_in, t_out, h_in, h_out = profile
    info = get_info(model_id)
    if info and info.input_cap:  # this model stops its agents sooner
        t_in, h_in = min(t_in, info.input_cap), min(h_in, info.input_cap)
    return {
        "node_id": node.id,
        "model": model_id,
        "tier": node.tier,
        "kind": kind_of(node.tools),
        "typical_input_tokens": t_in,
        "typical_output_tokens": t_out,
        "high_input_tokens": h_in,
        "high_output_tokens": h_out,
        "typical_usd": estimate_cost(model_id, t_in, t_out),
        "high_usd": estimate_cost(model_id, h_in, h_out),
    }


def estimate_plan(
    plan: DAGPlan, store: RunStore, input_cap: int, model_id: str = DEFAULT_MODEL, models: dict[str, str] | None = None
) -> dict:
    """Token profiles come from past agents on any model; dollars use each agent's model price.

    `models` maps node id -> model id; nodes missing from it are priced at `model_id`.
    """
    profiles, counts = _profiles(store, input_cap)
    models = models or {}
    nodes = [_node_estimate(n, profiles[kind_of(n.tools)], models.get(n.id, model_id)) for n in plan.nodes]
    return {
        "model": model_id,
        "nodes": nodes,
        "typical_usd": sum(n["typical_usd"] for n in nodes),
        "high_usd": sum(n["high_usd"] for n in nodes),
        "based_on": counts,
        "min_samples": MIN_SAMPLES,
        "input_cap_per_agent": input_cap,
        "options": _routing_options(plan, profiles),
        "comparisons": _comparisons(plan, profiles),
        "prices_checked": PRICES_CHECKED,
        "comparison_prices_checked": COMPARISON_PRICES_CHECKED,
    }


def _comparisons(plan: DAGPlan, profiles: dict[str, tuple[int, int, int, int]]) -> list[dict]:
    """What the whole plan would cost on other providers' models (pins ignored), at the token
    counts of this server's past agents. Their tokenizers and reasoning lengths differ, so rough."""
    rows = []
    for m in COMPARISON_MODELS:
        typical = high = 0.0
        for n in plan.nodes:
            t_in, t_out, h_in, h_out = profiles[kind_of(n.tools)]
            typical += (t_in * m.input_per_1m + t_out * m.output_per_1m) / 1_000_000
            high += (h_in * m.input_per_1m + h_out * m.output_per_1m) / 1_000_000
        rows.append(
            {
                "id": m.id,
                "provider": m.provider,
                "label": m.label,
                "note": m.note,
                "typical_usd": typical,
                "high_usd": high,
            }
        )
    return rows


def _routing_options(plan: DAGPlan, profiles: dict[str, tuple[int, int, int, int]]) -> dict[str, dict]:
    """The plan priced under every cost mode and every single model, keyed "mode:<id>" / "model:<id>".

    Agents a reviewer pinned to a model stay on it under every option, as they would at run time.
    """
    routings = [(f"mode:{m}", m, DEFAULT_MODEL) for m in MODES] + [(f"model:{m.id}", None, m.id) for m in MODELS]
    options = {}
    for key, mode, run_model in routings:
        nodes = [_node_estimate(n, profiles[kind_of(n.tools)], resolve_model(n, mode, run_model)) for n in plan.nodes]
        options[key] = {"typical_usd": sum(n["typical_usd"] for n in nodes), "high_usd": sum(n["high_usd"] for n in nodes)}
    return options
