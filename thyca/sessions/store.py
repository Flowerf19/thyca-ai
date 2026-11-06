from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path

from thyca.core.protocol import Message

from .errors import SessionCorrupt, SessionError, SessionNotFound
from .models import Session


def _require_legacy_writer_allowed(path: Path) -> None:
    """Fail closed when *path* sits under a fixture root switched to the store.

    Fixture-only Phase 2 routing: while a disposable fixture root is enabled
    via ``enable_fixture_writes``, legacy JSONL mutations under it are
    blocked so the two writers can never interleave on the same root. The
    live root is never enabled, so it keeps the legacy path. Lazy import
    keeps ``sessions`` free of a hard ``conversations`` dependency at
    module load."""
    try:
        from thyca.conversations.store.fixture_switch import is_legacy_writer_blocked
    except Exception:
        return
    try:
        blocked = is_legacy_writer_blocked(path)
    except ValueError:
        return
    if blocked:
        raise SessionError(f"legacy writer blocked: fixture root switched to conversation store: {path}")

#: Public conversation-door grammar (hard cut): ``group-<hex32>`` groups
#: and ``<bot-id>`` DMs only. ``ConversationRef`` enforces the same shape;
#: a timestamp id never names a public door.
SESSION_ID_PATTERN = r"(?:group-[0-9a-f]{32}|[a-z0-9]+(?:-[a-z0-9]+)*)"
#: Internal canonical-timeline grammar: per-bot execution sessions keep
#: timestamp ids (``WorkspaceStore._new_internal_session_id``). They are
#: a separate internal concept, never public doors, and only the file
#: listing below plus ``path_for`` bindings use them.
_LEGACY_TS_PATTERN = r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4}"
_LEGACY_TS_RE = re.compile(rf"^{_LEGACY_TS_PATTERN}$")
_ID_RE = re.compile(rf"^(?:{SESSION_ID_PATTERN}|{_LEGACY_TS_PATTERN})$")

# Serializes title check-and-set across every manager in this process: without
# it a sidebar rename landing between the auto-namer's check and its append
# loses to last-writer-wins. Leaf lock — never held while taking a manager
# lock. Single-process only; two processes sharing a sessions dir can still
# interleave (same assumption the per-manager lock already makes).
_TITLE_LOCK = threading.Lock()


