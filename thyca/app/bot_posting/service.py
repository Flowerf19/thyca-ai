"""Bot posting service: DM/room message + card writes, DM-pending consume.

Owns the ``message_user``/``ask_user``/``report_error`` landing paths
(AdminDeps ``dm_post``/``card_post``/``room_post``/``dm_card_post``),
the WebUIOutboundAdapter posts, and the private-followup DM answer
consume step. ChatApp keeps thin wrappers with identical signatures so
all wiring and callers are untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from thyca.core.protocol import Message
from thyca.sessions import SessionManager

if TYPE_CHECKING:
    from thyca.bots.registry import BotRegistry


@dataclass
class BotPostingDeps:
    """Concrete collaborators for :class:`BotPostingService`."""

    root: Path
    sessions: Any
    bots: BotRegistry
    current_cfg: Callable[[], Any]
    bot_dm: Callable[[str], dict]
    agent_status: Any


class BotPostingService:
    """DM/room posting + card landing paths (see module doc)."""

    def __init__(self, deps: BotPostingDeps) -> None:
        self._deps = deps

    def display_meta(self, bot_id: str) -> dict:
        """Display companions so transcript/Trace use the card's own
        name+tint instead of the FE hash fallback."""
        try:
            card = self._deps.bots.require(bot_id)
        except Exception:
            return {}
        return {"bot_name": card.name, "bot_tint": card.tint}

    def consume_dm_pending(self, bot_id: str, session_id: str,
                           room) -> None:
        """A DM answer to a waiting private follow-up -> received.

        Runs right after the user row is persisted (never on retry, so a
        replays can't double-consume). The stored answer_ref points at
        the real transcript row; the raw body is never copied into the
        pending."""
        from thyca.app.turn_event import current_channel_event
        from thyca.bots.private_followups import PrivateFollowupStore

        ev = current_channel_event()
        if ev is None or not ev.event_id:
            return
        if getattr(ev.actor, "kind", None) != "user":
            return
        session = self._deps.sessions.store.load(session_id)
        if session.dm_bot != bot_id:
            return
        store = PrivateFollowupStore(self._deps.root)
        pending = store.active(bot_id)
        if pending is None or pending.status != "waiting_private":
            return
        if pending.dm_window != session_id:
            return
        row_index = len(self._deps.sessions.store.load(session_id).messages) - 1
        store.receive(
            bot_id,
            pending.pending_id,
            answer_event_id=ev.event_id,
            answer_ref=f"{session_id}:{row_index}",
            idempotency_key=ev.event_id,
        )

    def post_bot_dm_message(self, bot_id: str, text: str,
                            current_room_id: str = "", meta: dict | None = None) -> dict:
        """Post *text* into *bot_id*'s own DM under its own name.

        The ``message_user`` tool's landing path (user decision
        2026-10-13): a bot that needs to talk privately with the user
        writes into its own DM window — never into the room its current
        turn happens to run in. Refused when the current turn already
        runs in that DM, where a normal reply is the private answer."""
        dm = self._deps.bot_dm(bot_id)
        if current_room_id and current_room_id == dm["id"]:
            return {"ok": False, "error": (
                "you are already in your private DM with the user; "
                "just reply normally")}
        cfg = self._deps.current_cfg()
        room = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._deps.sessions.store,
        )
        room.load(dm["id"])
        if meta and meta.get("kind") == "private_question":
            for m in reversed(list(room.current.messages)):
                m_meta = m.meta or {}
                if m_meta.get("pfq_marker") == meta.get("pfq_marker"):
                    return {"ok": True, "session_id": dm["id"],
                            "deduped": True}
        row_meta = {"bot_id": bot_id, "kind": "message",
                    **self.display_meta(bot_id)}
        if meta:
            row_meta.update(meta)
        room.append(Message(
            role="assistant",
            content=text,
            meta=row_meta,
        ))
        return {"ok": True, "session_id": dm["id"]}

    def post_bot_dm_card(self, bot_id: str, card: dict) -> dict:
        """Post a structured card into the bot's OWN DM window."""
        dm = self._deps.bot_dm(bot_id)
        cfg = self._deps.current_cfg()
        room = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._deps.sessions.store,
        )
        room.load(dm["id"])
        pf = card.get("private_followup") if isinstance(card, dict) else None
        if isinstance(pf, dict) and pf.get("pending_id"):
            for m in reversed(list(room.current.messages)):
                m_card = (m.meta or {}).get("card") or {}
                m_pf = m_card.get("private_followup") or {}
                if (
                    m_pf.get("pending_id") == pf.get("pending_id")
                    and m_pf.get("revision") == pf.get("revision")
                ):
                    return {"ok": True, "session_id": dm["id"],
                            "deduped": True}
        room.append(Message(
            role="assistant",
            content=card.get("title", "question"),
            meta={"bot_id": bot_id, "kind": "card", "card": dict(card),
                  **self.display_meta(bot_id)},
        ))
        return {"ok": True, "session_id": dm["id"]}

    def post_bot_room_message(self, bot_id: str, text: str,
                              room_id: str, meta: dict | None = None) -> dict:
        """Post a plain status row into a room the bot participates in.

        ``meta`` is for trusted internal callers only — when it carries a
        ``pending_id`` marker the room is scanned first and a matching
        row means the write already happened (crash-safe resume): the
        existing row is returned instead of a duplicate."""
        if not room_id:
            return {"ok": False, "error": "no room"}
        session = next(
            (s for s in self._deps.sessions.list_sessions() if s.id == room_id),
            None,
        )
        if session is None:
            return {"ok": False, "error": f"unknown room: {room_id}"}
        if session.dm_bot is not None and session.dm_bot != bot_id:
            return {"ok": False, "error": "room is another bot's DM"}
        participants = list(session.bots) if session.bots else ["thyca"]
        if bot_id not in participants:
            return {"ok": False, "error": f"not a participant: {bot_id}"}
        cfg = self._deps.current_cfg()
        room = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._deps.sessions.store,
        )
        room.load(room_id)
        marker = (meta or {}).get("pending_id")
        if marker is not None:
            for m in reversed(list(room.current.messages)):
                m_meta = m.meta or {}
                if (
                    m_meta.get("pending_id") == marker
                    and m_meta.get("kind") == (meta or {}).get("kind")
                    and m_meta.get("proposal_revision") == (meta or {}).get("proposal_revision")
                    and m_meta.get("bot_id") == bot_id
                ):
                    return {"ok": True, "session_id": room_id,
                            "deduped": True}
        row_meta = {"bot_id": bot_id, "kind": "status",
                    **self.display_meta(bot_id)}
        if meta:
            row_meta.update(meta)
        room.append(Message(
            role="assistant",
            content=text,
            meta=row_meta,
        ))
        return {"ok": True, "session_id": room_id}

    def post_bot_card(self, bot_id: str, card: dict,
                      current_room_id: str = "", content: str = "") -> dict:
        """Post a structured question/error card into the current room.

        The ``ask_user``/``report_error`` landing path: the card rides
        on an assistant row's ``meta.card`` (the FE renderer was ready
        before this contract extension existed). Privacy gates mirror
        A2A recording: another bot's DM never receives the card, and a
        non-DM room only accepts cards from its participants."""
        if not current_room_id:
            return {"ok": False, "error": "no current room for a card"}
        session = next(
            (s for s in self._deps.sessions.list_sessions() if s.id == current_room_id),
            None,
        )
        if session is None:
            return {"ok": False, "error": f"unknown room: {current_room_id}"}
        if session.dm_bot is not None and session.dm_bot != bot_id:
            return {"ok": False, "error": (
                "current room is another bot's private DM; return the "
                "question/problem to that bot or use message_user in "
                "your own DM")}
        participants = list(session.bots) if session.bots else ["thyca"]
        if bot_id not in participants:
            return {"ok": False, "error": f"bot is not a participant of this room: {bot_id}"}
        meta: dict = {"bot_id": bot_id, "kind": "card", "card": dict(card)}
        meta.update(self.display_meta(bot_id))
        cfg = self._deps.current_cfg()
        room = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._deps.sessions.store,
        )
        room.load(current_room_id)
        from thyca.app.turn_event import current_channel_event
        from thyca.core.channel import WORK_REF_RE

        _ev = current_channel_event()
        if _ev is not None:
            meta.setdefault("channel", _ev.channel_id)
            meta.setdefault("window_id", _ev.window_id)
            _wr = getattr(_ev, "work_ref", None)
            if isinstance(_wr, str) and WORK_REF_RE.fullmatch(_wr):
                meta.setdefault("work_id", _wr)
        room.append(Message(role="assistant", content=content, meta=meta))
        card_type = card.get("type") if isinstance(card, dict) else None
        self._deps.agent_status.note_card_posted(bot_id, current_room_id, card_type)
        return {"ok": True, "session_id": current_room_id}
