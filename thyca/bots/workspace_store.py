"""Authoritative workspace registry per bot (WS-1, canonical-only).

State lives in ``bots/<bot_id>/workspace/registry.json``: window bindings
(window metadata + canonical internal session id + room cursor) and task
owners. There is no legacy map, no migration source, no backup — one
current shape, written and read here.

``ensure_session`` creates a binding (never a transcript): the canonical
transcript lives in the workspace timeline. Malformed registry state
raises instead of being silently reset; unknown keys (top-level and
per-window) are preserved across updates. Every read-modify-write runs
under the per-bot registry FileLock.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock

from thyca.bots.card import NAME_MAX, _NAME_RE
from thyca.config import atomic_write_text
from thyca.core.channel import WindowRef

REGISTRY_FILENAME = "registry.json"
REGISTRY_VERSION = 1


def _new_internal_session_id() -> str:
    """Fresh canonical session id (same grammar as room sessions)."""
    import secrets
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    return f"{timestamp}_{secrets.token_hex(2)}"


class WorkspaceStoreError(Exception):
    """Corrupt new-format workspace state; never silently overwritten."""


def _check_bot_id(bot_id: str) -> None:
    if not isinstance(bot_id, str) or not _NAME_RE.fullmatch(bot_id) \
            or len(bot_id) > NAME_MAX:
        raise ValueError(f"invalid bot id: {bot_id!r}")


@dataclass
class WindowBinding:
    """One window's binding inside a bot workspace."""

    window_id: str
    internal_session_id: str | None = None
    cursor: tuple[int, str] | None = None
    window: WindowRef | None = None
    extra: dict = field(default_factory=dict)

    def to_entry(self) -> dict:
        entry: dict = dict(self.extra)
        if self.internal_session_id is not None:
            entry["session"] = self.internal_session_id
        if self.cursor is not None:
            entry["room_cursor"], entry["room_fingerprint"] = self.cursor
        if self.window is not None:
            entry["window"] = self.window.to_dict()
        return entry


def _entry_to_binding(window_id: str, entry: dict) -> WindowBinding:
    if not isinstance(entry, dict):
        raise WorkspaceStoreError(
            f"registry entry for window {window_id!r} must be a dict"
        )
    known = {"session", "room_cursor", "room_fingerprint", "window"}
    extra = {k: v for k, v in entry.items() if k not in known}
    session = entry.get("session")
    if session is not None and not isinstance(session, str):
        raise WorkspaceStoreError(
            f"registry entry for window {window_id!r}: session must be a string"
        )
    count, fingerprint = entry.get("room_cursor"), entry.get("room_fingerprint")
    cursor = None
    if count is not None or fingerprint is not None:
        if not isinstance(count, int) or not isinstance(fingerprint, str):
            raise WorkspaceStoreError(
                f"registry entry for window {window_id!r}: "
                "room_cursor/room_fingerprint must be int + string"
            )
        cursor = (count, fingerprint)
    window = entry.get("window")
    if window is not None:
        try:
            window = WindowRef.from_dict(window)
        except ValueError as error:
            raise WorkspaceStoreError(
                f"registry entry for window {window_id!r}: {error}"
            ) from error
    return WindowBinding(
        window_id=window_id,
        internal_session_id=session,
        cursor=cursor,
        window=window,
        extra=extra,
    )


