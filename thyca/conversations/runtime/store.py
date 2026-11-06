from __future__ import annotations

import re
from pathlib import Path

from thyca.core.protocol import Message

from .errors import SessionCorrupt, SessionError, SessionNotFound
from .models import Session

#: Public conversation-door grammar (hard cut): ``group-<hex32>`` groups
#: and ``<bot-id>`` DMs only. ``ConversationRef`` enforces the same shape;
#: a timestamp id never names a public door.
SESSION_ID_PATTERN = r"(?:group-[0-9a-f]{32}|[a-z0-9]+(?:-[a-z0-9]+)*)"
_ID_RE = re.compile(rf"^(?:{SESSION_ID_PATTERN})$")


# Find the runtime store root for a sessions directory, or None.
def _runtime_root_for(sessions_dir: Path) -> Path | None:
    """Runtime store root when *sessions_dir* lives under an initialized store.

    ``sessions_dir`` is ``root/"sessions"`` for user rooms; the parent
    ``root`` holds ``conversations.db`` when the runtime path is active.
    Canonical timeline stores (``bots/...``) never count: their parents
    hold no conversation DB. Returns None when no runtime store exists.
    Lazy import keeps ``sessions`` free of a hard ``conversations``
    dependency at module load.
    """
    try:
        from thyca.conversations.store.runtime import is_runtime_ready
    except Exception:
        return None
    try:
        sd = Path(sessions_dir)
        candidates = []
        if sd.name == "sessions":
            candidates.append(sd.parent)
        candidates.append(sd)
        for cand in candidates:
            try:
                if is_runtime_ready(cand):
                    return cand
            except Exception:
                continue
    except Exception:
        return None
    return None


# Require the runtime store root for a public sessions directory.
def _require_runtime_root(sessions_dir: Path) -> Path:
    root = _runtime_root_for(sessions_dir)
    if root is None:
        raise SessionError("runtime store not initialized for this root")
    return root


# Build the stable identifier path for a runtime session.
def _runtime_session_path(sessions_dir: Path, session_id: str) -> Path:
    """Stable identifier path for a runtime session (no file required)."""
    return Path(sessions_dir) / f"{session_id}.jsonl"


# Apply one metadata change inside a single store transaction.
def _runtime_mutate_metadata(runtime_root: Path, ref, transform) -> dict:
    """Read, change, and store door metadata atomically (server-side).

    The caller is the trusted server-side ``ChatApp`` flow (a user rename
    or membership edit), so no bot membership authorizes this write and
    no bot actor is claimed. Root and door readiness are still checked
    inside the same ``BEGIN IMMEDIATE`` transaction as the update.
    """
    import sqlite3

    from thyca.conversations.store.contracts import (
        _encode_metadata,
        _local_error,
        validate_metadata,
    )
    from thyca.conversations.store.database import (
        _ensure_door_ready,
        _ensure_root_ready,
        _root_instance_id_from_conn,
        _transaction_context,
        open_existing_session,
    )

    conn = open_existing_session(runtime_root, lock_mode="exclusive")
    try:
        with _transaction_context(conn, "BEGIN IMMEDIATE"):
            _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
            metadata, _gen, _ev, _log = _ensure_door_ready(conn, ref)
            updated = transform(dict(metadata))
            validate_metadata(ref, updated)
            try:
                cur = conn.execute(
                    "UPDATE conversations SET metadata=? WHERE kind=? AND id=?",
                    (_encode_metadata(updated), ref.kind, ref.id),
                )
                if cur.rowcount != 1:
                    raise _local_error(ref)
            except sqlite3.Error as exc:
                raise _local_error(ref) from exc
            return updated
    finally:
        conn.close()


