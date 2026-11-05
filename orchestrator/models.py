"""Models offered for runs. All are served by DeepInfra's OpenAI-compatible API (one key).

Prices are USD per 1M tokens from DeepInfra's model list, checked 2026-10-09; providers
change them, so costs shown are estimates. Every model here was checked live for tool
calling, visible reasoning and structured planner output before being added.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    input_per_1m: float
    output_per_1m: float
    note: str
    premium: bool = False
    input_cap: int | None = None  # per-agent input-token cap below the global one (pricey models)


MODELS: list[ModelInfo] = [
    ModelInfo("deepseek-ai/DeepSeek-V4-Flash", "DeepSeek V4 Flash", 0.09, 0.18, "Cheap general-purpose default."),
    ModelInfo(
        "deepseek-ai/DeepSeek-V4-Flash-0731",
        "DeepSeek V4 Flash (0731 snapshot)",
        0.06,
        0.18,
        "Pinned snapshot; cheapest input.",
    ),
    ModelInfo("Qwen/Qwen3.8-Flash", "Qwen 3.8 Flash", 0.113, 0.382, "Cheap; 1M-token context."),
    ModelInfo("deepseek-ai/DeepSeek-V4.1-Flash", "DeepSeek V4.1 Flash", 0.20, 0.60, "Newer DeepSeek; previous default."),
    ModelInfo(
        "moonshotai/Kimi-K3",
        "Kimi K3",
        2.85,
        14.25,
        "Premium: about 30x the default's input price and 80x its output price.",
        premium=True,
        input_cap=100_000,  # ~$0.29 of input per agent at most
    ),
]

_BY_ID = {m.id: m for m in MODELS}


@dataclass(frozen=True)
class ComparisonModel:
    """Another provider's model, priced for comparison only: there is no client or key for it,
    so get_info() doesn't know it and every endpoint that picks a model rejects it."""

    id: str
    provider: str
    label: str
    input_per_1m: float
    output_per_1m: float
    note: str


# Standard (uncached, short-prompt) rates, cheap -> flagship per provider, checked 2026-10-12:
#   Anthropic: claude-api skill model table (cached 2026-10-06)
#   OpenAI:    https://developers.openai.com/api/docs/pricing
#   xAI:       https://docs.x.ai/docs/models
#   Google:    https://ai.google.dev/gemini-api/docs/pricing (updated 2026-10-09)
# Base rates only: a single agent request stays well under the long-prompt thresholds in the notes.
COMPARISON_PRICES_CHECKED = "2026-10-12"
COMPARISON_MODELS: list[ComparisonModel] = [
    ComparisonModel("claude-haiku-5-5", "Anthropic", "Claude Haiku 5.5", 0.10, 0.50, "Prompts over 100K: $0.50/$2.50."),
    ComparisonModel("claude-sonnet-5-5", "Anthropic", "Claude Sonnet 5.5", 2.00, 10.00, "Mid tier."),
    ComparisonModel("claude-opus-5-5", "Anthropic", "Claude Opus 5.5", 4.00, 20.00, "Flagship."),
    ComparisonModel("gpt-6-luna", "OpenAI", "GPT-6 Luna", 0.10, 0.50, "Prompts over 272K: $0.20/$0.75."),
    ComparisonModel("gpt-6.1-sol", "OpenAI", "GPT-6.1 Sol", 2.00, 10.00, "Prompts over 272K: $4/$15."),
    ComparisonModel("gpt-6-astra", "OpenAI", "GPT-6 Astra", 10.00, 50.00, "Flagship. Prompts over 272K: $20/$75."),
    ComparisonModel("grok-4.3", "xAI", "Grok 4.3", 1.25, 2.50, "Prompts of 200K+: $2.50/$5."),
    ComparisonModel("grok-4.7", "xAI", "Grok 4.7", 2.00, 6.00, "Flagship. Prompts of 200K+: $4/$12."),
    ComparisonModel(
        "gemini-3.1-flash-lite", "Google", "Gemini 3.1 Flash-Lite", 0.25, 1.50, "Cheapest current Gemini."
    ),
    ComparisonModel(
        "gemini-3.8-flash", "Google", "Gemini 3.8 Flash", 0.75, 3.75, "Introductory; $1.50/$7.50 from 2027-01-01."
    ),
    ComparisonModel(
        "gemini-3.1-pro-preview", "Google", "Gemini 3.1 Pro (Preview)", 2.00, 12.00, "Prompts over 200K: $4/$18."
    ),
]

LEGACY_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"  # what runs recorded before model choice used


def get_info(model_id: str) -> ModelInfo | None:
    return _BY_ID.get(model_id)


def default_model() -> str:
    """ORCHESTRATOR_DEFAULT_MODEL if it names a listed model, else DeepSeek V4 Flash."""
    chosen = os.environ.get("ORCHESTRATOR_DEFAULT_MODEL", "")
    return chosen if chosen in _BY_ID else "deepseek-ai/DeepSeek-V4-Flash"


DEFAULT_MODEL = default_model()


def as_dicts() -> list[dict]:
    return [{**asdict(m), "default": m.id == DEFAULT_MODEL} for m in MODELS]


# --- routing: the planner gives each agent a difficulty tier; the run's cost mode maps tiers to models.
# Search agents re-read every tool result on each step, so their cost grows with input tokens and
# they belong on light/standard; premium models pay off only on short, hard reasoning steps.

TIERS = ("light", "standard", "heavy")
TIER_EFFORT = {"light": "low", "standard": "low", "heavy": "medium"}  # reasoning effort per tier

MODES: dict[str, dict] = {
    "economy": {
        "label": "Economy",
        "note": "Every agent on the cheapest model.",
        "planner": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "tiers": dict.fromkeys(TIERS, "deepseek-ai/DeepSeek-V4-Flash-0731"),
    },
    "balanced": {
        "label": "Balanced",
        "note": "Harder steps get a stronger model; no premium models.",
        "planner": "deepseek-ai/DeepSeek-V4-Flash",
        "tiers": {
            "light": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "standard": "deepseek-ai/DeepSeek-V4-Flash",
            "heavy": "deepseek-ai/DeepSeek-V4.1-Flash",
        },
    },
    "quality": {
        "label": "Quality",
        "note": "Heavy steps use the premium Kimi K3 (capped at 100k input tokens per agent).",
        "planner": "deepseek-ai/DeepSeek-V4.1-Flash",
        "tiers": {
            "light": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "standard": "deepseek-ai/DeepSeek-V4-Flash",
            "heavy": "moonshotai/Kimi-K3",
        },
    },
}
DEFAULT_MODE = "balanced"


def resolve_model(node, mode: str | None, run_model: str) -> str:
    """The model an agent runs on: its own override, else the run-wide model (mode None), else its tier's."""
    if node.model:
        return node.model
    if mode is None:
        return run_model
    return MODES[mode]["tiers"][node.tier]


def modes_as_dicts() -> list[dict]:
    return [{"id": k, **v, "tiers": dict(v["tiers"]), "default": k == DEFAULT_MODE} for k, v in MODES.items()]