class WorkspaceStore:
    """Atomic workspace-registry persistence under ``bots/<bot_id>/workspace/``."""

    def __init__(self, thyca_dir: Path) -> None:
        self._dir = Path(thyca_dir)

    def _bot_dir(self, bot_id: str) -> Path:
        _check_bot_id(bot_id)
        return self._dir / "bots" / bot_id

    def _registry_path(self, bot_id: str) -> Path:
        return self._bot_dir(bot_id) / "workspace" / REGISTRY_FILENAME

    def _txn_lock(self, bot_id: str) -> FileLock:
        """One lock covering a whole registry read-modify-write."""
        return FileLock(str(self._registry_path(bot_id)) + ".lock")

    def _read_registry_doc(self, bot_id: str) -> dict:
        """Raw new-format doc; corrupt JSON/shape raises, never resets."""
        path = self._registry_path(bot_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": REGISTRY_VERSION, "windows": {}}
        except OSError as error:
            raise WorkspaceStoreError(
                f"cannot read workspace registry: {error}"
            ) from error
        try:
            data = json.loads(raw)
        except ValueError as error:
            raise WorkspaceStoreError(
                f"workspace registry is not valid JSON: {path}"
            ) from error
        if not isinstance(data, dict) or not isinstance(
            data.get("windows"), dict
        ):
            raise WorkspaceStoreError(
                f"workspace registry must be a dict with a 'windows' dict: {path}"
            )
        return data

    def _write_registry_doc(self, bot_id: str, path: Path, doc: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        atomic_write_text(path, json.dumps(doc, ensure_ascii=False))

    def load(self, bot_id: str) -> dict[str, WindowBinding]:
        """All window bindings from the registry. Read-only: nothing is
        written back and no session is created by reading."""
        doc = self._read_registry_doc(bot_id)
        bindings: dict[str, WindowBinding] = {}
        for window_id, entry in doc["windows"].items():
            if not isinstance(window_id, str):
                continue
            bindings[window_id] = _entry_to_binding(window_id, entry)
        return bindings

    def record_task_owner(
        self, bot_id: str, work_id: str, owner: str
    ) -> None:
        """Persist canonical task -> owner bot in the registry doc.

        Written only when a NEW memory task is created (memory_remember
        new_task success); never inferred from catalog unions."""
        _check_bot_id(bot_id)
        from thyca.core.channel import WORK_REF_RE

        if not WORK_REF_RE.fullmatch(work_id or ""):
            raise WorkspaceStoreError(f"invalid work id: {work_id!r}")
        _check_bot_id(owner)
        path = self._registry_path(bot_id)
        with self._txn_lock(bot_id):
            doc = self._read_registry_doc(bot_id)
            owners = doc.setdefault("task_owners", {})
            if not isinstance(owners, dict):
                raise WorkspaceStoreError("task_owners must be a dict")
            existing = owners.get(work_id)
            if existing is not None and existing != owner:
                raise WorkspaceStoreError(
                    f"task {work_id} already owned by {existing}")
            owners[work_id] = owner
            doc.setdefault("version", REGISTRY_VERSION)
            self._write_registry_doc(bot_id, path, doc)

    def task_owner(self, bot_id: str, work_id: str) -> str | None:
        _check_bot_id(bot_id)
        doc = self._read_registry_doc(bot_id)
        owners = doc.get("task_owners")
        if not isinstance(owners, dict):
            return None
        owner = owners.get(work_id)
        return owner if isinstance(owner, str) else None

    def task_owner_fallback(self, bot_id: str, work_id: str) -> str | None:
        """Deterministic adoption for tasks without a recorded owner.

        Reads the EARLIEST daily-Markdown entry whose heading metadata
        carries ``task=<work_id>`` — line order across sorted day files —
        and accepts it only when that entry has exactly one concrete
        owner bot (``meta.bots`` singleton; ``team``/empty/ambiguous
        owners are denied). Metadata-only authorization: title/body are
        never returned, and the caller's window view does not filter it.
        A task already owned by another bot's registry wins globally —
        this file never claims a task owned elsewhere."""
        _check_bot_id(bot_id)
        from thyca.memory.heading import iter_session_blocks

        owner = self.task_owner(bot_id, work_id)
        if owner is not None:
            return owner
        bots_dir = self._dir / "bots"
        if bots_dir.is_dir():
            found: set[str] = set()
            for bot_dir in sorted(bots_dir.iterdir()):
                if not bot_dir.is_dir() or bot_dir.name == bot_id:
                    continue
                if not _NAME_RE.fullmatch(bot_dir.name):
                    continue
                try:
                    doc = self._read_registry_doc(bot_dir.name)
                except WorkspaceStoreError:
                    return None
                owners = doc.get("task_owners")
                if isinstance(owners, dict) and owners.get(work_id):
                    found.add(owners[work_id])
            if len(found) == 1:
                return found.pop()
            if len(found) > 1:
                return None
        memory_dir = self._dir / "memory"
        if not memory_dir.is_dir():
            return None
        for day_path in sorted(memory_dir.glob("*.md")):
            try:
                lines = day_path.read_text("utf-8").splitlines()
            except OSError:
                return None
            for meta, _entry_id, _start, _end in iter_session_blocks(
                lines, str(day_path)
            ):
                if meta.task != work_id:
                    continue
                bots = list(meta.bots) if meta.bots is not None else []
                return bots[0] if len(bots) == 1 and bots[0] != "team" else None
        return None

    def get_binding(self, bot_id: str, window_id: str) -> WindowBinding | None:
        return self.load(bot_id).get(window_id)

    def bind_window(
        self,
        bot_id: str,
        window: WindowRef,
        *,
        internal_session_id: str | None = None,
        cursor: tuple[int, str] | None = None,
    ) -> WindowBinding:
        """Bind real window metadata (+ optional refs) in the registry.

        A first-time window mints its canonical internal session id when
        the caller does not pass one. Existing fields not passed here
        and unknown keys on this and every other window are preserved."""
        if not isinstance(window, WindowRef):
            raise ValueError("window must be a WindowRef")
        path = self._registry_path(bot_id)
        with self._txn_lock(bot_id):
            doc = self._read_registry_doc(bot_id)
            binding = self._update_locked(
                bot_id, doc, window.window_id, window=window,
                internal_session_id=internal_session_id, cursor=cursor,
                generate_session=True,
            )
            self._write_registry_doc(bot_id, path, doc)
        return binding

    def _update_locked(
        self,
        bot_id: str,
        doc: dict,
        window_id: str,
        *,
        window: WindowRef | None = None,
        internal_session_id: str | None = None,
        cursor: tuple[int, str] | None = None,
        generate_session: bool = False,
    ) -> WindowBinding:
        """Single read-modify-write helper (caller holds the txn lock)."""
        windows = doc["windows"]
        existing = windows.get(window_id)
        binding = (
            _entry_to_binding(window_id, existing)
            if existing is not None
            else WindowBinding(window_id=window_id)
        )
        if window is not None:
            binding.window = window
        if internal_session_id is not None:
            binding.internal_session_id = internal_session_id
        if binding.internal_session_id is None and generate_session:
            binding.internal_session_id = _new_internal_session_id()
        if cursor is not None:
            binding.cursor = cursor
        windows[window_id] = binding.to_entry()
        doc.setdefault("version", REGISTRY_VERSION)
        return binding

    def ensure_session(self, bot_id: str, window_id: str) -> WindowBinding:
        """Get-or-create the canonical binding for a window id.

        Mints a stable internal session id on first use (no transcript
        is created — canonical content lives in the workspace timeline).
        Works for synthetic rooms with no ``WindowRef``; real window
        metadata lands later via :meth:`bind_window`."""
        _check_bot_id(bot_id)
        if not isinstance(window_id, str) or not window_id:
            raise ValueError("window_id must be a non-empty string")
        path = self._registry_path(bot_id)
        with self._txn_lock(bot_id):
            doc = self._read_registry_doc(bot_id)
            binding = self._update_locked(
                bot_id, doc, window_id, generate_session=True)
            self._write_registry_doc(bot_id, path, doc)
        return binding

    def set_cursor(
        self, bot_id: str, window_id: str,
        cursor: tuple[int, str] | None,
    ) -> None:
        """Persist a room-context cursor on an existing binding.

        A None cursor writes nothing; an unknown window writes nothing
        (callers only persist after the binding exists)."""
        if cursor is None:
            return
        count, fingerprint = cursor
        if not isinstance(count, int) or not isinstance(fingerprint, str):
            return
        path = self._registry_path(bot_id)
        with self._txn_lock(bot_id):
            doc = self._read_registry_doc(bot_id)
            if window_id not in doc["windows"]:
                return
            self._update_locked(bot_id, doc, window_id, cursor=cursor)
            self._write_registry_doc(bot_id, path, doc)
