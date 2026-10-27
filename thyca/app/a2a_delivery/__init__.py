"""A2A delivery service: mailbox take, bus recording, recipient turns.

One cohesive module: everything here moves one envelope from queue to
recorded reply. See :class:`service.A2ADeliveryService`.
"""
from __future__ import annotations

from .service import A2ADeliveryDeps, A2ADeliveryService

__all__ = ["A2ADeliveryDeps", "A2ADeliveryService"]
