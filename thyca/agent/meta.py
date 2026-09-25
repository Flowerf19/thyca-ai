"""Pure message/meta builders for :class:`Observe` + the naming sidecar.

No I/O, no session writes. Both builders share the model/usage/cost field
rules below; each keeps its own key set and insertion order."""
from __future__ import annotations

from typing import TYPE_CHECKING

from thyca.core.protocol import Message, ToolResult
from thyca.llm.pricing import cost_for

from .stage import Stage

if TYPE_CHECKING:
    from thyca.config import Config


def reasoning(stage: Stage) -> str | None:
    value = getattr(getattr(stage, "reply", None), "reasoning", None)
    if isinstance(value, str) and value:
        return value
    return None


def reasoning_details(stage: Stage) -> list[dict] | None:
    value = getattr(getattr(stage, "reply", None), "reasoning_details", None)
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        return [dict(item) for item in value]
    return None


def _clean_model(candidate: object) -> str | None:
    """Stripped model name, or None when blank/missing."""
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return None


def _usage_copy(reply: object) -> dict | None:
    """Copied usage dict of a reply, or None when absent/empty."""
    usage = getattr(reply, "usage", None)
    if isinstance(usage, dict) and usage:
        return dict(usage)
    return None


def _priced_cost(model: str | None, usage: dict | None, pricing: dict | None) -> float | None:
    """USD cost for a model+usage pair, or None when unpriced."""
    if model is None:
        return None
    return cost_for(model, usage, pricing)


def assistant_meta(stage: Stage, *, kind: str = "llm") -> dict:
    meta: dict = {"kind": kind}
    if stage.round:
        meta["round"] = stage.round
    model = _clean_model(
        getattr(stage, "llm_model", None) or getattr(getattr(stage, "reply", None), "model", None)
    )
    if model is not None:
        meta["model"] = model
    latency = getattr(stage, "llm_latency_ms", None)
    if isinstance(latency, int) and latency >= 0:
        meta["latency_ms"] = latency
    usage = _usage_copy(getattr(stage, "reply", None))
    if usage is not None:
        meta["usage"] = usage
    cost = getattr(stage, "llm_cost_usd", None)
    if isinstance(cost, (int, float)):
        meta["cost_usd"] = float(cost)
    finish = getattr(getattr(stage, "reply", None), "finish_reason", None)
    if isinstance(finish, str) and finish:
        meta["finish_reason"] = finish
    return meta


def naming_meta(reply: object, latency_ms: int, cfg: Config) -> dict:
    """Meta for the naming sidecar record: same model/usage/cost rules."""
    usage = _usage_copy(reply)
    model = _clean_model(getattr(reply, "model", None) or cfg.provider.model or "")
    meta: dict = {"kind": "naming", "latency_ms": max(0, latency_ms)}
    if model is not None:
        meta["model"] = model
    if usage is not None:
        meta["usage"] = usage
    price = _priced_cost(model, usage, cfg.effective_pricing() or None)
    if price is not None:
        meta["cost_usd"] = price
    return meta


def tool_message(
    result: ToolResult, latency_ms: int | None = None, round_no: int | None = None
) -> Message:
    meta: dict | None = None
    if result.is_error:
        meta = {"is_error": True}
    if latency_ms is not None:
        if meta is None:
            meta = {}
        meta["latency_ms"] = latency_ms
    if round_no is not None and round_no > 0:
        if meta is None:
            meta = {}
        meta["round"] = round_no
    return Message(
        role="tool",
        content=result.content,
        tool_call_id=result.tool_call_id,
        meta=meta,
    )
