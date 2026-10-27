"""Live agent status for Agent Card responses.

Agent Cards (``TWIN.md``) are static identity/configuration. This module
owns the separate, in-memory runtime snapshot that ``GET /api/bots``
merges into each public card as ``agent_status``. It records only
operational metadata (bot, room, trace/span, phase, sanitized tool or
skill names) — never prompts, message content, tool arguments/results,
or exception text.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


#: A completed turn stays visibly "done" only briefly; after this the
#: bot is simply idle again. Waiting/error latches behave differently:
#: they persist until the user answers in that room or the bot starts
#: another turn there (see ``note_user_message`` / ``begin_turn``).
DONE_TTL_SECONDS = 60

_BOT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TERMINAL_OUTCOMES = frozenset({"done", "error", "cancelled"})


def _clean_bot_id(value: object) -> str | None:
    if isinstance(value, str) and len(value) <= 64 and _BOT_ID_RE.fullmatch(value):
        return value
    return None


def _clean_id(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _format_ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class _Action:
    kind: str
    name: str
    call_id: str
    since: datetime


@dataclass
class _Turn:
    bot_id: str
    room_id: str
    trace_id: str | None
    span_id: str
    started_at: datetime
    updated_at: datetime
    phase: str = "turn"
    actions: dict[str, _Action] = field(default_factory=dict)
    wait_kind: str | None = None  # "user" (ask_user) | "blocked" (report_error)


@dataclass
class _Latch:
    state: str  # waiting | done | error
    since: datetime
    updated_at: datetime
    room_id: str
    trace_id: str | None
    span_id: str | None
    reason: str
    expires_at: datetime | None = None


class AgentStatusTracker:
    """Thread-safe, in-memory status registry keyed by turn span.

    One bot may run turns in several rooms at once, and A2A delivery
    nests one bot's turn inside another's tool call, so active work is
    tracked per span; the public snapshot aggregates per bot.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, _Turn] = {}
        self._latches: dict[tuple[str, str], _Latch] = {}
        self._last_results: dict[str, dict] = {}

    # -- turn lifecycle -------------------------------------------------

    def begin_turn(
        self,
        bot_id: object,
        room_id: object,
        *,
        trace_id: object = None,
        span_id: object = None,
        at: datetime | None = None,
    ) -> str | None:
        """Open one active turn record; returns the span id used."""
        bot = _clean_bot_id(bot_id)
        span = _clean_id(span_id)
        if bot is None or span is None:
            return None
        now = at or datetime.now(UTC)
        room = room_id if isinstance(room_id, str) else ""
        with self._lock:
            # New work in a room supersedes that room's terminal latch.
            self._latches.pop((bot, room), None)
            self._active[span] = _Turn(
                bot_id=bot,
                room_id=room,
                trace_id=_clean_id(trace_id),
                span_id=span,
                started_at=now,
                updated_at=now,
            )
        return span

    def observe_event(
        self,
        bot_id: object,
        span_id: object,
        event: object,
        *,
        at: datetime | None = None,
    ) -> None:
        """Fold one live turn event into its turn record.

        Unknown spans and events stamped for another bot are ignored:
        a stale span must never resurrect or corrupt a newer turn."""
        bot = _clean_bot_id(bot_id)
        span = _clean_id(span_id)
        if bot is None or span is None:
            return
        event_bot = _clean_bot_id(getattr(event, "bot_id", None))
        if event_bot is not None and event_bot != bot:
            return
        event_type = getattr(event, "type", None) or getattr(event, "TYPE", None)
        if not isinstance(event_type, str):
            return
        now = at or datetime.now(UTC)
        with self._lock:
            turn = self._active.get(span)
            if turn is None or turn.bot_id != bot:
                return
            turn.updated_at = now
            if event_type in ("llm.started", "llm.retry", "llm.thinking", "llm.content"):
                turn.phase = "llm"
            elif event_type in ("tool.started", "skill.started"):
                kind = "skill" if event_type.startswith("skill.") else "tool"
                call_id = _clean_id(getattr(event, "call_id", None)) or f"{kind}:{now.isoformat()}"
                name = getattr(event, "name", None)
                turn.phase = kind
                turn.actions[call_id] = _Action(
                    kind=kind,
                    name=name if isinstance(name, str) and name else kind,
                    call_id=call_id,
                    since=now,
                )
            elif event_type in ("tool.finished", "skill.finished"):
                call_id = _clean_id(getattr(event, "call_id", None))
                if call_id is not None:
                    turn.actions.pop(call_id, None)
                turn.phase = "turn"
            elif event_type == "llm.finished":
                turn.phase = "turn"
            elif event_type.startswith("session.naming."):
                turn.phase = "naming"
            else:
                # turn.accepted, context.shrunk, and anything else the
                # loop emits still prove the turn is alive and working.
                turn.phase = "turn"

    def note_card_posted(
        self,
        bot_id: object,
        room_id: object,
        card_type: object,
        *,
        at: datetime | None = None,
    ) -> None:
        """Remember that a bot asked the user or reported being blocked.

        The latch lands when the posting turn finishes (the bot may
        still write its closing line); with no active turn it latches
        immediately."""
        bot = _clean_bot_id(bot_id)
        if bot is None or card_type not in ("question", "error"):
            return
        room = room_id if isinstance(room_id, str) else ""
        now = at or datetime.now(UTC)
        wait_kind = "user" if card_type == "question" else "blocked"
        with self._lock:
            candidates = [
                turn for turn in self._active.values()
                if turn.bot_id == bot and turn.room_id == room
            ]
            if candidates:
                turn = max(candidates, key=lambda item: item.updated_at)
                turn.wait_kind = wait_kind
                turn.updated_at = now
                return
            self._latches[(bot, room)] = _Latch(
                state="waiting" if card_type == "question" else "error",
                since=now,
                updated_at=now,
                room_id=room,
                trace_id=None,
                span_id=None,
                reason="ask_user" if card_type == "question" else "report_error",
            )

    def finish_turn(
        self,
        bot_id: object,
        span_id: object,
        outcome: object,
        *,
        code: str | None = None,
        at: datetime | None = None,
    ) -> bool:
        """Close one active turn and latch its terminal state.

        Returns False when the span is unknown (already finished), so
        callers may safely attempt exactly-once finishing from several
        layers without extending a done latch."""
        bot = _clean_bot_id(bot_id)
        span = _clean_id(span_id)
        if bot is None or span is None or outcome not in _TERMINAL_OUTCOMES:
            return False
        now = at or datetime.now(UTC)
        with self._lock:
            turn = self._active.get(span)
            if turn is None or turn.bot_id != bot:
                return False
            del self._active[span]
            result: dict = {"outcome": outcome, "at": _format_ts(now)}
            if isinstance(code, str) and code:
                result["code"] = code
            self._last_results[bot] = result
            if outcome == "cancelled":
                return True
            if outcome == "error":
                state, reason = "error", "turn_failed"
                expires = None
            elif turn.wait_kind == "user":
                state, reason = "waiting", "ask_user"
                expires = None
            elif turn.wait_kind == "blocked":
                state, reason = "error", "report_error"
                expires = None
            else:
                state, reason = "done", "turn_completed"
                expires = now + timedelta(seconds=DONE_TTL_SECONDS)
            self._latches[(bot, turn.room_id)] = _Latch(
                state=state,
                since=now,
                updated_at=now,
                room_id=turn.room_id,
                trace_id=turn.trace_id,
                span_id=turn.span_id,
                reason=reason,
                expires_at=expires,
            )
            return True

    # -- room / roster maintenance ---------------------------------------

    def note_user_message(self, room_id: object, *, at: datetime | None = None) -> None:
        """A fresh user message resolves waiting/error/done in its room."""
        room = room_id if isinstance(room_id, str) else ""
        if not room:
            return
        with self._lock:
            for key in [key for key in self._latches if key[1] == room]:
                del self._latches[key]

    def forget_room(self, room_id: object) -> None:
        """Drop every record tied to a deleted room.

        Waiting/error latches have no expiry, so without this a latch
        from a deleted room would outrank fresh states forever."""
        room = room_id if isinstance(room_id, str) else ""
        if not room:
            return
        with self._lock:
            for key in [key for key in self._latches if key[1] == room]:
                del self._latches[key]
            for span in [
                span for span, turn in self._active.items() if turn.room_id == room
            ]:
                del self._active[span]

    def retain(self, bot_ids) -> None:
        """Drop records for bots that no longer exist (deleted cards)."""
        keep = {bot for bot in (_clean_bot_id(item) for item in bot_ids) if bot}
        with self._lock:
            for span in [span for span, turn in self._active.items() if turn.bot_id not in keep]:
                del self._active[span]
            for key in [key for key in self._latches if key[0] not in keep]:
                del self._latches[key]
            for bot in [bot for bot in self._last_results if bot not in keep]:
                del self._last_results[bot]

    # -- snapshots ---------------------------------------------------------

    def snapshot(
        self,
        bot_id: object,
        *,
        paused: bool = False,
        at: datetime | None = None,
    ) -> dict:
        """Public ``agent_status`` for one bot (see module docstring)."""
        bot = _clean_bot_id(bot_id) or ""
        now = at or datetime.now(UTC)
        with self._lock:
            self._prune_expired(now)
            active = [turn for turn in self._active.values() if turn.bot_id == bot]
            active.sort(key=lambda turn: turn.updated_at, reverse=True)
            latches = [
                latch for (owner, _room), latch in self._latches.items() if owner == bot
            ]
            last_result = self._last_results.get(bot)
            base: dict = {
                "state": "idle",
                "since": None,
                "updated_at": _format_ts(last_result_at(last_result)),
                "room_id": None,
                "trace_id": None,
                "span_id": None,
                "phase": None,
                "current_action": None,
                "waiting_on": None,
                "active_turns": len(active),
                "last_result": dict(last_result) if last_result else None,
            }
            if paused:
                base["state"] = "offline"
                return base
            if active:
                thinking = [turn for turn in active if turn.phase == "llm"]
                rep = thinking[0] if thinking else active[0]
                base.update(
                    state="thinking" if thinking else "running",
                    since=_format_ts(rep.started_at),
                    updated_at=_format_ts(rep.updated_at),
                    room_id=rep.room_id or None,
                    trace_id=rep.trace_id,
                    span_id=rep.span_id,
                    phase=rep.phase,
                )
                if rep.actions:
                    action = max(rep.actions.values(), key=lambda item: item.since)
                    base["current_action"] = {
                        "kind": action.kind,
                        "name": action.name,
                        "call_id": action.call_id,
                    }
                return base
            if latches:
                priority = {"error": 0, "waiting": 1, "done": 2}
                latches.sort(key=lambda latch: (priority[latch.state], -latch.updated_at.timestamp()))
                rep = latches[0]
                base.update(
                    state=rep.state,
                    since=_format_ts(rep.since),
                    updated_at=_format_ts(rep.updated_at),
                    room_id=rep.room_id or None,
                    trace_id=rep.trace_id,
                    span_id=rep.span_id,
                    phase="user" if rep.state == "waiting" else "turn",
                )
                if rep.state == "waiting" or rep.reason == "report_error":
                    base["waiting_on"] = {"kind": "user"}
                return base
            return base

    def snapshot_all(self, paused_by_id: dict[str, bool] | None = None) -> dict[str, dict]:
        paused = paused_by_id or {}
        with self._lock:
            ids = (
                {turn.bot_id for turn in self._active.values()}
                | {bot for bot, _room in self._latches}
                | set(self._last_results)
                | set(paused)
            )
        return {
            bot: self.snapshot(bot, paused=bool(paused.get(bot)))
            for bot in sorted(ids)
        }

    def _prune_expired(self, now: datetime) -> None:
        for key in [
            key for key, latch in self._latches.items()
            if latch.expires_at is not None and latch.expires_at <= now
        ]:
            del self._latches[key]


