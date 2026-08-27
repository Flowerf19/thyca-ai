"""Pricing per 1M tokens. Config overlays builtin ``DEFAULT_PRICES``."""
from __future__ import annotations

import re

from thyca.config import PricingCfg

# USD / 1M tokens. Snapshot 2026-08; override via Config.pricing.
DEFAULT_PRICES: dict[str, PricingCfg] = {
    "gpt-4o-mini": PricingCfg(input=0.15, cache=0.075, output=0.60),
    "gpt-5.6-luna": PricingCfg(input=0.20, cache=0.02, output=1.20),
    "muse-spark-1.2-contributor": PricingCfg(input=0.10, cache=0.002, output=0.20),
}

_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def resolve_model(raw: str) -> str:
    return raw.strip()


def _candidates(model: str) -> list[str]:
    names: list[str] = []

    def add(name: str) -> None:
        if name and name not in names:
            names.append(name)

    add(model)
    if "/" in model:
        add(model.rsplit("/", 1)[-1])
    for name in list(names):
        stripped = _DATE_SUFFIX.sub("", name)
        add(stripped)
        if "/" in stripped:
            add(stripped.rsplit("/", 1)[-1])
    return names


def _lookup(model: str, pricing_cfg: dict[str, PricingCfg] | None) -> PricingCfg | None:
    keys = _candidates(model)
    if pricing_cfg:
        for name in keys:
            if name in pricing_cfg:
                return pricing_cfg[name]
    for name in keys:
        if name in DEFAULT_PRICES:
            return DEFAULT_PRICES[name]
    return None


def cost_for(
    model: str | None, usage: dict | None, pricing_cfg: dict[str, PricingCfg] | None = None
) -> float | None:
    """Return USD cost rounded to 6 decimals, or None when model/usage unknown."""
    if not model or not isinstance(usage, dict):
        return None
    pricing = _lookup(resolve_model(model), pricing_cfg)
    if pricing is None:
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if isinstance(prompt, bool) or isinstance(completion, bool):
        return None
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    cached = usage.get("cached_tokens", 0)
    if isinstance(cached, bool) or not isinstance(cached, int) or cached < 0:
        cached = 0
    if cached > prompt:
        cached = prompt
    uncached = prompt - cached
    cost = (uncached * pricing.input + cached * pricing.cache + completion * pricing.output) / 1_000_000
    return round(cost, 6)
