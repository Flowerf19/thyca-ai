"""Mid-turn context guard: in-memory shrink of tool outputs.

Pure policy, no I/O. The loop calls :func:`shrink_stage_messages` before
each think() when ``context_tokens`` is set. Disk keeps full content: we
return new ``Message`` copies, never mutating inputs, so the caller replacing
``stage.messages`` entries leaves ``session.messages`` untouched.
"""
from __future__ import annotations

import re
from dataclasses import replace

from thyca.core.protocol import Message
from thyca.sessions.compaction import estimate_tokens as _wire_tokens

TRIGGER_RATIO = 0.8
TARGET_RATIO = 0.7
BACKSTOP_RATIO = 0.95

REFETCH_HEAD_CHARS = 500
FASTPATH_HEAD_CHARS = 2048
PROTECT_LAST_ROUNDS = 3

_EXEC_TOOL_READ = re.compile(r'tool_read\s+id\s*=\s*["\']?(exec\d+)["\']?')
_EXEC_RUNNING = re.compile(r"still running:\s*(exec\d+)")
_EXEC_STARTED = re.compile(r"started:\s*(exec\d+)")


def find_exec_ids(content: str) -> set[str]:
    """Distinct exec ids referenced by tool output markers."""
    ids: set[str] = set()
    for pattern in (_EXEC_TOOL_READ, _EXEC_RUNNING, _EXEC_STARTED):
        ids.update(pattern.findall(content))
    return ids


def estimate_wire_tokens(messages: list[Message], tools_tokens: int = 0) -> int:
    """Round payload estimate: T3 per-message wire tokens + tools schema."""
    return sum(_wire_tokens(message) for message in messages) + tools_tokens


def _msg_round(message: Message) -> int | None:
    round_no = (message.meta or {}).get("round")
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no <= 0:
        return None
    return round_no


def _build_shrunk(content: str, exec_id: str | None) -> tuple[str, int] | None:
    """Shrunk text + hidden bytes, or None when shrinking would not help."""
    head_limit = REFETCH_HEAD_CHARS if exec_id else FASTPATH_HEAD_CHARS
    if len(content) <= head_limit:
        return None
    head = content[:head_limit]
    hidden = len(content.encode("utf-8")) - len(head.encode("utf-8"))
    if exec_id:
        pointer = (
            f'[shrunk: {hidden} bytes hidden; re-read via tool_read id="{exec_id}"'
            " offset/limit]"
        )
    else:
        pointer = "[shrunk: {} bytes hidden; re-run the tool if needed]".format(hidden)
    return f"{head}\n{pointer}", hidden


def shrink_stage_messages(
    messages: list[Message],
    *,
    current_round: int,
    context_tokens: int,
    tools_tokens: int = 0,
    run_start: int = 0,
) -> tuple[list[Message], int, int, int]:
    """Shrink oldest eligible tool outputs until under 70% of cap.

    Returns ``(new_messages, shrunk_count, hidden_bytes, estimate)`` where
    ``estimate`` is the post-shrink wire estimate (for the 95% backstop).
    Eligible = tool role, not already shrunk, unambiguous exec refs,
    outside the last 3 rounds. ``run_start`` is the index where this run's
    own messages begin: history before it restarts round numbering every
    turn, so round-protection would misfire there and is skipped — history
    always shrinks oldest-first. Re-fetchable outputs shrink first
    (oldest first), fast-path last.
    """
    estimate = estimate_wire_tokens(messages, tools_tokens)
    if estimate <= int(context_tokens * TRIGGER_RATIO):
        return list(messages), 0, 0, estimate
    candidates: list[tuple[bool, int, str | None]] = []
    for index, message in enumerate(messages):
        if message.role != "tool":
            continue
        content = message.content
        if not isinstance(content, str) or not content:
            continue
        if index >= run_start:
            round_no = _msg_round(message)
            if (
                round_no is not None
                and 0 <= current_round - round_no < PROTECT_LAST_ROUNDS
            ):
                continue
        if "[shrunk:" in content:
            continue  # idempotent: never re-shrink, the pointer would lie
        ids = find_exec_ids(content)
        if len(ids) > 1:
            continue  # ambiguous refs: keep intact
        exec_id = next(iter(ids)) if ids else None
        head_limit = REFETCH_HEAD_CHARS if exec_id else FASTPATH_HEAD_CHARS
        if len(content) <= head_limit:
            continue
        candidates.append((exec_id is None, index, exec_id))
    candidates.sort(key=lambda item: (item[0], item[1]))
    shrunk_messages = list(messages)
    shrunk_count = 0
    hidden_total = 0
    target = int(context_tokens * TARGET_RATIO)
    for _, index, exec_id in candidates:
        original = shrunk_messages[index]
        assert isinstance(original.content, str)
        built = _build_shrunk(original.content, exec_id)
        if built is None:
            continue
        text, hidden = built
        replacement = replace(original, content=text)
        saved = _wire_tokens(original) - _wire_tokens(replacement)
        if saved <= 0:
            continue
        shrunk_messages[index] = replacement
        estimate -= saved
        shrunk_count += 1
        hidden_total += hidden
        if estimate < target:
            break
    return shrunk_messages, shrunk_count, hidden_total, estimate
