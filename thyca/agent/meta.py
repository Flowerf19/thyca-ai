"""Pure message/meta builders for :class:`Observe`. No I/O, no session writes."""
from __future__ import annotations

from thyca.core.protocol import Message, ToolResult

from .stage import Stage


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


def assistant_meta(stage: Stage, *, kind: str = "llm") -> dict | None:
    meta: dict = {"kind": kind}
    if stage.round:
        meta["round"] = stage.round
    model = getattr(stage, "llm_model", None) or getattr(getattr(stage, "reply", None), "model", None)
    if isinstance(model, str) and model.strip():
        meta["model"] = model.strip()
    latency = getattr(stage, "llm_latency_ms", None)
    if isinstance(latency, int) and latency >= 0:
        meta["latency_ms"] = latency
    usage = getattr(getattr(stage, "reply", None), "usage", None)
    if isinstance(usage, dict) and usage:
        meta["usage"] = dict(usage)
    cost = getattr(stage, "llm_cost_usd", None)
    if isinstance(cost, (int, float)):
        meta["cost_usd"] = float(cost)
    finish = getattr(getattr(stage, "reply", None), "finish_reason", None)
    if isinstance(finish, str) and finish:
        meta["finish_reason"] = finish
    # drop kind-only meta when no other field — still keep kind for trace grouping
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
