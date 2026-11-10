"""ConversationRuntime-admin service: DMs, rooms, compaction.

One cohesive module: everything that creates, finds, deletes,
or compacts room conversations, plus participant and payload reads. See
:class:`service.ConversationAdminService`.
"""
from __future__ import annotations

from .service import ConversationAdminDeps, ConversationAdminService

__all__ = ["ConversationAdminDeps", "ConversationAdminService"]
