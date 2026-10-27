"""Bot posting service: DM/room message + card writes, DM-pending consume.

One cohesive module: everything here lands bot-authored rows into
transcripts (message_user / ask_user / report_error paths, outbound
adapter posts, follow-up answer consume). See :class:`service.BotPostingService`.
"""
from __future__ import annotations

from .service import BotPostingDeps, BotPostingService

__all__ = ["BotPostingDeps", "BotPostingService"]
