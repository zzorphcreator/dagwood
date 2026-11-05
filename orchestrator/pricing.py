"""Token pricing -> USD estimates, from the model list in models.py.

Unknown models cost 0.0 (so a typo shows as free rather than crashing). Costs are
estimates: providers change prices and peak/off-peak rates vary.
"""

from __future__ import annotations

from .models import MODELS

# model id -> (input USD per 1M tokens, output USD per 1M tokens)
MODEL_PRICES: dict[str, tuple[float, float]] = {m.id: (m.input_per_1m, m.output_per_1m) for m in MODELS}


def get_rate(model_id: str) -> tuple[float, float]:
    return MODEL_PRICES.get(model_id, (0.0, 0.0))


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    inp, outp = get_rate(model_id)
    return (input_tokens * inp + output_tokens * outp) / 1_000_000
