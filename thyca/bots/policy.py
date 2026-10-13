"""Gateway policy for bot turns: card enforcement at submit time.

`hidden_tools` from the Agent Card wins over every allowlist (master §2):
a hidden tool is denied at submit time by name, so it also covers MCP
tools loaded later. For non-system bots the policy is fail-closed in
depth behind the schema filter:

* when the runtime binds an allowlist, any call outside it is denied
  (an empty allowlist therefore means *no* tools at all);
* file tools (``read``/``write``/``edit``) may not target the team-control
  files (``bots/**``, ``config.json``, ``skills-team/**``) — the same rule
  :class:`PathGuard` enforces inside the handlers;
* ``bash`` may not reference those targets by absolute/``~`` path or by
  ``BOT.md`` name (bash otherwise bypasses PathGuard by design).

The denied/allowed sets travel on ContextVars bound by the bot runtime
for the in-flight turn; the default (no bot) allows all — zero behavior
change for existing single-user turns.
"""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from thyca.bots.context import current_bot_turn_context
from thyca.core.protocol import ToolCall
from thyca.memory.identity import current_bot_id
from thyca.tools.gateway.policy import Policy, PolicyDenied

_hidden_tools: ContextVar[frozenset[str] | None] = ContextVar(
    "thyca_hidden_tools", default=None
)
_allowed_tools: ContextVar[frozenset[str] | None] = ContextVar(
    "thyca_allowed_tools", default=None
)

_FILE_TOOLS = frozenset({"read", "write", "edit"})


def bind_hidden_tools(names: frozenset[str] | None):
    """Pin the hidden-tool deny set for the in-flight turn."""
    return _hidden_tools.set(names)


def reset_hidden_tools(token) -> None:
    _hidden_tools.reset(token)


def bind_allowed_tools(names: frozenset[str] | None):
    """Pin the fail-closed allowlist for a non-system bot turn.

    ``None`` means unbound (user / Thyca system turn: no restriction);
    an empty frozenset means the bot may call nothing."""
    return _allowed_tools.set(names)


def reset_allowed_tools(token) -> None:
    _allowed_tools.reset(token)


def _is_system_bot() -> bool:
    bot_id = current_bot_id()
    return bot_id is None or bot_id == "thyca"


def _protected_roots() -> list[Path]:
    ctx = current_bot_turn_context()
    scope = getattr(ctx, "scope", None) if ctx is not None else None
    # BotRuntime binds the thyca root on the context via `thyca_dir` when
    # present; fall back to the home default the config layer also uses.
    root = getattr(ctx, "thyca_dir", None) if ctx is not None else None
    if root is None:
        root = Path.home() / ".thyca"
    root = Path(root)
    return [root / "bots", root / "config.json", root / "skills-team"]


def _targets_protected(value: str) -> bool:
    """True when a path/command string references a team-control target."""
    if not isinstance(value, str) or not value:
        return False
    text = value.replace("\\", "/")
    for target in _protected_roots():
        absolute = str(target)
        if absolute in text or absolute.replace(str(Path.home()), "~") in text:
            return True
    lowered = text.lower()
    if "/.thyca/bots" in lowered or "/.thyca/skills-team" in lowered:
        return True
    if lowered.rstrip("/").endswith("/.thyca/config.json") or "~/.thyca/config.json" in lowered:
        return True
    return False


class BotPolicy(Policy):
    """Default allow-all plus the in-flight bot's card enforcement."""

    def check(self, call: ToolCall) -> None:
        hidden = _hidden_tools.get()
        if hidden and call.name in hidden:
            raise PolicyDenied(f"tool hidden by bot card: {call.name}")
        if _is_system_bot():
            return
        allowed = _allowed_tools.get()
        if allowed is not None and call.name not in allowed:
            raise PolicyDenied(
                f"tool not in bot card allowlist: {call.name}"
            )
        if call.name in _FILE_TOOLS:
            path = call.arguments.get("path") if isinstance(call.arguments, dict) else None
            if isinstance(path, str) and _targets_protected(path):
                raise PolicyDenied(f"bot may not access team-control file: {path}")
            # Relative or obfuscated paths are still caught by PathGuard
            # inside the handler, which resolves against the real root.
        if call.name == "bash":
            command = call.arguments.get("command") if isinstance(call.arguments, dict) else None
            if isinstance(command, str) and (
                _targets_protected(command) or "BOT.md" in command
            ):
                raise PolicyDenied("bot may not access team-control files via bash")
