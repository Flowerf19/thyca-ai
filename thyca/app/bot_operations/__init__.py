"""Bot read-model/operations service (canonical-workspace reads + metering).

One cohesive module: everything here answers read queries over bot
canonical state without running turns or writing transcripts.
"""
from __future__ import annotations

from .service import BotOperationsDeps, BotOperationsService

__all__ = ["BotOperationsDeps", "BotOperationsService"]
