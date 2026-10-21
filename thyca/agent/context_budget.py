"""Bot-visible context budget for proactive checkpointing.

Pure helpers shared by the agent loop: classify the calibrated
context-usage estimate into a checkpoint state, and detect a fresh
checkpoint recorded through the existing memory tools. A checkpoint is
a deliberate ``kind="checkpoint"`` memory entry tied to a task; no new
store is introduced. Its freshness is band-based: a checkpoint written
in a lower context band no longer suppresses guidance once usage has
escalated into a higher band.
"""
from __future__ import annotations

import math

from thyca.core.context import BACKSTOP_RATIO
from thyca.core.protocol import Message

PREPARE_RATIO = 0.8
MANDATORY_RATIO = 0.9
_BANDS = ("roomy", "prepare", "mandatory", "backstop")


def _band_index(ratio: float) -> int:
    if ratio >= BACKSTOP_RATIO:
        return 3
    if ratio >= MANDATORY_RATIO:
        return 2
    if ratio >= PREPARE_RATIO:
        return 1
    return 0


def _safe_calibration(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    factor = float(value)
    if not math.isfinite(factor) or factor <= 0:
        return 1.0
    return min(2.5, max(0.5, factor))


def _write_band(message: Message, context_tokens: int, calibration: float) -> int:
    """Context band of the assistant call that issued a checkpoint write.

    Uses the provider-reported prompt size when the persisted meta has
    it, else the raw pre-call estimate under the current calibration.
    Unknown reads as the lowest band, so it goes stale on escalation.
    """
    meta = message.meta or {}
    usage = meta.get("usage")
    actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    if isinstance(actual, int) and not isinstance(actual, bool) and actual > 0:
        return _band_index(actual / context_tokens)
    estimated = meta.get("estimate_tokens")
    if isinstance(estimated, int) and not isinstance(estimated, bool) and estimated > 0:
        return _band_index(estimated * _safe_calibration(calibration) / context_tokens)
    return 0


def checkpoint_state(
    messages: list[Message],
    *,
    context_tokens: int,
    current_ratio: float,
    calibration: float = 1.0,
) -> str:
    """``recorded`` / ``stale`` / ``none`` for the post-compaction segment.

    Evidence is an explicit checkpoint write in the persisted
    transcript: a successful ``memory_remember`` with
    ``kind="checkpoint"`` and a task link (`task` or `new_task`).
    Ordinary memory writes, habit observations and updates never count.
    The latest qualifying write is fresh only while usage stays in (or
    below) the band it was written in.
    """
    if context_tokens <= 0:
        return "none"
    start = 0
    for index, message in enumerate(messages):
        if message.role == "system" and (message.content or "").startswith(
            "[compaction: "
        ):
            start = index + 1
    calls: dict[str, int] = {}
    for message in messages[start:]:
        if message.role != "assistant" or not message.tool_calls:
            continue
        for call in message.tool_calls:
            args = call.arguments if isinstance(call.arguments, dict) else {}
            task_linked = (
                isinstance(args.get("task"), str) and bool(args["task"].strip())
            ) or args.get("new_task") is True
            if (
                call.name == "memory_remember"
                and args.get("kind") == "checkpoint"
                and task_linked
            ):
                calls[call.id] = _write_band(message, context_tokens, calibration)
    latest_band: int | None = None
    for message in messages[start:]:
        if message.role != "tool":
            continue
        band = calls.get(message.tool_call_id or "")
        if band is None or (message.meta or {}).get("is_error"):
            continue
        latest_band = band
    if latest_band is None:
        return "none"
    return "recorded" if latest_band >= _band_index(current_ratio) else "stale"


def format_context_budget(
    usage: dict,
    *,
    checkpoint: str,
    can_read: bool = True,
    can_write: bool = True,
) -> str:
    """One compact signal line plus the guidance for its state/capability."""
    context_tokens = int(usage.get("context_tokens") or 0)
    estimated = int(usage.get("estimated_tokens") or 0)
    ratio = float(usage.get("ratio") or 0.0)
    remaining = max(0, context_tokens - estimated)
    percent = round(ratio * 100)
    state = _BANDS[_band_index(ratio)]
    if checkpoint not in ("recorded", "stale"):
        checkpoint = "none"
    head = (
        f"used={percent}% remaining≈{remaining:,} tokens "
        f"state={state} checkpoint={checkpoint}"
    )
    if checkpoint == "recorded":
        guidance = (
            "A checkpoint is recorded in memory for this segment; update it "
            "only when the goal, decisions or next step change."
        )
    elif not can_write:
        guidance = (
            "Memory write tools are unavailable for this bot; do not attempt "
            "an in-turn checkpoint. The system summary fallback will "
            "preserve context at compaction."
        )
    elif state in ("prepare", "mandatory", "backstop"):
        urgency = "now" if state != "prepare" else "soon"
        action = "Refresh the checkpoint" if checkpoint == "stale" else "Checkpoint"
        lookup = (
            "check <memory_tasks> and search memory first, reuse existing "
            "task/entry ids, then "
            if can_read
            else "reuse task/entry ids already in context, then "
        )
        guidance = (
            f"{action} {urgency} before large work: {lookup}call "
            "memory_remember with kind='checkpoint' and task/new_task to "
            "record only the durable delta (goal, decisions, open work, "
            "next step, references). Do not dump the transcript, secrets, "
            "or DM content. After recording, call compact to summarize the "
            "session and free context."
        )
    else:
        guidance = (
            "You may proactively checkpoint at a task boundary: call "
            "memory_remember with kind='checkpoint' and task/new_task, "
            "reusing ids and references instead of copying memory text. "
            "After checkpointing a finished task, you may call compact to "
            "free context."
        )
    return f"{head}\n{guidance}"
