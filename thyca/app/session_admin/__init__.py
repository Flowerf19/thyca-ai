"""Session-admin service: DMs, rooms, titles, compaction.

One cohesive module: everything that creates, finds, renames, deletes,
or compacts room sessions, plus participant and payload reads. See
:class:`service.SessionAdminService`.
"""
from __future__ import annotations

from .service import SessionAdminDeps, SessionAdminService

__all__ = ["SessionAdminDeps", "SessionAdminService"]
