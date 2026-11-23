from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from thyca.conversations.runtime.wire import is_sidecar_message
from thyca.conversations.store import runtime as _store_runtime
from thyca.conversations.store.admin import AdminStore
from thyca.conversations.store.contracts import (
    ConversationStoreError,
    _decode_metadata,
    _encode_metadata,
    _local_error,
    _metadata,
    normalize_group_title,
    validate_metadata,
)
from thyca.conversations.store.database import (
    _ensure_door_ready,
    _ensure_root_ready,
    _root_instance_id_from_conn,
    _transaction_context,
    open_existing_conversation,
)
from thyca.conversations.store.read_adapter import assemble_history
from thyca.conversations.store.runtime import (
    RuntimeReadIntent,
    RuntimeWriteIntent,
    is_runtime_ready,
)
from thyca.conversations.store.write_adapter import (
    append_message,
    append_server_message,
    append_server_row,
)
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message, utc_now_ts

from .errors import ConversationCorrupt, ConversationError, ConversationNotFound
from .models import ConversationRuntime

CONVERSATION_ID_PATTERN = r"(?:group-[0-9a-f]{32}|[a-z0-9]+(?:-[a-z0-9]+)*)"
_ID_RE = re.compile(rf"^(?:{CONVERSATION_ID_PATTERN})$")


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
        pass
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


def _require_runtime_root(conversations_dir: Path) -> Path:
    root = _runtime_root_for(conversations_dir)
    if root is None:
        raise ConversationError("runtime store not initialized for this root")
    return root


def _runtime_conversation_path(conversations_dir: Path, conversation_id: str) -> Path:
    """Stable identifier path for a runtime conversation (no file required)."""
    return Path(conversations_dir) / f"{conversation_id}.jsonl"


