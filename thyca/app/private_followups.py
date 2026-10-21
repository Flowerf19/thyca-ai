"""Native consent service (WS-4).

The API route talks only to this facade: it parses safe types, maps the
service's typed errors to HTTP codes, and never touches app internals.
The real work runs inside the app's event loop — under the bot's
workspace lease AND a per-bot delivery FileLock — so a crash or retry
between the room write and the state finalize can never double-post.
"""
from __future__ import annotations

from pathlib import Path

from thyca.bots.private_followups import PrivateFollowupError, PrivateFollowupStore
from thyca.sessions import SessionBusy, SessionNotFound


class ConsentError(Exception):
    """Typed service failure with a fixed public HTTP status."""

    status = 500

    def __init__(self, message: str = "private follow-up failed") -> None:
        super().__init__(message)


class ConsentNotFound(ConsentError):
    status = 404


class ConsentConflict(ConsentError):
    status = 409


class ConsentUnavailable(ConsentError):
    status = 503


class ConsentBadRequest(ConsentError):
    status = 400


def _delivery_lock(app, bot_id: str):
    from thyca.config.store import FileLock

    path = Path(app._root) / "bots" / bot_id / "workspace" / "private_delivery.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(path)


class PrivateFollowupService:
    """decide(session_id, pending_id, revision, choice) for the route."""

    def __init__(self, app) -> None:
        self._app = app

    def decide(
        self,
        session_id: str,
        pending_id: str,
        revision: int,
        choice: str,
    ) -> dict:
        """Sync facade: submits to the app loop, blocks for the result."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ConsentBadRequest("missing session")
        if not isinstance(pending_id, str) or not pending_id.strip():
            raise ConsentBadRequest("missing pending_id")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ConsentBadRequest("missing revision")
        if choice not in ("approve", "keep_private", "cancel"):
            raise ConsentBadRequest("unknown choice")
        if revision < 0 or (choice == "approve" and revision < 1):
            raise ConsentBadRequest("invalid revision")
        try:
            return self._app._submit(self._decide(
                session_id, pending_id.strip(), revision, choice))
        except ConsentError:
            raise
        except (PrivateFollowupError, OSError):
            raise ConsentUnavailable("private follow-up unavailable") from None

    async def _decide(
        self,
        session_id: str,
        pending_id: str,
        revision: int,
        choice: str,
    ) -> dict:
        app = self._app
        try:
            dm_session = app._sessions.store.load(session_id)
        except SessionNotFound:
            raise ConsentNotFound("session not found")
        dm_bot = dm_session.dm_bot
        if dm_bot is None:
            raise ConsentBadRequest("not a bot DM")
        store = PrivateFollowupStore(app._root)
        async with app._runtime.workspaces.lease(dm_bot):
            with _delivery_lock(app, dm_bot):
                if app._sessions.store.load(session_id).dm_bot != dm_bot:
                    raise ConsentConflict("DM changed")
                pending = store.get(dm_bot, pending_id)
                if pending is None or pending.dm_window != session_id:
                    raise ConsentNotFound("unknown pending")
                if revision != pending.proposal_revision:
                    raise ConsentConflict("stale proposal revision")
                marker = f"{pending.pending_id}:{revision}"
                if choice == "approve":
                    return self._approve(
                        store, dm_bot, pending, revision, marker)
                if choice == "keep_private":
                    try:
                        store.keep_private(
                            dm_bot, pending.pending_id, revision=revision)
                    except PrivateFollowupError:
                        raise ConsentConflict("pending cannot be changed") from None
                    return {"ok": True, "status": "ready"}
                try:
                    store.cancel(dm_bot, pending.pending_id)
                except PrivateFollowupError:
                    raise ConsentConflict("pending cannot be changed") from None
                return {"ok": True, "status": "cancelled"}

    def cancel_window(self, session_id: str) -> bool:
        return self._app._submit(self._cancel_window(session_id))

    async def _cancel_window(self, session_id: str) -> bool:
        app = self._app
        session = app._sessions.store.load(session_id)
        store = PrivateFollowupStore(app._root)
        owners = [session.dm_bot] if session.dm_bot else (session.bots or ["thyca"])
        candidates = []
        for bot_id in owners:
            pending = store.active(bot_id)
            if pending is not None and session_id in (
                pending.dm_window, pending.origin_window
            ):
                candidates.append((bot_id, pending.pending_id))
        if len(candidates) > 1:
            raise SessionBusy("multiple pending private follow-ups")
        if not candidates:
            return False
        bot_id, pending_id = candidates[0]
        async with app._runtime.workspaces.lease(bot_id):
            with _delivery_lock(app, bot_id):
                pending = store.get(bot_id, pending_id)
                if pending is None or pending.status not in (
                    "open", "waiting_private", "received"
                ):
                    return False
                store.cancel(bot_id, pending_id)
                return True

    def _approve(
        self,
        store: PrivateFollowupStore,
        dm_bot: str,
        pending,
        revision: int,
        marker: str,
    ) -> dict:
        app = self._app
        if pending.status == "resumed":
            if pending.approved_revision == revision:
                return {"ok": True, "status": "resumed"}
            raise ConsentConflict("already resumed")
        try:
            origin = app._sessions.store.load(pending.origin_window)
        except Exception:
            raise ConsentConflict("origin room is gone")
        try:
            bot_card = app._bots.require(dm_bot)
        except Exception:
            bot_card = None
        if (
            origin.dm_bot is not None
            or dm_bot not in (origin.bots or ())
            or bot_card is None
            or getattr(bot_card, "paused", False)
        ):
            raise ConsentConflict("origin is not shareable")
        try:
            pending = store.approve(
                dm_bot, pending.pending_id, revision=revision,
                idempotency_key=marker,
            )
        except PrivateFollowupError:
            raise ConsentConflict("pending cannot be approved")
        posted = app.post_bot_room_message(
            dm_bot,
            pending.proposed_redacted,
            pending.origin_window,
            meta={
                "kind": "private_resume",
                "pending_id": pending.pending_id,
                "proposal_revision": revision,
                "work_id": pending.work_id,
                "requests": 0,
            },
        )
        if not isinstance(posted, dict) or posted.get("ok") is not True:
            raise ConsentUnavailable("could not post approved text")
        try:
            store.finalize_resumed(
                dm_bot, pending.pending_id, posted_marker=marker)
        except PrivateFollowupError:
            raise ConsentConflict("pending cannot be finalized")
        return {"ok": True, "status": "resumed"}
