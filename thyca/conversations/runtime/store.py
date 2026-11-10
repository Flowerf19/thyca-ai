from __future__ import annotations

import re
from pathlib import Path

from thyca.core.protocol import Message

from .errors import ConversationCorrupt, ConversationError, ConversationNotFound
from .models import ConversationRuntime

#: Public conversation-door grammar (hard cut): ``group-<hex32>`` groups
#: and ``<bot-id>`` DMs only. ``ConversationRef`` enforces the same shape;
#: a timestamp id never names a public door.
CONVERSATION_ID_PATTERN = r"(?:group-[0-9a-f]{32}|[a-z0-9]+(?:-[a-z0-9]+)*)"
_ID_RE = re.compile(rf"^(?:{CONVERSATION_ID_PATTERN})$")


# Find the runtime store root for a conversations directory, or None.
def _runtime_root_for(conversations_dir: Path) -> Path | None:
    """Runtime store root when *conversations_dir* lives under an initialized store.

    ``conversations_dir`` is ``root/"conversations"`` for user rooms; the parent
    ``root`` holds ``conversations.db`` when the runtime path is active.
    Canonical timeline stores (``bots/...``) never count: their parents
    hold no conversation DB. Returns None when no runtime store exists.
    Lazy import keeps ``conversations`` free of a hard ``conversations``
    dependency at module load.
    """
    try:
        from thyca.conversations.store.runtime import is_runtime_ready
    except Exception:
        return None
    try:
        sd = Path(conversations_dir)
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


# Require the runtime store root for a public conversations directory.
def _require_runtime_root(conversations_dir: Path) -> Path:
    root = _runtime_root_for(conversations_dir)
    if root is None:
        raise ConversationError("runtime store not initialized for this root")
    return root


# Build the stable identifier path for a runtime conversation.
def _runtime_conversation_path(conversations_dir: Path, conversation_id: str) -> Path:
    """Stable identifier path for a runtime conversation (no file required)."""
    return Path(conversations_dir) / f"{conversation_id}.jsonl"


