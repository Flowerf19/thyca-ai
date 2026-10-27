"""Background jobs service: idle sweep, profile aggregation, memory hooks.

One cohesive module: everything here runs outside turns (scheduler
sweep, daily profile aggregation) or as a BotRuntime hook
(memory flush/search/task catalog). See :class:`service.BackgroundJobsService`.
"""
from __future__ import annotations

from .service import BackgroundJobsDeps, BackgroundJobsService

__all__ = ["BackgroundJobsDeps", "BackgroundJobsService"]
