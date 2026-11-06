"""Fixture-only write-route switch: block legacy JSONL writers on switched roots.

Disposable fixture roots opt in via :func:`enable_fixture_writes`; while a
root is enabled every legacy ``SessionStore`` mutation whose target lives
under that root must fail closed. Live roots (notably ``~/.thyca``) are
never enabled: :func:`enable_fixture_writes` refuses them.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_ENABLED: set[str] = set()


def _canonical(path: Path | str) -> str:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw:
        raise ValueError('path must be a non-empty string')
    return os.path.normpath(os.path.abspath(raw))


def _live_root() -> str:
    return _canonical(Path.home() / '.thyca')


def enable_fixture_writes(root: Path | str) -> None:
    """Mark a disposable fixture root as routed to the conversation store.

    Refuses the live ``~/.thyca`` root (or anything inside it) so the live
    path can never be switched. Idempotent for already-enabled roots.
    """
    canonical = _canonical(root)
    live = _live_root()
    if canonical == live or canonical.startswith(live + os.sep):
        raise ValueError('live root stays on the legacy path')
    with _LOCK:
        _ENABLED.add(canonical)


def disable_fixture_writes(root: Path | str) -> None:
    """Switch a fixture root back; legacy writers are allowed again after."""
    canonical = _canonical(root)
    with _LOCK:
        _ENABLED.discard(canonical)


def is_fixture_write_enabled(root: Path | str) -> bool:
    """True when *root* itself was explicitly enabled (exact match)."""
    canonical = _canonical(root)
    with _LOCK:
        return canonical in _ENABLED


def is_legacy_writer_blocked(path: Path | str) -> bool:
    """True when *path* lives on (or under) any enabled fixture root."""
    canonical = _canonical(path)
    with _LOCK:
        enabled = tuple(_ENABLED)
    for root in enabled:
        if canonical == root or canonical.startswith(root + os.sep):
            return True
    return False
