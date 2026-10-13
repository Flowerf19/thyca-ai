"""Gateway policy for bot turns: hidden_tools enforcement.

`hidden_tools` from the Agent Card wins over every allowlist (master §2):
a hidden tool is denied at submit time by name, so it also covers MCP
tools loaded later. The denied set travels on a ContextVar bound by the
bot runtime for the in-flight turn; the default (no bot) allows all —
zero behavior change for existing single-user turns.
"""
from __future__ import annotations

from contextvars import ContextVar

from thyca.core.protocol import ToolCall
from thyca.tools.gateway.policy import Policy, PolicyDenied

_hidden_tools: ContextVar[frozenset[str] | None] = ContextVar(
    "thyca_hidden_tools", default=None
)


def bind_hidden_tools(names: frozenset[str] | None):
    """Pin the hidden-tool deny set for the in-flight turn."""
    return _hidden_tools.set(names)


def reset_hidden_tools(token) -> None:
    _hidden_tools.reset(token)


class BotPolicy(Policy):
    """Default allow-all plus the in-flight bot's hidden_tools deny list."""

    def check(self, call: ToolCall) -> None:
        hidden = _hidden_tools.get()
        if hidden and call.name in hidden:
            raise PolicyDenied(f"tool hidden by bot card: {call.name}")