class SessionStore:
    """Conversation-store backed session I/O. No compaction policy."""

    # Remember the sessions directory that names this store's doors.
    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir

    # Create the sessions directory that holds door binding paths.
    def ensure_dir(self) -> None:
        # Runtime roots keep an (empty) sessions dir for Session.path
        # compatibility; the conversation DB is the source of truth.
        try:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            self.sessions_dir.chmod(0o700)
        except OSError as exc:
            raise SessionError(f"cannot secure {self.sessions_dir}: {exc}") from exc

    # Resolve one public door id to its binding path without touching disk.
    def path_for(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _ID_RE.fullmatch(session_id):
            raise SessionNotFound(session_id, f"invalid session id: {session_id!r}")
        root = self.sessions_dir.resolve()
        candidate = (self.sessions_dir / f"{session_id}.jsonl").resolve()
        if candidate.parent != root:
            raise SessionNotFound(candidate, "session id traversal blocked")
        return self.sessions_dir / f"{session_id}.jsonl"

    # Create one explicit-id group door in the conversation store.
    def create(self, session_id: str) -> Path:
        runtime_root = _require_runtime_root(self.sessions_dir)
        # Runtime: group doors only (DMs bootstrap via ensure_dm).
        from thyca.core.conversation import ConversationRef

        try:
            ref = ConversationRef.group(session_id)
        except ValueError:
            raise SessionError(f"cannot create session: {session_id!r}") from None
        if ref.kind != "group":
            raise SessionError(f"cannot create session: {session_id!r}")
        # Specific-id group creation: direct INSERT for explicit-id
        # creates (e.g. tests). New groups should still mint via
        # AdminStore.create_group.
        import sqlite3

        from thyca.conversations.store.database import (
            _ensure_root_ready,
            _root_instance_id_from_conn,
            _transaction_context,
            open_existing_session,
        )
        from thyca.conversations.store.contracts import (
            _encode_metadata,
            _metadata,
        )
        from thyca.core.protocol import utc_now_ts

        metadata = _metadata(["thyca"])
        conn = open_existing_session(runtime_root, lock_mode="exclusive")
        try:
            with _transaction_context(conn, "BEGIN IMMEDIATE"):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                try:
                    conn.execute(
                        "INSERT INTO conversations(kind,id,metadata,created_at,"
                        "lifecycle_state,generation,event_high_water,logical_high_water) "
                        "VALUES('group',?,?,?,'ready',0,0,0)",
                        (ref.id, _encode_metadata(metadata), utc_now_ts()),
                    )
                except sqlite3.IntegrityError as exc:
                    # Match the old FileExistsError for create-retry loops.
                    raise FileExistsError(session_id) from exc
                except sqlite3.Error as exc:
                    from thyca.conversations.store.contracts import (
                        ConversationStoreError,
                    )

                    raise ConversationStoreError() from exc
        finally:
            conn.close()
        return _runtime_session_path(self.sessions_dir, session_id)

    # Delete one door from the conversation store, idempotently.
    def delete(self, session_id: str) -> None:
        runtime_root = _require_runtime_root(self.sessions_dir)
        from thyca.conversations.store.admin import AdminStore
        from thyca.conversations.store.runtime import resolve_ref
        from thyca.conversations.store.contracts import ConversationStoreError

        try:
            ref = resolve_ref(runtime_root, session_id)
        except ConversationStoreError as exc:
            # Missing door: delete stays idempotent (no mutation, no error).
            if getattr(exc, "scope", "global") == "local":
                return
            raise SessionError("cannot delete session") from exc
        except ValueError:
            # Malformed id: treat as missing (idempotent).
            return
        try:
            AdminStore(runtime_root).delete(ref)
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                return
            raise SessionError("cannot delete session") from exc
        return

    # Read the stored title for the door named by a binding path.
    def read_title(self, path: Path) -> tuple[str, str | None] | None:
        """Stored ``(title, source)`` for one door, or None when untitled.

        Returns None when the door carries no title (or cannot be read),
        which callers treat as "leave the in-memory title alone".
        """
        runtime_root = _require_runtime_root(Path(path).parent)
        try:
            from thyca.conversations.store.runtime import resolve_ref
            from thyca.conversations.store.database import (
                _ensure_root_ready,
                _root_instance_id_from_conn,
                _transaction_context,
                open_existing_session,
            )
            from thyca.conversations.store.contracts import _decode_metadata

            ref = resolve_ref(runtime_root, Path(path).stem)
            conn = open_existing_session(runtime_root, lock_mode="shared")
            try:
                with _transaction_context(conn, "BEGIN"):
                    _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                    row = conn.execute(
                        "SELECT metadata FROM conversations WHERE kind=? AND id=?",
                        (ref.kind, ref.id),
                    ).fetchone()
            finally:
                conn.close()
            if row is None:
                return None
            metadata = _decode_metadata(row[0])
            title = metadata.get("title")
            if title:
                return title, metadata.get("title_source")
            return None
        except SessionError:
            raise
        except Exception:
            return None

    # Record the room's participant list as door metadata.
    def append_bots(self, path: Path, bots: list[str]) -> None:
        """Record the room's participant list (stored metadata wins)."""
        cleaned = [bot for bot in bots if isinstance(bot, str) and bot]
        if not cleaned:
            raise SessionError("bots must be a non-empty list of bot ids")
        runtime_root = _require_runtime_root(Path(path).parent)
        # Runtime: one atomic metadata update (title fields preserved).
        # Authorized by the server-side caller, never by a bot actor.
        from thyca.conversations.store.runtime import resolve_ref

        ref = resolve_ref(runtime_root, Path(path).stem)
        _runtime_mutate_metadata(
            runtime_root, ref,
            lambda meta: {**meta, "participants": list(cleaned)},
        )
        return

    # Mark a door as one bot's fixed DM, refusing group conversion.
    def append_dm(self, path: Path, bot_id: str) -> None:
        """Mark this session as ``bot_id``'s fixed DM (CBE-5)."""
        if not isinstance(bot_id, str) or not bot_id:
            raise SessionError("dm bot must be a non-empty bot id")
        runtime_root = _require_runtime_root(Path(path).parent)
        # Runtime DM doors bootstrap via AdminStore; a group door can
        # never become a DM (DM integrity). Idempotent when the door
        # is already this bot's DM, otherwise refuse.
        from thyca.conversations.store.runtime import resolve_ref
        from thyca.conversations.store.contracts import ConversationStoreError

        try:
            ref = resolve_ref(runtime_root, Path(path).stem)
        except ConversationStoreError as exc:
            raise SessionError("cannot mark DM") from exc
        if ref.kind == "dm" and ref.id == bot_id:
            return
        raise SessionError("cannot convert group to DM on conversation store")

    # Read which bot owns the DM door named by a binding path.
    def read_dm(self, path: Path) -> str | None:
        """The DM owner bot id for one door, or None when not a DM."""
        runtime_root = _require_runtime_root(Path(path).parent)
        try:
            from thyca.conversations.store.runtime import resolve_ref

            ref = resolve_ref(runtime_root, Path(path).stem)
            return ref.id if ref.kind == "dm" else None
        except SessionError:
            raise
        except Exception:
            return None

    # Read the participant list stored for one door.
    def read_bots(self, path: Path) -> list[str] | None:
        """Stored participant list for one door, or None when unset."""
        runtime_root = _require_runtime_root(Path(path).parent)
        try:
            from thyca.conversations.store.runtime import resolve_ref
            from thyca.conversations.store.database import (
                _ensure_root_ready,
                _root_instance_id_from_conn,
                _transaction_context,
                open_existing_session,
            )
            from thyca.conversations.store.contracts import _decode_metadata

            ref = resolve_ref(runtime_root, Path(path).stem)
            conn = open_existing_session(runtime_root, lock_mode="shared")
            try:
                with _transaction_context(conn, "BEGIN"):
                    _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                    row = conn.execute(
                        "SELECT metadata FROM conversations WHERE kind=? AND id=?",
                        (ref.kind, ref.id),
                    ).fetchone()
            finally:
                conn.close()
            if row is None:
                return None
            participants = _decode_metadata(row[0]).get("participants")
            return list(participants) if participants else None
        except SessionError:
            raise
        except Exception:
            return None

    # Assemble the full stored history for one door.
    def scan(self, path: Path) -> tuple[list[Message], str | None, str | None, bool]:
        runtime_root = _require_runtime_root(Path(path).parent)
        # Runtime: full history via the store (cursor-paged, resync-safe).
        from thyca.conversations.store.read_adapter import assemble_history
        from thyca.conversations.store.runtime import RuntimeReadIntent, resolve_ref

        ref = resolve_ref(runtime_root, Path(path).stem)
        # Actor is store truth (first participant / DM owner).
        from thyca.conversations.store.database import (
            _ensure_root_ready,
            _root_instance_id_from_conn,
            _transaction_context,
            open_existing_session,
        )
        from thyca.conversations.store.contracts import _decode_metadata

        conn = open_existing_session(runtime_root, lock_mode="shared")
        try:
            with _transaction_context(conn, "BEGIN"):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                row = conn.execute(
                    "SELECT metadata FROM conversations WHERE kind=? AND id=?",
                    (ref.kind, ref.id),
                ).fetchone()
        finally:
            conn.close()
        if row is None:
            from thyca.conversations.store.contracts import ConversationStoreError

            raise ConversationStoreError("conversation unavailable")
        participants = _decode_metadata(row[0]).get("participants", [])
        # Read-side member only: satisfies the read membership check.
        actor = ref.id if ref.kind == "dm" else (participants[0] if participants else "thyca")
        conv = assemble_history(
            runtime_root, ref, actor,
            intent=RuntimeReadIntent("runtime-scan"),
        )
        return list(conv.messages), conv.title, conv.title_source, conv.naming_attempted

    # Load one door as a Session from the conversation store.
    def load(self, session_id: str) -> Session:
        runtime_root = _runtime_root_for(self.sessions_dir)
        if runtime_root is None:
            # Not found (a SessionError): missing-door callers such as the
            # bot runtime and memory visibility keep their fallback.
            raise SessionNotFound(session_id, "runtime store not initialized for this root")
        from thyca.conversations.store.runtime import resolve_ref
        from thyca.conversations.store.contracts import ConversationStoreError

        try:
            ref = resolve_ref(runtime_root, session_id)
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                raise SessionNotFound(session_id) from exc
            raise SessionCorrupt(session_id, None, exc) from exc
        except ValueError as exc:
            raise SessionNotFound(session_id) from exc
        path = _runtime_session_path(self.sessions_dir, session_id)
        try:
            messages, title, title_source, naming_attempted = self.scan(path)
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                raise SessionNotFound(session_id) from exc
            raise SessionCorrupt(path, None, exc) from exc
        return Session(
            session_id, path, messages, title, title_source, naming_attempted,
            self.read_bots(path), self.read_dm(path),
        )

    # List binding paths for every stored door, newest first.
    def list_paths(self) -> list[Path]:
        runtime_root = _require_runtime_root(self.sessions_dir)
        # Runtime: newest-first store listing projected as identifier
        # paths (no files required). Order mirrors AdminStore.list
        # (created_at desc, kind/id asc).
        from thyca.conversations.store.admin import AdminStore

        try:
            items, _ = AdminStore(runtime_root).list(limit=5000)
        except Exception:
            return []
        return [_runtime_session_path(self.sessions_dir, row.ref.id) for row in items]

    # Load the newest stored door, skipping any that fail to read.
    def latest(self) -> Session:
        _require_runtime_root(self.sessions_dir)
        candidates = self.list_paths()
        if not candidates:
            raise SessionNotFound(self.sessions_dir)
        # Newest-first already; first loadable wins (skip corrupt).
        for chosen in candidates:
            try:
                return self.load(chosen.stem)
            except (SessionCorrupt, SessionNotFound):
                continue
        raise SessionNotFound(self.sessions_dir, "no valid sessions")

    # Append one public row, threading the acting bot when a bot acts.
    def append(self, path: Path, msg: Message, *, actor: str | None = None) -> None:
        runtime_root = _require_runtime_root(Path(path).parent)
        # Runtime: plain user/assistant rows go to the store; sidecar
        # and tool rows never persist (attempt flag / timeline own them).
        from thyca.conversations.runtime.wire import is_sidecar_message

        if is_sidecar_message(msg):
            return
        if msg.role not in ("user", "assistant"):
            # System error markers route via mark_turn_error (direct
            # SQL trailing row); tool rows belong to the canonical
            # timeline, never the public store. Drop silently to keep
            # the public transcript lean.
            return
        if msg.tool_calls is not None or msg.tool_call_id is not None:
            return
        if msg.reasoning or msg.reasoning_details:
            return
        if not isinstance(msg.content, str):
            return
        from thyca.conversations.store.runtime import (
            RuntimeWriteIntent,
            resolve_ref,
        )
        from thyca.conversations.store.write_adapter import (
            append_message,
            append_server_message,
            append_server_row,
        )

        ref = resolve_ref(runtime_root, Path(path).stem)
        # Acting bot: explicit caller first, then the bot stamped on
        # the row itself, then the DM owner (sole member). A group
        # assistant row with no bot identity cannot be attributed.
        acting = actor
        if acting is None:
            meta_bot = (msg.meta or {}).get("bot_id") if isinstance(msg.meta, dict) else None
            if isinstance(meta_bot, str) and meta_bot:
                acting = meta_bot
        if acting is None and ref.kind == "dm":
            acting = ref.id
        _meta = msg.meta if isinstance(msg.meta, dict) else {}
        _is_a2a_display = msg.role == "assistant" and (
            _meta.get("bus_event") is True or _meta.get("kind") == "result"
        )
        try:
            if _is_a2a_display:
                # Server-recorded A2A pair display: the delivery service
                # gated sender/recipient ownership; no bot membership is
                # claimed for the non-owner side of a DM.
                append_server_row(
                    runtime_root, ref, msg,
                    intent=RuntimeWriteIntent("runtime-append"),
                )
            elif msg.role == "assistant":
                if acting is None:
                    raise SessionError("cannot append: no acting bot")
                # Membership is checked inside the append transaction.
                append_message(
                    runtime_root, ref, acting, msg,
                    intent=RuntimeWriteIntent("runtime-append"),
                )
            elif acting is not None:
                # Turn flow records the user row as the responding bot.
                append_message(
                    runtime_root, ref, acting, msg,
                    intent=RuntimeWriteIntent("runtime-append"),
                )
            else:
                # No bot acts on a user row: the trusted server-side
                # caller authorizes it; no membership is claimed.
                append_server_message(
                    runtime_root, ref, msg,
                    intent=RuntimeWriteIntent("runtime-append"),
                )
        except Exception as exc:
            from thyca.conversations.store.contracts import ConversationStoreError

            if isinstance(exc, ConversationStoreError):
                raise SessionError("cannot append") from exc
            raise
        return

    # Store one title as door metadata for the server-side caller.
    def append_meta(self, path: Path, title: str, source: str | None = None) -> None:
        runtime_root = _require_runtime_root(Path(path).parent)
        # Runtime: titles are metadata. User renames are authorized
        # by the server-side caller; no bot actor is claimed.
        from thyca.conversations.store.runtime import resolve_ref

        ref = resolve_ref(runtime_root, Path(path).stem)
        try:
            _runtime_mutate_metadata(
                runtime_root, ref,
                lambda meta: {**meta, "title": title, "title_source": source},
            )
        except Exception as exc:
            from thyca.conversations.store.contracts import ConversationStoreError

            if isinstance(exc, ConversationStoreError):
                raise SessionError("cannot store title") from exc
            raise
        return

    # Store an automatic title only when the door still has none.
    def append_title_if_missing(
        self, path: Path, title: str, source: str | None = None,
        *, actor: str | None = None,
    ) -> bool:
        """Store a title only when the door still has none.

        Returns False when a title was already stored and nothing was
        written.
        """
        runtime_root = _require_runtime_root(Path(path).parent)
        # Runtime: atomic title-if-missing via the store (turn_pipeline
        # semantics: concurrent rename wins, blank titles store nothing).
        # The naming bot is the actor; membership checks inside the
        # store transaction, never from a pre-read participant list.
        from thyca.conversations.store.runtime import (
            RuntimeWriteIntent,
            resolve_ref,
        )
        from thyca.conversations.store.turn_pipeline import (
            store_title_if_missing as _store_title_if_missing,
        )

        ref = resolve_ref(runtime_root, Path(path).stem)
        acting = actor if actor is not None else (ref.id if ref.kind == "dm" else None)
        if acting is None:
            raise SessionError("cannot store title: no acting bot")
        stored = _store_title_if_missing(
            runtime_root, ref,
            intent=RuntimeWriteIntent("runtime-title-if-missing"),
            actor=acting, title=title,
        )
        return stored is not None

    # Record the naming step's one attempt for the acting bot.
    def append_naming_attempted(self, path: Path, *, actor: str | None = None) -> None:
        """Record the automatic naming step's one attempt, with no title."""
        runtime_root = _require_runtime_root(Path(path).parent)
        # The naming bot is the actor; membership checks inside the
        # store transaction via set_conversation_metadata.
        from thyca.conversations.store.runtime import (
            RuntimeWriteIntent,
            resolve_ref,
        )
        from thyca.conversations.store.turn_pipeline import (
            mark_naming_attempted as _mark_attempted,
        )

        ref = resolve_ref(runtime_root, Path(path).stem)
        acting = actor if actor is not None else (ref.id if ref.kind == "dm" else None)
        if acting is None:
            raise SessionError("cannot mark naming: no acting bot")
        _mark_attempted(
            runtime_root, ref,
            intent=RuntimeWriteIntent("runtime-naming-attempted"),
            actor=acting,
        )
        return

    # Refuse history rewrites on the append-only conversation store.
    def rewrite(
        self,
        session_id: str,
        target: Path,
        messages: list[Message],
        title: str | None = None,
        title_source: str | None = None,
        naming_attempted: bool = False,
        bots: list[str] | None = None,
        dm: str | None = None,
    ) -> None:
        # Runtime store is append-only: history rewrites never go
        # through here (SessionManager branches to store-native paths;
        # CanonicalSessionStore overrides this for timeline journals).
        raise SessionError("rewrite not supported on conversation store")