def _runtime_mutate_metadata(runtime_root: Path, ref, transform) -> dict:
    """Read, change, and store door metadata atomically (server-side).

    The caller is the trusted server-side ``ChatApp`` flow (a membership
    edit), so no bot membership authorizes this write and
    no bot actor is claimed. Root and door readiness are still checked
    inside the same ``BEGIN IMMEDIATE`` transaction as the update.
    """


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

    def __init__(self, conversations_dir: Path) -> None:
        self.conversations_dir = conversations_dir

    def ensure_dir(self) -> None:
        try:
            self.conversations_dir.mkdir(parents=True, exist_ok=True)
            self.conversations_dir.chmod(0o700)
        except OSError as exc:
            raise ConversationError(f"cannot secure {self.conversations_dir}: {exc}") from exc

    def path_for(self, conversation_id: str) -> Path:
        if not isinstance(conversation_id, str) or not _ID_RE.fullmatch(conversation_id):
            raise ConversationNotFound(conversation_id, f"invalid conversation id: {conversation_id!r}")
        root = self.conversations_dir.resolve()
        candidate = (self.conversations_dir / f"{conversation_id}.jsonl").resolve()
        if candidate.parent != root:
            raise ConversationNotFound(candidate, "conversation id traversal blocked")
        return self.conversations_dir / f"{conversation_id}.jsonl"

    def create(self, conversation_id: str) -> Path:
        runtime_root = _require_runtime_root(self.conversations_dir)

        try:
            ref = ConversationRef.group(conversation_id)
        except ValueError:
            raise ConversationError(f"cannot create conversation: {conversation_id!r}") from None
        if ref.kind != "group":
            raise ConversationError(f"cannot create conversation: {conversation_id!r}")


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
                    raise FileExistsError(conversation_id) from exc
                except sqlite3.Error as exc:

                    raise ConversationStoreError() from exc
        finally:
            conn.close()
        return _runtime_conversation_path(self.conversations_dir, conversation_id)

    def delete(self, conversation_id: str) -> None:
        runtime_root = _require_runtime_root(self.conversations_dir)

        try:
            ref = _store_runtime.resolve_ref(runtime_root, conversation_id)
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                return
            raise ConversationError("cannot delete conversation") from exc
        except ValueError:
            return
        try:
            AdminStore(runtime_root).delete(ref)
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                return
            raise ConversationError("cannot delete conversation") from exc
        return

    def append_bots(self, path: Path, bots: list[str]) -> None:
        """Record the room's participant list (stored metadata wins)."""
        cleaned = [bot for bot in bots if isinstance(bot, str) and bot]
        if not cleaned:
            raise ConversationError("bots must be a non-empty list of bot ids")
        runtime_root = _require_runtime_root(Path(path).parent)

        ref = _store_runtime.resolve_ref(runtime_root, Path(path).stem)
        _runtime_mutate_metadata(
            runtime_root, ref,
            lambda meta: {**meta, "participants": list(cleaned)},
        )
        return

    def append_dm(self, path: Path, bot_id: str) -> None:
        """Mark this conversation as ``bot_id``'s fixed DM (CBE-5)."""
        if not isinstance(bot_id, str) or not bot_id:
            raise ConversationError("dm bot must be a non-empty bot id")
        runtime_root = _require_runtime_root(Path(path).parent)

        try:
            ref = _store_runtime.resolve_ref(runtime_root, Path(path).stem)
        except ConversationStoreError as exc:
            raise ConversationError("cannot mark DM") from exc
        if ref.kind == "dm" and ref.id == bot_id:
            return
        raise ConversationError("cannot convert group to DM on conversation store")

    def read_dm(self, path: Path) -> str | None:
        """The DM owner bot id for one door, or None when not a DM."""
        runtime_root = _require_runtime_root(Path(path).parent)

        try:

            ref = _store_runtime.resolve_ref(runtime_root, Path(path).stem)
            return ref.id if ref.kind == "dm" else None
        except ConversationError:
            raise
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                raise ConversationNotFound(path) from exc
            raise ConversationCorrupt(path, None, exc) from exc
        except Exception:
            return None

    def _read_metadata(self, path: Path) -> dict | None:
        runtime_root = _require_runtime_root(Path(path).parent)

        try:

            ref = _store_runtime.resolve_ref(runtime_root, Path(path).stem)
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
            return _decode_metadata(row[0])
        except ConversationError:
            raise
        except ConversationStoreError as exc:
            if getattr(exc, "scope", "global") == "local":
                raise ConversationNotFound(path) from exc
            raise ConversationCorrupt(path, None, exc) from exc
        except Exception:
            return None

    def read_title(self, path: Path) -> str | None:
        metadata = self._read_metadata(path)
        if not metadata:
            return None
        title = metadata.get("title")
        return title if isinstance(title, str) and title else None

    def set_group_title(self, path: Path, title: str | None) -> None:

        cleaned = normalize_group_title(title)
        runtime_root = _require_runtime_root(Path(path).parent)

        ref = _store_runtime.resolve_ref(runtime_root, Path(path).stem)
        if ref.kind != "group":
            raise ConversationError("cannot title a DM conversation")

        def apply(meta: dict) -> dict:
            updated = dict(meta)
            if cleaned is None:
                updated.pop("title", None)
            else:
                updated["title"] = cleaned
            return updated

        _runtime_mutate_metadata(runtime_root, ref, apply)

    def read_bots(self, path: Path) -> list[str] | None:
        """Stored participant list for one door, or None when unset."""
        metadata = self._read_metadata(path)
        if not metadata:
            return None
        participants = metadata.get("participants")
        return list(participants) if participants else None

    def scan(self, path: Path) -> list[Message]:
        runtime_root = _require_runtime_root(Path(path).parent)

        ref = _store_runtime.resolve_ref(runtime_root, Path(path).stem)

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

            raise ConversationStoreError("conversation unavailable")
        participants = _decode_metadata(row[0]).get("participants", [])
        actor = ref.id if ref.kind == "dm" else (participants[0] if participants else "thyca")
        conv = assemble_history(
            runtime_root, ref, actor,
            intent=RuntimeReadIntent("runtime-scan"),
        )
        return list(conv.messages)

    def load(self, conversation_id: str) -> ConversationRuntime:
        runtime_root = _runtime_root_for(self.conversations_dir)
        if runtime_root is None:
            raise ConversationNotFound(conversation_id, "runtime store not initialized for this root")

        try:
            _store_runtime.resolve_ref(runtime_root, conversation_id)
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
            title=self.read_title(path),
        )

    def list_paths(self) -> list[Path]:
        runtime_root = _require_runtime_root(self.conversations_dir)

        try:
            items, _ = AdminStore(runtime_root).list(limit=5000)
        except Exception:
            return []
        return [_runtime_conversation_path(self.conversations_dir, row.ref.id) for row in items]

    def latest(self) -> ConversationRuntime:
        _require_runtime_root(self.conversations_dir)
        candidates = self.list_paths()
        if not candidates:
            raise ConversationNotFound(self.conversations_dir)
        for chosen in candidates:
            try:
                return self.load(chosen.stem)
            except (ConversationCorrupt, ConversationNotFound):
                continue
        raise ConversationNotFound(self.conversations_dir, "no valid conversations")

    def append(self, path: Path, msg: Message, *, actor: str | None = None) -> None:
        runtime_root = _require_runtime_root(Path(path).parent)

        if is_sidecar_message(msg):
            return
        if msg.role not in ("user", "assistant"):
            return
        if msg.tool_calls is not None or msg.tool_call_id is not None:
            return
        if msg.reasoning or msg.reasoning_details:
            return
        if not isinstance(msg.content, str):
            return

        ref = _store_runtime.resolve_ref(runtime_root, Path(path).stem)
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
                append_server_row(
                    runtime_root, ref, msg,
                    intent=RuntimeWriteIntent("runtime-append"),
                )
            elif msg.role == "assistant":
                if acting is None:
                    raise ConversationError("cannot append: no acting bot")
                append_message(
                    runtime_root, ref, acting, msg,
                    intent=RuntimeWriteIntent("runtime-append"),
                )
            elif acting is not None:
                append_message(
                    runtime_root, ref, acting, msg,
                    intent=RuntimeWriteIntent("runtime-append"),
                )
            else:
                append_server_message(
                    runtime_root, ref, msg,
                    intent=RuntimeWriteIntent("runtime-append"),
                )
        except Exception as exc:

            if isinstance(exc, ConversationStoreError):
                raise ConversationError("cannot append") from exc
            raise
        return

    def rewrite(
        self,
        conversation_id: str,
        target: Path,
        messages: list[Message],
        bots: list[str] | None = None,
        dm: str | None = None,
    ) -> None:
        raise ConversationError("rewrite not supported on conversation store")
