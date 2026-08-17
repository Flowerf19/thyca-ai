from __future__ import annotations

import json

from thyca.protocol import Message

_EXCERPT_LIMIT = 1000


def estimate_tokens(msg: Message) -> int:
    value = json.dumps(msg.to_canonical_dict(), ensure_ascii=False)
    return (len(value) + 3) // 4


class SessionCompactor:
    """Turn-safe tail policy. No I/O."""

    def compact(self, messages: list[Message], context_tokens: int) -> list[Message] | None:
        if sum(estimate_tokens(msg) for msg in messages) <= context_tokens:
            return None

        leading = 0
        while leading < len(messages) and messages[leading].role == "system":
            leading += 1
        body = messages[leading:]
        turns = self._turns(body)

        budget = int(context_tokens * 0.6)
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
        excerpt_src = "\n".join(
            msg.content
            for msg in omitted
            if msg.role in ("user", "assistant") and msg.content
        )
        excerpt = self._clip_excerpt(excerpt_src)
        omitted_chars = sum(len(msg.content or "") for msg in omitted)
        marker = Message(
            role="system",
            content=(
                f"[compaction: omitted {len(omitted)} messages/"
                f"{len(turns) - len(kept)} turns; excerpt: {excerpt}]"
            ),
            meta={
                "omitted_messages": len(omitted),
                "omitted_turns": len(turns) - len(kept),
                "omitted_chars": omitted_chars,
            },
        )
        return [marker] + tail

    @staticmethod
    def _clip_excerpt(text: str, limit: int = _EXCERPT_LIMIT) -> str:
        if len(text) <= limit:
            return text
        cut = text[:limit]
        if "\ud800" <= cut[-1] <= "\udbff":
            return cut[:-1]
        return cut

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