def _runtime_root_for(sessions_dir: Path) -> Path | None:
    """Runtime store root when *sessions_dir* lives under an initialized store.

    Transition helper (2026-11-01): ``sessions_dir`` is ``root/"sessions"``
    for user rooms; the parent ``root`` holds ``conversations.db`` when
    the runtime path is active. Canonical timeline stores (``bots/...``)
    never count: their parents hold no conversation DB. Returns None for
    legacy file roots. Lazy import keeps ``sessions`` free of a hard
    ``conversations`` dependency at module load.
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
    """Durable JSONL I/O. No compaction policy."""

    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir

    def ensure_dir(self) -> None:
        _require_legacy_writer_allowed(self.sessions_dir)
        # Runtime roots keep an (empty) sessions dir for Session.path
        # compatibility; the conversation DB is the source of truth.
        # The fixture gate above still blocks legacy writes on switched
        # roots, so dual-write is impossible: runtime writes go to the
        # store (see create/append/delete below), never to these files.
        try:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            self.sessions_dir.chmod(0o700)
        except OSError as exc:
            raise SessionError(f"cannot secure {self.sessions_dir}: {exc}") from exc

    def path_for(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _ID_RE.fullmatch(session_id):
            raise SessionNotFound(session_id, f"invalid session id: {session_id!r}")
        root = self.sessions_dir.resolve()
        candidate = (self.sessions_dir / f"{session_id}.jsonl").resolve()
        if candidate.parent != root:
            raise SessionNotFound(candidate, "session id traversal blocked")
        return self.sessions_dir / f"{session_id}.jsonl"

    def create(self, session_id: str) -> Path:
        _require_legacy_writer_allowed(self.sessions_dir)
        runtime_root = _runtime_root_for(self.sessions_dir)
        if runtime_root is not None:
            # Runtime: group doors only (DMs bootstrap via ensure_dm).
            # Legacy timestamps and group-... both read as group doors
            # (transition adapter); bot ids must not mint via create.
            from thyca.conversations.store.runtime import RuntimeWriteIntent
            from thyca.core.conversation import ConversationRef

            try:
                ref = ConversationRef.group(session_id)
            except ValueError:
                raise SessionError(f"cannot create session: {session_id!r}") from None
            if ref.kind != "group":
                raise SessionError(f"cannot create session: {session_id!r}")
            # Specific-id group creation (transition): direct INSERT so
            # legacy timestamp ids keep resolving after the hard-cut.
            # New groups should still mint via AdminStore.create_group;
            # this path only serves explicit-id creates (e.g. tests).
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
                        # Match legacy FileExistsError for create-retry loops.
                        raise FileExistsError(str(path)) from exc
                    except sqlite3.Error as exc:
                        from thyca.conversations.store.contracts import (
                            ConversationStoreError,
                        )

                        raise ConversationStoreError() from exc
            finally:
                conn.close()
            return _runtime_session_path(self.sessions_dir, session_id)
        path = self.path_for(session_id)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            raise
        except OSError as exc:
            raise SessionError(f"cannot create session: {exc}") from exc
        self._chmod_best_effort(path)
        return path

    def delete(self, session_id: str) -> None:
        _require_legacy_writer_allowed(self.sessions_dir)
        runtime_root = _runtime_root_for(self.sessions_dir)
        if runtime_root is not None:
            from thyca.conversations.store.admin import AdminStore
            from thyca.conversations.store.runtime import resolve_ref
            from thyca.conversations.store.contracts import ConversationStoreError

            try:
                ref = resolve_ref(runtime_root, session_id)
            except ConversationStoreError as exc:
                # Missing door: legacy delete is idempotent (unlink
                # missing -> return). Match it: no mutation, no error.
                if getattr(exc, "scope", "global") == "local":
                    return
                raise SessionError("cannot delete session") from exc
            except ValueError:
                # Malformed id: legacy path_for would raise NotFound.
                # For runtime, treat as missing (idempotent).
                return
            try:
                AdminStore(runtime_root).delete(ref)
            except ConversationStoreError as exc:
                if getattr(exc, "scope", "global") == "local":
                    return
                raise SessionError("cannot delete session") from exc
            return
        path = self.path_for(session_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SessionError(f"cannot delete session: {exc}") from exc

    def read_title(self, path: Path) -> tuple[str, str | None] | None:
        """Last meta line's ``(title, source)``, read from the end of the file.

        The naming step needs only the title, and a full ``scan`` parses the
        whole transcript (tens of milliseconds on a long session). Meta lines
        are appended, so the last one wins — walking backwards from the tail
        stops after the first match.

        Returns None when the file has no meta line (or cannot be read), which
        callers treat as "leave the in-memory title alone".
        """
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
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
            except Exception:
                return None
        try:
            with path.open("rb") as stream:
                for raw in _lines_backwards(stream):
                    if b'"type"' not in raw or b"title" not in raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not _is_meta(payload):
                        continue
                    title = _meta_title(payload)
                    if title:
                        return title, _meta_source(payload)
        except OSError:
            return None
        return None

    def append_bots(self, path: Path, bots: list[str]) -> None:
        """Record the room's participant list (last meta line wins).

        Team rooms (BE-3): ``bots`` is the validated participant list.
        Shares ``_TITLE_LOCK`` with title appends so a concurrent rename
        cannot interleave between the check and the write (same process)."""
        cleaned = [bot for bot in bots if isinstance(bot, str) and bot]
        if not cleaned:
            raise SessionError("bots must be a non-empty list of bot ids")
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
            # Runtime: one atomic metadata update (title fields preserved).
            # Authorized by the server-side caller, never by a bot actor.
            from thyca.conversations.store.runtime import resolve_ref

            ref = resolve_ref(runtime_root, Path(path).stem)
            _runtime_mutate_metadata(
                runtime_root, ref,
                lambda meta: {**meta, "participants": list(cleaned)},
            )
            return
        with _TITLE_LOCK:
            self._append_json(path, _meta_payload(None, None, bots=cleaned))

    def append_dm(self, path: Path, bot_id: str) -> None:
        """Mark this session as ``bot_id``'s fixed DM (CBE-5)."""
        if not isinstance(bot_id, str) or not bot_id:
            raise SessionError("dm bot must be a non-empty bot id")
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
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
        with _TITLE_LOCK:
            self._append_json(path, _meta_payload(None, None, dm=bot_id))

    def read_dm(self, path: Path) -> str | None:
        """Last meta line's ``dm`` bot id, or None when never marked."""
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
            try:
                from thyca.conversations.store.runtime import resolve_ref

                ref = resolve_ref(runtime_root, Path(path).stem)
                return ref.id if ref.kind == "dm" else None
            except Exception:
                return None
        try:
            with path.open("rb") as stream:
                for raw in _lines_backwards(stream):
                    if b'"type"' not in raw or b"dm" not in raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not _is_meta(payload):
                        continue
                    dm = _meta_dm(payload)
                    if dm:
                        return dm
        except OSError:
            return None
        return None

    def read_bots(self, path: Path) -> list[str] | None:
        """Last meta line's ``bots`` list, or None when never recorded."""
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
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
            except Exception:
                return None
        try:
            with path.open("rb") as stream:
                for raw in _lines_backwards(stream):
                    if b'"type"' not in raw or b"bots" not in raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not _is_meta(payload):
                        continue
                    bots = _meta_bots(payload)
                    if bots:
                        return bots
        except OSError:
            return None
        return None

    def scan(self, path: Path) -> tuple[list[Message], str | None, str | None, bool]:
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
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
        result: list[Message] = []
        title: str | None = None
        title_source: str | None = None
        naming_attempted = False
        known_calls: set[str] = set()
        try:
            with path.open("r", encoding="utf-8") as stream:
                for number, raw_line in enumerate(stream, 1):
                    line = raw_line.rstrip("\n")
                    if not line.strip():
                        # Blank lines carry no information: skip so sessions
                        # self-heal instead of bricking the whole transcript.
                        continue
                    try:
                        payload = json.loads(line)
                        if _is_meta(payload):
                            if _meta_attempted(payload):
                                naming_attempted = True
                            extracted = _meta_title(payload)
                            if extracted:
                                title = extracted
                                title_source = _meta_source(payload)
                            continue
                        msg = Message.from_dict(payload)
                        if msg.role == "system" and (
                            result or not (msg.content or "").startswith("[compaction: ")
                        ):
                            raise ValueError(
                                "system messages are only synthetic compaction markers"
                            )
                        if msg.role == "tool" and (
                            not msg.tool_call_id or msg.tool_call_id not in known_calls
                        ):
                            raise ValueError(
                                "role=tool requires a matching prior assistant tool_call"
                            )
                        if msg.role == "assistant" and msg.tool_calls:
                            ids = [call.id for call in msg.tool_calls]
                            if len(ids) != len(set(ids)):
                                raise ValueError("assistant tool_call ids must be unique")
                            known_calls.update(ids)
                    except SessionCorrupt:
                        raise
                    except (json.JSONDecodeError, ValueError, TypeError) as exc:
                        raise SessionCorrupt(path, number, exc) from exc
                    result.append(msg)
        except SessionCorrupt:
            raise
        except UnicodeDecodeError as exc:
            raise SessionCorrupt(path, None, "invalid UTF-8") from exc
        except FileNotFoundError as exc:
            raise SessionNotFound(path) from exc
        except OSError as exc:
            raise SessionCorrupt(path, None, exc) from exc
        return result, title, title_source, naming_attempted

    def load(self, session_id: str) -> Session:
        runtime_root = _runtime_root_for(self.sessions_dir)
        if runtime_root is not None:
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
        path = self.path_for(session_id)
        if not path.is_file() or path.is_symlink():
            raise SessionNotFound(path)
        messages, title, title_source, naming_attempted = self.scan(path)
        return Session(
            session_id, path, messages, title, title_source, naming_attempted,
            self.read_bots(path), self.read_dm(path),
        )

    def list_paths(self) -> list[Path]:
        runtime_root = _runtime_root_for(self.sessions_dir)
        if runtime_root is not None:
            # Runtime: newest-first store listing projected as identifier
            # paths (no files required). Order mirrors AdminStore.list
            # (created_at desc, kind/id asc) so legacy newest-first holds.
            from thyca.conversations.store.admin import AdminStore

            try:
                items, _ = AdminStore(runtime_root).list(limit=5000)
            except Exception:
                return []
            return [_runtime_session_path(self.sessions_dir, row.ref.id) for row in items]
        if not self.sessions_dir.is_dir():
            return []
        candidates = [
            path
            for path in self.sessions_dir.glob("*.jsonl")
            if path.is_file() and not path.is_symlink() and _LEGACY_TS_RE.fullmatch(path.stem)
        ]
        candidates.sort(key=lambda path: (_mtime_ns(path), path.name), reverse=True)
        return candidates

    def latest(self) -> Session:
        runtime_root = _runtime_root_for(self.sessions_dir)
        if runtime_root is not None:
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
        candidates = self.list_paths()
        if not candidates:
            raise SessionNotFound(self.sessions_dir)
        for chosen in candidates:
            session_id = chosen.stem
            try:
                messages, title, title_source, naming_attempted = self.scan(chosen)
            except (SessionCorrupt, SessionNotFound):
                continue
            return Session(
                session_id, chosen, messages, title, title_source, naming_attempted,
                self.read_bots(chosen), self.read_dm(chosen),
            )
        raise SessionNotFound(self.sessions_dir, "no valid sessions")

    # Append one public row, threading the acting bot when a bot acts.
    def append(self, path: Path, msg: Message, *, actor: str | None = None) -> None:
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
            # Runtime: plain user/assistant rows go to the store; sidecar
            # and tool rows never persist (attempt flag / timeline own them).
            from thyca.sessions.wire import is_sidecar_message

            if is_sidecar_message(msg):
                return
            if msg.role not in ("user", "assistant"):
                # System error markers route via mark_turn_error (direct
                # SQL trailing row); tool rows belong to the canonical
                # timeline, never the public store. Drop silently to keep
                # the public transcript lean (legacy would brick here).
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
        self._append_json(path, msg.to_canonical_dict())

    # Store one title as door metadata for the server-side caller.
    def append_meta(self, path: Path, title: str, source: str | None = None) -> None:
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
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
        with _TITLE_LOCK:
            self._append_json(path, _meta_payload(title, source))

    # Store an automatic title only when the door still has none.
    def append_title_if_missing(
        self, path: Path, title: str, source: str | None = None,
        *, actor: str | None = None,
    ) -> bool:
        """Append a title meta line only when the file carries no title.

        The check and the append share ``_TITLE_LOCK`` with every other
        title append, so a concurrent rename cannot slip between them
        (same process). Returns False when a title was already on disk and
        nothing was written.
        """
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
            # Runtime: atomic title-if-missing via the store (turn_pipeline
            # semantics: concurrent rename wins, blank titles store nothing).
            # The naming bot is the actor; membership checks inside the
            # store transaction, never from a pre-read participant list.
            from thyca.conversations.store.runtime import (
                RuntimeWriteIntent,
                resolve_ref,
            )
            from thyca.conversations.store.turn_pipeline import (
                store_fixture_title_if_missing as _store_title_if_missing,
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
        with _TITLE_LOCK:
            found = self.read_title(path)
            if found is not None and found[0]:
                return False
            self._append_json(path, _meta_payload(title, source))
            return True

    # Record the naming step's one attempt for the acting bot.
    def append_naming_attempted(self, path: Path, *, actor: str | None = None) -> None:
        """Record the automatic naming step's one attempt, with no title."""
        runtime_root = _runtime_root_for(Path(path).parent)
        if runtime_root is not None:
            # The naming bot is the actor; membership checks inside the
            # store transaction via set_conversation_metadata.
            from thyca.conversations.store.runtime import (
                RuntimeWriteIntent,
                resolve_ref,
            )
            from thyca.conversations.store.turn_pipeline import (
                mark_fixture_naming_attempted as _mark_attempted,
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
        self._append_json(path, _meta_payload(None, None, naming_attempted=True))

    def _append_json(self, path: Path, payload: dict) -> None:
        # Runtime roots never take file appends: the store is the truth.
        # This fail-closed guard prevents dual-write even when the fixture
        # switch is off (runtime-only roots). Fixture tests keep asserting
        # the legacy-writer-blocked error on switched roots via the gate
        # below, so check runtime first for a clear store error.
        if _runtime_root_for(Path(path).parent) is not None:
            raise SessionError(
                f"legacy writer blocked: runtime store owns this root: {path}"
            )
        _require_legacy_writer_allowed(path)
        try:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise SessionError(f"append failed: {exc}") from exc
        self._chmod_best_effort(path)

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
        # Fixture gate first so switched-root tests keep asserting the
        # legacy-blocked error; runtime-only roots then fail closed below.
        # Runtime store is append-only: history rewrites never go through
        # file rewrites (SessionManager branches to store-native paths).
        _require_legacy_writer_allowed(self.sessions_dir)
        _require_legacy_writer_allowed(target)
        if _runtime_root_for(self.sessions_dir) is not None:
            raise SessionError("rewrite not supported on conversation store")
        if _runtime_root_for(Path(target).parent) is not None:
            raise SessionError("rewrite not supported on conversation store")
        path = self.path_for(session_id)
        if path.resolve() != target.resolve():
            raise SessionError("rewrite target does not match session id")
        if bots is None:
            # Preserve the room's participant list across compactions and
            # retries: the rewrite rebuilds the meta line from scratch.
            bots = self.read_bots(target)
        if dm is None:
            # Same preservation for the DM marker (CBE-5).
            dm = self.read_dm(target)
        tmp = self.sessions_dir / f".{path.stem}.tmp.{secrets.token_hex(4)}"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    if title or naming_attempted or bots or dm:
                        meta = _meta_payload(
                            title, title_source, naming_attempted=naming_attempted,
                            bots=bots, dm=dm,
                        )
                        stream.write(
                            json.dumps(meta, ensure_ascii=False) + "\n"
                        )
                    for msg in messages:
                        stream.write(json.dumps(msg.to_canonical_dict(), ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except (TypeError, ValueError) as exc:
                raise SessionError(f"compaction rewrite failed: {exc}") from exc
            self._chmod_best_effort(tmp)
            os.replace(tmp, path)
        except OSError as exc:
            raise SessionError(f"compaction rewrite failed: {exc}") from exc
        finally:
            # One cleanup path: a failed write never leaves its tmp behind
            # (on success the rename already moved it away, so this no-ops).
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        self._fsync_dir_best_effort()
        self._chmod_best_effort(path)

    def _chmod_best_effort(self, path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _fsync_dir_best_effort(self) -> None:
        try:
            parent_fd = os.open(self.sessions_dir, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(parent_fd)
        except OSError:
            pass
        finally:
            os.close(parent_fd)


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _lines_backwards(stream, chunk_size: int = 8192):
    """Yield the file's lines newest-first without loading it whole."""
    stream.seek(0, os.SEEK_END)
    position = stream.tell()
    pending = b""
    while position > 0:
        read_size = min(chunk_size, position)
        position -= read_size
        stream.seek(position)
        pending = stream.read(read_size) + pending
        parts = pending.split(b"\n")
        pending = parts[0]
        for line in reversed(parts[1:]):
            if line.strip():
                yield line
    if pending.strip():
        yield pending


def _meta_payload(
    title: str | None, source: str | None, *, naming_attempted: bool = False,
    bots: list[str] | None = None, dm: str | None = None,
) -> dict:
    """The one meta-line builder shared by append and rewrite."""
    payload: dict = {"type": "meta"}
    if title:
        payload["title"] = title
        if source:
            payload["source"] = source
    if naming_attempted:
        payload["naming_attempted"] = True
    if bots:
        payload["bots"] = list(bots)
    if dm:
        payload["dm"] = dm
    return payload


def _meta_dm(payload: dict) -> str | None:
    raw = payload.get("dm")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _is_meta(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("type") == "meta" and "role" not in payload


def _meta_title(payload: dict) -> str | None:
    raw = payload.get("title")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def _meta_source(payload: dict) -> str | None:
    raw = payload.get("source")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _meta_attempted(payload: dict) -> bool:
    return payload.get("naming_attempted") is True


def _meta_bots(payload: dict) -> list[str] | None:
    raw = payload.get("bots")
    if not isinstance(raw, list) or not raw:
        return None
    bots = [item for item in raw if isinstance(item, str) and item]
    return bots or None