def last_result_at(result: dict | None) -> datetime | None:
    raw = (result or {}).get("at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def workspace_status(
    snapshot: dict,
    *,
    pending=None,
    channel: str = "webui",
) -> dict:
    """Public status shape for one bot's workspace lane.

    Pure copy — never mutates ``snapshot``. A nonterminal private
    follow-up projects as generic ``waiting``/``phase='private'`` with
    ``waiting_on={'kind':'private','state':'waiting_private'}``; the
    question, raw answer, proposal and received-ready state never reach
    this public payload. Active thinking/running keeps its state and may
    attach ``work_id`` only when its room matches the pending's windows.
    """
    out = dict(snapshot)
    out["channel"] = channel
    out["window_id"] = out.get("room_id")
    out.setdefault("work_id", None)
    if pending is None or pending.status in (
        "done", "cancelled", "resumed", "expired"
    ):
        return out
    state = out.get("state")
    if state in ("offline", "thinking", "running", "error"):
        if out.get("room_id") in (pending.origin_window, pending.dm_window):
            out["work_id"] = pending.work_id
        return out
    out.update(
        state="waiting",
        phase="private",
        room_id=pending.origin_window,
        window_id=pending.origin_window,
        waiting_on={"kind": "private", "state": "waiting_private"},
        work_id=pending.work_id,
        pending_private_id=pending.pending_id,
    )
    return out
