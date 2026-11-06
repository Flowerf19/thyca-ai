"""MCP server entity."""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ConfigError


@dataclass(frozen=True)
class McpServerCfg:
    # command is required (no default): bare McpServerCfg() must be a
    # TypeError, not a ConfigError from a default that always fails.
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise ConfigError("mcpServers[].command must be a non-empty string")
        if not isinstance(self.args, list) or any(not isinstance(arg, str) for arg in self.args):
            raise ConfigError("mcpServers[].args must be a list of strings")
        if not isinstance(self.env, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()
        ):
            raise ConfigError("mcpServers[].env must map strings to strings")
