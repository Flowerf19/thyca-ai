from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from statistics import median

from thyca.core.context import BACKSTOP_RATIO
from thyca.core.protocol import Message
from thyca.core.protocol import estimate_tokens as _chars_to_tokens

_EXCERPT_LIMIT = 1000
# Reserved head budget for the prior marker's excerpt on re-compaction.
# The new (most recent) omitted content keeps the remainder at the tail,
# so the total excerpt never exceeds _EXCERPT_LIMIT.
_PRIOR_EXCERPT_LIMIT = _EXCERPT_LIMIT // 2

CALIBRATION_MIN = 0.5
CALIBRATION_MAX = 2.5
CALIBRATION_PAIRS = 10


def _safe_calibration(value: object) -> float:
    """Clamped calibration factor; anything unusable means uncalibrated."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    factor = float(value)
    if not math.isfinite(factor) or factor <= 0:
        return 1.0
    return min(CALIBRATION_MAX, max(CALIBRATION_MIN, factor))


def calibration_factor(
    messages: list[Message], model: str | None = None
) -> float:
    """Per-model correction from stored (estimate, prompt_tokens) pairs.

    Each assistant row may carry the raw wire estimate recorded just
    before its provider call next to the provider-reported
    ``usage.prompt_tokens``. The median actual/estimated ratio over the
    most recent pairs (optionally only pairs for ``model``) corrects the
    ``len/4`` bias for the model actually in use — including models with
    no public tokenizer. No usable pairs means 1.0 (raw heuristic).
    Outlier ratios outside the clamp band are dropped before the median
    so one provider quirk cannot poison the factor.
    """
    wanted = model.strip() if isinstance(model, str) and model.strip() else None
    ratios: list[float] = []
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        meta = message.meta or {}
        if wanted is not None and meta.get("model") != wanted:
            continue
        usage = meta.get("usage")
        actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        estimated = meta.get("estimate_tokens")
        if isinstance(actual, bool) or not isinstance(actual, int) or actual <= 0:
            continue
        if (
            isinstance(estimated, bool)
            or not isinstance(estimated, int)
            or estimated <= 0
        ):
            continue
        ratio = actual / estimated
        if CALIBRATION_MIN <= ratio <= CALIBRATION_MAX:
            ratios.append(ratio)
            if len(ratios) >= CALIBRATION_PAIRS:
                break
    if not ratios:
        return 1.0
    return _safe_calibration(median(ratios))


def estimate_tokens(msg: Message) -> int:
    """Wire-payload token estimate for one transcript message.

    Mirrors the chat adapter payload (``_to_openai_message``): both adapters
    strip ``ts``/``meta``/``reasoning``, while ``reasoning_details`` rides the
    chat path uncapped — so it stays counted (conservative for responses).
    """
    payload: dict = {"role": msg.role, "content": msg.content}
    if msg.reasoning_details:
        payload["reasoning_details"] = msg.reasoning_details
    if msg.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in msg.tool_calls
        ]
    if msg.tool_call_id is not None:
        payload["tool_call_id"] = msg.tool_call_id
    return _chars_to_tokens(json.dumps(payload, ensure_ascii=False))


def context_usage(
    messages: list[Message],
    context_tokens: int,
    *,
    hot_tokens: int = 0,
    tools_tokens: int = 0,
    pending_user_tokens: int = 0,
    calibration: float = 1.0,
) -> dict:
    """Wire-estimate snapshot for UI hints: transcript + turn overhead.

    Uses the same estimator the compactor and the mid-turn guard use, so a
    displayed ratio means the same thing as the 80%/95% thresholds. Pure:
    callers that cannot measure overhead pass 0 and get a transcript-only
    lower bound. ``transcript_tokens``/``overhead_tokens`` stay raw;
    ``estimated_tokens`` is the calibrated total the ratio uses.
    """
    factor = _safe_calibration(calibration)
    transcript = sum(estimate_tokens(msg) for msg in messages)
    overhead = hot_tokens + tools_tokens + pending_user_tokens
    estimated = round((transcript + overhead) * factor)
    ratio = estimated / context_tokens if context_tokens > 0 else 0.0
    return {
        "estimated_tokens": estimated,
        "transcript_tokens": transcript,
        "overhead_tokens": overhead,
        "context_tokens": context_tokens,
        "ratio": ratio,
        "calibration": factor,
    }


def _marker_reserve_tokens(body_chars: int = _EXCERPT_LIMIT) -> int:
    """Worst-case marker cost: a full-body probe, counted like any message."""
    probe = Message(
        role="system",
        content="[compaction: omitted 0 messages/0 turns; excerpt: "
        + "x" * max(0, body_chars)
        + "]",
    )
    return estimate_tokens(probe)


@dataclass(frozen=True)
class CompactionPlan:
    """Turn-safe split for one compaction: keep tail, summarize the rest.

    ``fresh`` is the newly omitted non-marker messages (summarizer
    input); ``prior_parts`` are bodies carried from earlier markers
    (merged by the summarizer, or reserve-clipped by the excerpt path).
    Totals already include the carried counts, ready for the marker.
    """

    tail: list[Message] = field(default_factory=list)
    fresh: list[Message] = field(default_factory=list)
    prior_parts: list[str] = field(default_factory=list)
    total_messages: int = 0
    total_turns: int = 0
    total_chars: int = 0

    @property
    def prior_text(self) -> str:
        return "\n".join(self.prior_parts)


class SessionCompactor:
    """Turn-safe tail policy. No I/O."""

    def plan(
        self,
        messages: list[Message],
        context_tokens: int,
        *,
        hot_tokens: int = 0,
        tools_tokens: int = 0,
        pending_user_tokens: int = 0,
        force: bool = False,
        calibration: float = 1.0,
        body_chars: int = _EXCERPT_LIMIT,
    ) -> CompactionPlan | None:
        """Split keep/omit for one compaction, or None when none is due.

        ``body_chars`` reserves room for the marker body the caller will
        render (1000-char excerpt, or a longer LLM summary), so the tail
        budget already accounts for it.
        """
        overhead = hot_tokens + tools_tokens + pending_user_tokens
        # Compare in raw-estimate units: dividing the cap by the factor
        # makes every threshold/budget below mean calibrated tokens.
        cap_raw = context_tokens / _safe_calibration(calibration)
        # Fire at the backstop ratio, not at the cap: a turn that would
        # die at the mid-turn guard must compact first (no dead zone).
        # Overhead covers what assemble adds after this (hot prompt, tools
        # schema, pending user text) so the estimate matches the guard's.
        # ``force`` is the manual path: same turn-safe tail, no threshold.
        if not force and (
            sum(estimate_tokens(msg) for msg in messages) + overhead
            <= int(cap_raw * BACKSTOP_RATIO)
        ):
            return None

        leading = 0
        while leading < len(messages) and messages[leading].role == "system":
            leading += 1
        body = messages[leading:]
        turns = self._turns(body)

        budget = int((cap_raw - overhead - _marker_reserve_tokens(body_chars)) * 0.6)
        kept: list[list[Message]] = []
        used = 0
        for turn in reversed(turns):
            cost = sum(estimate_tokens(msg) for msg in turn)
            if kept and used + cost > budget:
                break
            kept.append(turn)
            used += cost
        kept.reverse()
        tail = [msg for turn in kept for msg in turn]
        omitted = messages[:leading] + body[: len(body) - len(tail)]
        if len(tail) == len(body):
            # Overhead alone tripped the cap but every turn fits: shrinking
            # nothing must not mint a junk 0/0 marker (and churn the file).
            # Subsumes `not omitted`, and covers omitted==[prior marker]
            # with tail==body, which would otherwise re-wrap and clip the
            # prior excerpt 1000->500 chars on every overhead-only trip.
            return None
        prior_parts: list[str] = []
        prior_messages = 0
        prior_turns = 0
        prior_chars = 0
        fresh: list[Message] = []
        for msg in omitted:
            if msg.role == "system" and (msg.content or "").startswith(
                "[compaction: "
            ):
                part = self._prior_excerpt(msg.content or "")
                if part:
                    prior_parts.append(part)
                meta = msg.meta or {}
                carried = meta.get("omitted_messages")
                prior_messages += carried if isinstance(carried, int) else 1
                carried = meta.get("omitted_turns")
                prior_turns += carried if isinstance(carried, int) else 0
                carried = meta.get("omitted_chars")
                prior_chars += (
                    carried
                    if isinstance(carried, int)
                    else len(msg.content or "")
                )
            else:
                fresh.append(msg)
        return CompactionPlan(
            tail=tail,
            fresh=fresh,
            prior_parts=prior_parts,
            total_messages=prior_messages + len(fresh),
            total_turns=prior_turns + (len(turns) - len(kept)),
            total_chars=prior_chars + sum(len(msg.content or "") for msg in fresh),
        )

    def excerpt_body(self, plan: CompactionPlan) -> str:
        """Rule-based marker body: prior reserve + tail of fresh text."""
        prior_kept = (
            self._clip_excerpt(plan.prior_text, _PRIOR_EXCERPT_LIMIT)
            if plan.prior_text
            else ""
        )
        excerpt_src = "\n".join(
            msg.content
            for msg in plan.fresh
            if msg.role in ("user", "assistant") and msg.content
        )
        if prior_kept:
            new_budget = _EXCERPT_LIMIT - len(prior_kept) - 1
            new_kept = (
                self._clip_excerpt(excerpt_src, new_budget)
                if excerpt_src and new_budget > 0
                else ""
            )
            return f"{prior_kept}\n{new_kept}" if new_kept else prior_kept
        return self._clip_excerpt(excerpt_src)

    @staticmethod
    def render(plan: CompactionPlan, body: str) -> list[Message]:
        """Marker + tail for a plan. ``body`` must already be capped.

        The ``excerpt:`` label is kept for both excerpt and LLM-summary
        bodies: store/assemble only key on the ``[compaction: `` prefix,
        and re-compaction carries the prior body through this label.
        """
        marker = Message(
            role="system",
            content=(
                f"[compaction: omitted {plan.total_messages} messages/"
                f"{plan.total_turns} turns; excerpt: {body}]"
            ),
            meta={
                "omitted_messages": plan.total_messages,
                "omitted_turns": plan.total_turns,
                "omitted_chars": plan.total_chars,
            },
        )
        return [marker, *plan.tail]

    def compact(
        self,
        messages: list[Message],
        context_tokens: int,
        *,
        hot_tokens: int = 0,
        tools_tokens: int = 0,
        pending_user_tokens: int = 0,
        force: bool = False,
        calibration: float = 1.0,
    ) -> list[Message] | None:
        plan = self.plan(
            messages,
            context_tokens,
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            pending_user_tokens=pending_user_tokens,
            force=force,
            calibration=calibration,
        )
        if plan is None:
            return None
        return self.render(plan, self.excerpt_body(plan))

    @staticmethod
    def _clip_excerpt(text: str, limit: int = _EXCERPT_LIMIT) -> str:
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        cut = text[-limit:]
        # Tail cut: a leading low surrogate lost its high half.
        if cut and "\udc00" <= cut[0] <= "\udfff":
            return cut[1:]
        return cut

    @staticmethod
    def _prior_excerpt(content: str) -> str:
        _, sep, tail = content.partition("excerpt: ")
        if not sep:
            return ""
        return tail.removesuffix("]")

    @staticmethod
    def _turns(messages: list[Message]) -> list[list[Message]]:
        turns: list[list[Message]] = []
        current: list[Message] = []
        pending: set[str] = set()
        for msg in messages:
            current.append(msg)
            if msg.role == "assistant":
                pending.update(call.id for call in (msg.tool_calls or []))
            elif msg.role == "tool":
                pending.discard(msg.tool_call_id or "")
            if pending:
                continue
            if msg.role in ("assistant", "tool"):
                turns.append(current)
                current = []
        if current:
            turns.append(current)
        return turns
