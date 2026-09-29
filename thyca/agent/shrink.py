"""Mid-turn context guard: in-memory shrink of tool outputs.

Pure policy, no I/O. The loop calls :func:`shrink_stage_messages` before
each think() when ``context_tokens`` is set. Disk keeps full content: we
return new ``Message`` copies, never mutating inputs, so the caller replacing
``stage.messages`` entries leaves ``session.messages`` untouched.

Execution pointers come from gateway-attached metadata (``meta["exec_id"]``,
set from the gateway's own tracking) or the original ``tool_read`` call
args — never from ids quoted in output text, where an ``exec<N>`` may be a
doc example. History is always untrusted: after a restart or eviction the
id may be dead, so old rows get honest text instead of a pointer.
"""
from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import replace

from thyca.core.context import BACKSTOP_RATIO as BACKSTOP_RATIO
from thyca.core.protocol import Message, ToolCall
from thyca.sessions.compaction import estimate_tokens as _wire_tokens

TRIGGER_RATIO = 0.8
TARGET_RATIO = 0.7
# BACKSTOP_RATIO is imported from core (shared with the pre-turn compactor)
# and stays importable from here for existing importers.

REFETCH_HEAD_CHARS = 500
FASTPATH_HEAD_CHARS = 2048
PROTECT_LAST_ROUNDS = 3

#: Shape check only: authority comes from metadata/call args, never text.
_EXEC_ID_RE = re.compile(r"exec\d+")


def _calls_by_id(messages: list[Message]) -> dict[str, ToolCall]:
    """Original tool calls by id, for correlating results to their calls."""
    return {
        call.id: call
        for message in messages
        if message.role == "assistant" and message.tool_calls
        for call in message.tool_calls
    }


def _live_candidate(
    raw: object, live_exec_ids: Collection[str] | None
) -> str | None:
    """A shape-valid id that is still promiseable, or None."""
    if not isinstance(raw, str) or _EXEC_ID_RE.fullmatch(raw) is None:
        return None
    if live_exec_ids is not None and raw not in live_exec_ids:
        return None
    return raw


def _trusted_exec_id(
    message: Message,
    calls_by_id: dict[str, ToolCall],
    index: int,
    run_start: int,
    live_exec_ids: Collection[str] | None,
) -> str | None:
    """Promiseable execution id for a tool message, or None.

    Sources in order: gateway-attached ``meta["exec_id"]``, then the
    original ``tool_read`` call's ``id`` arg. History (``index < run_start``)
    and error results never promise: the id may be stale, evicted, or never
    have existed. With an unknown live set and no gateway id, a bare
    call-arg id is never promised (zero retention knowledge): delta
    guidance covers that case.
    """
    if index < run_start:
        return None
    meta = message.meta or {}
    if meta.get("is_error"):
        return None
    trusted = _live_candidate(meta.get("exec_id"), live_exec_ids)
    if trusted is not None:
        return trusted
    if live_exec_ids is None and meta.get("exec_id") is None:
        return None
    call = calls_by_id.get(message.tool_call_id or "")
    if call is not None and call.name == "tool_read" and isinstance(
        call.arguments, dict
    ):
        return _live_candidate(call.arguments.get("id"), live_exec_ids)
    return None


def _offered_but_rejected(
    message: Message,
    calls_by_id: dict[str, ToolCall],
    live_exec_ids: Collection[str] | None,
) -> bool:
    """A shape-valid id was offered but rejected by a known live set."""
    if live_exec_ids is None:
        return False
    meta = message.meta or {}
    raw_meta = meta.get("exec_id")
    if (
        isinstance(raw_meta, str)
        and _EXEC_ID_RE.fullmatch(raw_meta) is not None
        and raw_meta not in live_exec_ids
    ):
        return True
    call = calls_by_id.get(message.tool_call_id or "")
    if call is not None and call.name == "tool_read" and isinstance(
        call.arguments, dict
    ):
        raw_arg = call.arguments.get("id")
        if (
            isinstance(raw_arg, str)
            and _EXEC_ID_RE.fullmatch(raw_arg) is not None
            and raw_arg not in live_exec_ids
        ):
            return True
    return False


def _is_tool_read(message: Message, calls_by_id: dict[str, ToolCall]) -> bool:
    call = calls_by_id.get(message.tool_call_id or "")
    return call is not None and call.name == "tool_read"


