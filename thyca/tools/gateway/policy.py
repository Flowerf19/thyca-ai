"""Policy seam: every submit passes policy.check() first.

Default allow-all (zero behavior change); approval rules are a later feature.
"""
from __future__ import annotations

from thyca.core.protocol import ToolCall


class PolicyDenied(Exception):
    """Raised by Policy.check to refuse a tool call."""


class Policy:
    """Entry hook; the default allows every call."""

    def check(self, call: ToolCall) -> None:
        """Approve the call, or raise PolicyDenied with the reason."""