# Apply one metadata change inside a single store transaction.
def _runtime_mutate_metadata(runtime_root: Path, ref, transform) -> dict:
    """Read, change, and store door metadata atomically (server-side).

    The caller is the trusted server-side ``ChatApp`` flow (a membership
    edit), so no bot membership authorizes this write and
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
        open_existing_conversation,
    )

    conn = open_existing_conversation(runtime_root, lock_mode="exclusive")
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


class ConversationStore:
    """Conversation-store backed conversation I/O. No compaction policy."""

    # Remember the conversations directory that names this store's doors.
    def __init__(self, conversations_dir: Path) -> None:
        self.conversations_dir = conversations_dir

    # Create the conversations directory that holds door binding paths.
    def ensure_dir(self) -> None:
        # Runtime roots keep an (empty) conversations dir for ConversationRuntime.path
        # compatibility; the conversation DB is the source of truth.
        try:
            self.conversations_dir.mkdir(parents=True, exist_ok=True)
            self.conversations_dir.chmod(0o700)
        except OSError as exc:
            raise ConversationError(f"cannot secure {self.conversations_dir}: {exc}") from exc

    # Resolve one public door id to its binding path without touching disk.
    def path_for(self, conversation_id: str) -> Path:
        if not isinstance(conversation_id, str) or not _ID_RE.fullmatch(conversation_id):
            raise ConversationNotFound(conversation_id, f"invalid conversation id: {conversation_id!r}")
        root = self.conversations_dir.resolve()
        candidate = (self.conversations_dir / f"{conversation_id}.jsonl").resolve()
        if candidate.parent != root:
            raise ConversationNotFound(candidate, "conversation id traversal blocked")
        return self.conversations_dir / f"{conversation_id}.jsonl"

    # Create one explicit-id group door in the conversation store.
    def create(self, conversation_id: str) -> Path:
        runtime_root = _require_runtime_root(self.conversations_dir)
        # Runtime: group doors only (DMs bootstrap via ensure_dm).
        from thyca.core.conversation import ConversationRef

        try:
            ref = ConversationRef.group(conversation_id)
        except ValueError:
            raise ConversationError(f"cannot create conversation: {conversation_id!r}") from None
        if ref.kind != "group":
            raise ConversationError(f"cannot create conversation: {conversation_id!r}")
        # Specific-id group creation: direct INSERT for explicit-id
        # creates (e.g. tests). New groups should still mint via
        # AdminStore.create_group.
        import sqlite3

        from thyca.conversations.store.database import (
            _ensure_root_ready,
            _root_instance_id_from_conn,
            _transaction_context,
            open_existing_conversation,
        )
        from thyca.conversations.store.contracts import (
            _encode_metadata,
            _metadata,
        )
        from thyca.core.protocol import utc_now_ts

        metadata = _metadata(["thyca"])
        conn = open_existing_conversation(runtime_root, lock_mode="exclusive")
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
                    raise FileExistsError(conversation_id) from exc
                except sqlite3.Error as exc:
                    from thyca.conversations.store.contracts import (
                        ConversationStoreError,
                    )

                    raise ConversationStoreError() from exc
        finally:
            conn.close()
        return _runtime_conversation_path(self.conversations_dir, conversation_id)

    # Delete one door from the conversation store, idempotently.
    def delete(self, conversation_id: str) -> None:
        runtime_root = _require_runtime_root(self.conversations_dir)
        from thyca.conversations.store.admin import AdminStore
        from thyca.conversations.store.runtime import resolve_ref
        from thyca.conversations.store.contracts import ConversationStoreError

        try:
            ref = resolve_ref(runtime_root, conversation_id)
        except ConversationStoreError as exc:
            # Missing door: delete stays idempotent (no mutation, no error).
            if getattr(exc, "scope", "global") == "local":
                return
            raise ConversationError("cannot delete conversation") from exc
        except ValueError:
            # Malformed id: treat as missing (idempotent).
            return
        try:
            AdminStore(runtime_root).delete(ref)
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                return
            raise ConversationError("cannot delete conversation") from exc
        return

    # Record the room's participant list as door metadata.
    def append_bots(self, path: Path, bots: list[str]) -> None:
        """Record the room's participant list (stored metadata wins)."""
        cleaned = [bot for bot in bots if isinstance(bot, str) and bot]
        if not cleaned:
            raise ConversationError("bots must be a non-empty list of bot ids")
        runtime_root = _require_runtime_root(Path(path).parent)
        # Runtime: one atomic metadata update.
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
        """Mark this conversation as ``bot_id``'s fixed DM (CBE-5)."""
        if not isinstance(bot_id, str) or not bot_id:
            raise ConversationError("dm bot must be a non-empty bot id")
        runtime_root = _require_runtime_root(Path(path).parent)
        # Runtime DM doors bootstrap via AdminStore; a group door can
        # never become a DM (DM integrity). Idempotent when the door
        # is already this bot's DM, otherwise refuse.
        from thyca.conversations.store.runtime import resolve_ref
        from thyca.conversations.store.contracts import ConversationStoreError

        try:
            ref = resolve_ref(runtime_root, Path(path).stem)
        except ConversationStoreError as exc:
            raise ConversationError("cannot mark DM") from exc
        if ref.kind == "dm" and ref.id == bot_id:
            return
        raise ConversationError("cannot convert group to DM on conversation store")

    # Read which bot owns the DM door named by a binding path.
    def read_dm(self, path: Path) -> str | None:
        """The DM owner bot id for one door, or None when not a DM."""
        runtime_root = _require_runtime_root(Path(path).parent)
        from thyca.conversations.store.contracts import ConversationStoreError

        try:
            from thyca.conversations.store.runtime import resolve_ref

            ref = resolve_ref(runtime_root, Path(path).stem)
            return ref.id if ref.kind == "dm" else None
        except ConversationError:
            raise
        except ConversationStoreError as exc:
            # A missing door is not found; a broken store is corrupt, never None.
            if getattr(exc, "scope", "global") == "local":
                raise ConversationNotFound(path) from exc
            raise ConversationCorrupt(path, None, exc) from exc
        except Exception:
            return None

    # Read the participant list stored for one door.
    def read_bots(self, path: Path) -> list[str] | None:
        """Stored participant list for one door, or None when unset."""
        runtime_root = _require_runtime_root(Path(path).parent)
        from thyca.conversations.store.contracts import ConversationStoreError

        try:
            from thyca.conversations.store.runtime import resolve_ref
            from thyca.conversations.store.database import (
                _ensure_root_ready,
                _root_instance_id_from_conn,
                _transaction_context,
                open_existing_conversation,
            )
            from thyca.conversations.store.contracts import _decode_metadata

            ref = resolve_ref(runtime_root, Path(path).stem)
            conn = open_existing_conversation(runtime_root, lock_mode="shared")
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
        except ConversationError:
            raise
        except ConversationStoreError as exc:
            # A missing door is not found; a broken store is corrupt, never None.
            if getattr(exc, "scope", "global") == "local":
                raise ConversationNotFound(path) from exc
            raise ConversationCorrupt(path, None, exc) from exc
        except Exception:
            return None

    # Assemble the full stored history for one door.
    def scan(self, path: Path) -> list[Message]:
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
            open_existing_conversation,
        )
        from thyca.conversations.store.contracts import _decode_metadata

        conn = open_existing_conversation(runtime_root, lock_mode="shared")
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
        return list(conv.messages)

    # Load one door as a ConversationRuntime from the conversation store.
    def load(self, conversation_id: str) -> ConversationRuntime:
        runtime_root = _runtime_root_for(self.conversations_dir)
        if runtime_root is None:
            # Not found (a ConversationError): missing-door callers such as the
            # bot runtime and memory visibility keep their fallback.
            raise ConversationNotFound(conversation_id, "runtime store not initialized for this root")
        from thyca.conversations.store.runtime import resolve_ref
        from thyca.conversations.store.contracts import ConversationStoreError

        try:
            ref = resolve_ref(runtime_root, conversation_id)
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                raise ConversationNotFound(conversation_id) from exc
            raise ConversationCorrupt(conversation_id, None, exc) from exc
        except ValueError as exc:
            raise ConversationNotFound(conversation_id) from exc
        path = _runtime_conversation_path(self.conversations_dir, conversation_id)
        try:
            messages = self.scan(path)
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                raise ConversationNotFound(conversation_id) from exc
            raise ConversationCorrupt(path, None, exc) from exc
        return ConversationRuntime(
            conversation_id, path, messages,
            self.read_bots(path), self.read_dm(path),
        )

    # List binding paths for every stored door, newest first.
    def list_paths(self) -> list[Path]:
        runtime_root = _require_runtime_root(self.conversations_dir)
        # Runtime: newest-first store listing projected as identifier
        # paths (no files required). Order mirrors AdminStore.list
        # (created_at desc, kind/id asc).
        from thyca.conversations.store.admin import AdminStore

        try:
            items, _ = AdminStore(runtime_root).list(limit=5000)
        except Exception:
            return []
        return [_runtime_conversation_path(self.conversations_dir, row.ref.id) for row in items]

    # Load the newest stored door, skipping any that fail to read.
    def latest(self) -> ConversationRuntime:
        _require_runtime_root(self.conversations_dir)
        candidates = self.list_paths()
        if not candidates:
            raise ConversationNotFound(self.conversations_dir)
        # Newest-first already; first loadable wins (skip corrupt).
        for chosen in candidates:
            try:
                return self.load(chosen.stem)
            except (ConversationCorrupt, ConversationNotFound):
                continue
        raise ConversationNotFound(self.conversations_dir, "no valid conversations")

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
                    raise ConversationError("cannot append: no acting bot")
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
                raise ConversationError("cannot append") from exc
            raise
        return

    # Refuse history rewrites on the append-only conversation store.
    def rewrite(
        self,
        conversation_id: str,
        target: Path,
        messages: list[Message],
        bots: list[str] | None = None,
        dm: str | None = None,
    ) -> None:
        # Runtime store is append-only: history rewrites never go
        # through here (ConversationManager branches to store-native paths;
        # CanonicalConversationStore overrides this for timeline journals).
        raise ConversationError("rewrite not supported on conversation store")