def estimate_wire_tokens(messages: list[Message], tools_tokens: int = 0) -> int:
    """Round payload estimate: T3 per-message wire tokens + tools schema."""
    return sum(_wire_tokens(message) for message in messages) + tools_tokens


def _msg_round(message: Message) -> int | None:
    round_no = (message.meta or {}).get("round")
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no <= 0:
        return None
    return round_no


def _build_shrunk(
    content: str,
    exec_id: str | None,
    *,
    delta: bool = False,
    evicted: bool = False,
) -> tuple[str, int] | None:
    """Shrunk text + hidden bytes, or None when shrinking would not help."""
    if exec_id is not None:
        head_limit = REFETCH_HEAD_CHARS
        pointer = (
            f'[shrunk: {{hidden}} bytes hidden; re-read via tool_read id="{exec_id}"'
            " offset/limit]"
        )
    elif evicted:
        head_limit = REFETCH_HEAD_CHARS
        pointer = "[shrunk: {hidden} bytes hidden; execution no longer retained]"
    elif delta:
        # A delta-consuming poll (tool_read): a plain re-read reports no new
        # output, so point at positional paging, never at a rerun or an id.
        head_limit = REFETCH_HEAD_CHARS
        pointer = (
            "[shrunk: {hidden} bytes hidden; re-read retained output via "
            "tool_read with offset/limit — a plain re-read may report "
            "no new output]"
        )
    else:
        head_limit = FASTPATH_HEAD_CHARS
        pointer = "[shrunk: {hidden} bytes hidden; re-run the tool if needed]"
    if len(content) <= head_limit:
        return None
    head = content[:head_limit]
    hidden = len(content.encode("utf-8")) - len(head.encode("utf-8"))
    return f"{head}\n{pointer.format(hidden=hidden)}", hidden


def shrink_stage_messages(
    messages: list[Message],
    *,
    current_round: int,
    context_tokens: int,
    tools_tokens: int = 0,
    run_start: int = 0,
    live_exec_ids: Collection[str] | None = None,
) -> tuple[list[Message], int, int, int]:
    """Shrink oldest eligible tool outputs until under 70% of cap.

    Returns ``(new_messages, shrunk_count, hidden_bytes, estimate)`` where
    ``estimate`` is the post-shrink wire estimate (for the 95% backstop).
    Eligible = tool role, not already shrunk, outside the last 3 rounds.
    ``run_start`` is the index where this run's own messages begin: history
    before it restarts round numbering every turn, so round-protection would
    misfire there and is skipped — history always shrinks oldest-first, and
    its execution ids are never promised (stale after restart/eviction).
    ``live_exec_ids`` is the gateway's retained id set when known: a trusted
    id outside it is evicted, so it gets honest text, not a pointer.
    Re-fetchable outputs shrink first (oldest first), delta polls next,
    fast-path last.
    """
    estimate = estimate_wire_tokens(messages, tools_tokens)
    if estimate <= int(context_tokens * TRIGGER_RATIO):
        return list(messages), 0, 0, estimate
    calls_by_id = _calls_by_id(messages)
    candidates: list[tuple[int, int, str | None, bool, bool]] = []
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
        exec_id = _trusted_exec_id(
            message, calls_by_id, index, run_start, live_exec_ids
        )
        is_read = _is_tool_read(message, calls_by_id)
        is_error = bool((message.meta or {}).get("is_error"))
        evicted = (
            exec_id is None
            and is_read
            and not is_error
            and _offered_but_rejected(message, calls_by_id, live_exec_ids)
        )
        delta = exec_id is None and is_read and not is_error and not evicted
        head_limit = (
            REFETCH_HEAD_CHARS
            if exec_id is not None or delta or evicted
            else FASTPATH_HEAD_CHARS
        )
        if len(content) <= head_limit:
            continue
        tier = 0 if exec_id is not None else (1 if delta or evicted else 2)
        candidates.append((tier, index, exec_id, delta, evicted))
    candidates.sort(key=lambda item: (item[0], item[1]))
    shrunk_messages = list(messages)
    shrunk_count = 0
    hidden_total = 0
    target = int(context_tokens * TARGET_RATIO)
    for _, index, exec_id, delta, evicted in candidates:
        original = shrunk_messages[index]
        assert isinstance(original.content, str)
        built = _build_shrunk(
            original.content, exec_id, delta=delta, evicted=evicted
        )
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
