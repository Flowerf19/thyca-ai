"""Runtime conversation-store path for all roots.

This module is the explicit runtime path for the hard-cut branch: it
works on any initialized root (including tmp test roots and, once the
legacy guard passes, the live root).

- ``RuntimeReadIntent``/``RuntimeWriteIntent`` are explicit caller
  assertions that the call targets the runtime store path. They are
  not authentication.
- ``ensure_runtime_store`` is the trusted local initializer: only the
  local ``ChatApp`` process construction (server-side startup, never an
  HTTP/CLI request handler) may call it to initialize a genuinely fresh
  root. An existing database is audited before any writer runs; a
  missing database with any leftover store artifact fails closed.
- ``require_no_legacy_sessions`` refuses to start when legacy
  ``sessions/*.jsonl`` files exist under any root (no migration).
- Door ids equal ``ref.id``: ``group-<hex32>`` for groups and the bot
  id for DMs. The HTTP layer builds refs from path segments; internal
  :func:`resolve_ref` looks a stored id up in the store across both
  kinds and never guesses kind from identifier text.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from thyca.core.conversation import ConversationRef

from .admin import AdminStore
from .agent import AgentStore
from .contracts import (
    Conversation,
    ConversationStoreError,
    DMBootstrapIntent,
    RootInitializationIntent,
)

_GROUP_RE = re.compile(r"group-[0-9a-f]{32}\Z")
_BOT_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


@dataclass(frozen=True)
class RuntimeReadIntent:
    """Explicit caller assertion for a runtime store read (all roots)."""

    purpose: str

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ValueError("purpose must be a non-empty string")


@dataclass(frozen=True)
class RuntimeWriteIntent:
    """Explicit caller assertion for a runtime store write (all roots)."""

    purpose: str

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ValueError("purpose must be a non-empty string")


def _require_read_intent(intent: object) -> RuntimeReadIntent:
    if not isinstance(intent, RuntimeReadIntent):
        raise ValueError("explicit runtime read intent required")
    return intent


def _require_write_intent(intent: object) -> RuntimeWriteIntent:
    if not isinstance(intent, RuntimeWriteIntent):
        raise ValueError("explicit runtime write intent required")
    return intent


# Report whether a name exists under a root without following links.
def _artifact_exists(root: Path, name: str) -> bool:
    import os

    try:
        os.lstat(Path(root) / name)
    except OSError:
        return False
    return True


# List the store-owned artifact names whose presence proves prior state.
def _store_artifact_names() -> tuple[str, ...]:
    from .rootfs import (
        DB_FILE_NAME,
        DM_MARKERS_NAME,
        LOCK_FILE_NAME,
        ROOT_MARKER_NAME,
        ROOT_MARKER_TEMP,
    )

    return (
        DB_FILE_NAME + "-wal",
        DB_FILE_NAME + "-shm",
        LOCK_FILE_NAME,
        ROOT_MARKER_NAME,
        ROOT_MARKER_TEMP,
        DM_MARKERS_NAME,
    )


def is_runtime_ready(root: Path) -> bool:
    """True when *root* holds an initialized conversation store."""
    try:
        return (Path(root) / "conversations.db").is_file()
    except OSError:
        return False


# Ensure the runtime store exists and passes one full audit.
def ensure_runtime_store(root: Path) -> str:
    """Initialize the runtime store under *root* (idempotent).

    Returns the root instance id. The local ``ChatApp`` construction is
    the trusted issuer for a genuinely fresh local root; no HTTP or CLI
    request handler may call this. Three cases, in order:

    (a) ``conversations.db`` exists: audit the existing root in full
    before any writer runs and return its current instance id.
    (b) The database is absent but any store artifact exists (WAL/SHM
    sidecar, lock, root marker or its temp, or ``dm-markers/``): fail
    closed. A missing database is not proof of freshness.
    (c) No database, no store artifacts, and (checked by the caller via
    :func:`require_no_legacy_sessions`) no legacy sessions: initialize
    a fresh root, preserving unrelated sentinel files untouched.
    """
    root = Path(root)
    if is_runtime_ready(root):
        AdminStore(root).audit()
        # Read the current instance id without re-initializing.
        from .database import _root_instance_id_from_conn, open_existing_session
        from .database import _transaction_context, _ensure_root_ready

        conn = open_existing_session(root, lock_mode="shared")
        try:
            with _transaction_context(conn, "BEGIN"):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                return _root_instance_id_from_conn(conn)
        finally:
            conn.close()
    # Missing DB with leftover store state is partial/corrupt, never fresh.
    if any(_artifact_exists(root, name) for name in _store_artifact_names()):
        raise ConversationStoreError("conversation unavailable")
    # Fresh root: mint a random instance id and initialize.
    root_id = str(uuid.uuid4())
    AdminStore(root).initialize(RootInitializationIntent(root_id))
    return root_id


# Refuse to start when any root still carries legacy session files.
def require_no_legacy_sessions(root: Path) -> None:
    """Storage guard: refuse to start on legacy ``sessions/`` files.

    Every root is refused (hard cut, no migration): an empty or missing
    ``sessions/`` directory passes. Raises ``RuntimeError`` with a clear
    remediation when legacy files exist under the root.
    """
    root = Path(root)
    sessions_dir = root / "sessions"
    try:
        if not sessions_dir.is_dir():
            return
        legacy = [
            p
            for p in sessions_dir.glob("*.jsonl")
            if p.is_file() and not p.is_symlink()
        ]
    except OSError:
        return
    if legacy:
        raise RuntimeError(
            f"refusing to start: legacy sessions exist under sessions/ "
            f"({len(legacy)} files); remove them to use the "
            f"conversation store (no automatic migration)"
        )


# Resolve one stored door id to its ref by database lookup only.
def resolve_ref(root: Path, session_id: str) -> ConversationRef:
    """Resolve a stored door id to its ``ConversationRef`` via the store.

    Internal callers only: the HTTP layer builds refs from path segments
    and never calls this. Kind is never guessed from the identifier
    text: the id is looked up across both ``dm`` and ``group`` doors.
    Raises ``ConversationStoreError`` (local scope) when no door
    carries the id.
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session id must be a non-empty string")
    if not (_GROUP_RE.fullmatch(session_id) or _BOT_RE.fullmatch(session_id)):
        raise ValueError(f"invalid session id: {session_id!r}")
    # Authority is the store: one indexed lookup by id across both kinds.
    from .database import (
        _ensure_root_ready,
        _root_instance_id_from_conn,
        _transaction_context,
        open_existing_session,
    )

    try:
        conn = open_existing_session(Path(root), lock_mode="shared")
        try:
            with _transaction_context(conn, "BEGIN"):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                rows = conn.execute(
                    "SELECT kind,id FROM conversations WHERE id=? AND lifecycle_state='ready'",
                    (session_id,),
                ).fetchall()
        finally:
            conn.close()
    except ConversationStoreError:
        raise
    # Same id in both doors cannot happen (namespaces are disjoint);
    # prefer the DM deterministically if corrupt data ever says so.
    for kind, identifier in rows:
        if kind == "dm":
            try:
                return ConversationRef(kind, identifier)
            except ValueError:
                continue
    for kind, identifier in rows:
        try:
            return ConversationRef(kind, identifier)
        except ValueError:
            continue
    raise ConversationStoreError("conversation unavailable", scope="local")


# Pick a member reader for one door without authorizing any write.
def runtime_reader_for(ref: ConversationRef, participants: tuple[str, ...] | list[str]) -> str:
    """Member reader for membership-checked reads of one door.

    Reads only: the returned bot is a stored participant used to satisfy
    the read-side membership check. It never authorizes a write; writers
    thread the actual acting bot (turn responder, DM owner, or the bot
    named in the message meta) to the store call instead.
    """
    if ref.kind == "dm":
        return ref.id
    members = list(participants or [])
    if not members:
        return "thyca"
    return members[0]
