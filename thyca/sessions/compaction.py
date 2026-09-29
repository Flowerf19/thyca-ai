from __future__ import annotations

import json

from thyca.core.context import BACKSTOP_RATIO
from thyca.core.protocol import Message
from thyca.core.protocol import estimate_tokens as _chars_to_tokens

_EXCERPT_LIMIT = 1000
# Reserved head budget for the prior marker's excerpt on re-compaction.
# The new (most recent) omitted content keeps the remainder at the tail,
# so the total excerpt never exceeds _EXCERPT_LIMIT.
_PRIOR_EXCERPT_LIMIT = _EXCERPT_LIMIT // 2


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


def _marker_reserve_tokens() -> int:
    """Worst-case marker cost: a full-excerpt probe, counted like any message."""
    probe = Message(
        role="system",
        content="[compaction: omitted 0 messages/0 turns; excerpt: "
        + "x" * _EXCERPT_LIMIT
        + "]",
    )
    return estimate_tokens(probe)


class SessionCompactor:
    """Turn-safe tail policy. No I/O."""

    def compact(
        self,
        messages: list[Message],
        context_tokens: int,
        *,
        hot_tokens: int = 0,
        tools_tokens: int = 0,
        pending_user_tokens: int = 0,
    ) -> list[Message] | None:
        overhead = hot_tokens + tools_tokens + pending_user_tokens
        # Fire at the backstop ratio, not at the cap: a turn that would
        # die at the mid-turn guard must compact first (no dead zone).
        # Overhead covers what assemble adds after this (hot prompt, tools
        # schema, pending user text) so the estimate matches the guard's.
        if (
            sum(estimate_tokens(msg) for msg in messages) + overhead
            <= int(context_tokens * BACKSTOP_RATIO)
        ):
            return None

        leading = 0
        while leading < len(messages) and messages[leading].role == "system":
            leading += 1
        body = messages[leading:]
        turns = self._turns(body)

        budget = int((context_tokens - overhead - _marker_reserve_tokens()) * 0.6)
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
        prior_text = "\n".join(prior_parts)
        prior_kept = (
            self._clip_excerpt(prior_text, _PRIOR_EXCERPT_LIMIT)
            if prior_text
            else ""
        )
        excerpt_src = "\n".join(
            msg.content
            for msg in fresh
            if msg.role in ("user", "assistant") and msg.content
        )
        if prior_kept:
            new_budget = _EXCERPT_LIMIT - len(prior_kept) - 1
            new_kept = (
                self._clip_excerpt(excerpt_src, new_budget)
                if excerpt_src and new_budget > 0
                else ""
            )
            excerpt = f"{prior_kept}\n{new_kept}" if new_kept else prior_kept
        else:
            excerpt = self._clip_excerpt(excerpt_src)
        total_messages = prior_messages + len(fresh)
        total_turns = prior_turns + (len(turns) - len(kept))
        total_chars = prior_chars + sum(len(msg.content or "") for msg in fresh)
        marker = Message(
            role="system",
            content=(
                f"[compaction: omitted {total_messages} messages/"
                f"{total_turns} turns; excerpt: {excerpt}]"
            ),
            meta={
                "omitted_messages": total_messages,
                "omitted_turns": total_turns,
                "omitted_chars": total_chars,
            },
        )
        return [marker, *tail]

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
