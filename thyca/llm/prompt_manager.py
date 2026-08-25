from __future__ import annotations

from pathlib import Path

from thyca.memory.active import ActiveSnapshot

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_TEMPLATE_NAMES = frozenset({"soul", "identity"})
_STUB_SOUL = frozenset({"", "# Soul"})
_STUB_IDENTITY = frozenset({"", "# Identity"})
_STUB_USER = frozenset({"", "# User"})

_RULES = (
    "Use memory_remember only for daily L2 bullets (memory/YYYY-MM-DD.md).\n"
    "To update your persona or profile, use write/edit on these exact paths:\n"
    "  - ~/.thyca/SOUL.md\n"
    "  - ~/.thyca/IDENTITY.md\n"
    "  - ~/.thyca/USER.md\n"
    "Do not write or edit L2 daily files or sessions under ~/.thyca.\n"
    "You may write/edit ~/.thyca/config.json (provider keys, mcpServers).\n"
    "Need a capability you do not have (HTTP, search, weather, other APIs): "
    "do not say you cannot add tools. Write a small FastMCP stdio server in the workspace "
    "(mcp.server.fastmcp, mcp.run()), then add mcpServers.<name> = {command, args, env}. "
    "Name must match [A-Za-z0-9_-]+. Put API keys in env, not in the script. "
    "Tell the user to restart thyca/--serve. Tools appear as server__tool only after restart. "
    "If that server is already in config this session, call server__tool — do not ask the user to run it by hand.\n"
    "memory_search is lexical-first. If search returns nothing, say so. Do not invent memories."
)


class PromptManager:
    def build(self, hot: ActiveSnapshot) -> str:
        soul = hot.soul.strip()
        if soul in _STUB_SOUL:
            soul = self.template("soul")
        identity = hot.identity.strip()
        if identity in _STUB_IDENTITY:
            identity = self.template("identity")
        user = hot.user.strip()
        parts = [
            _section("identity", identity),
            _section("role", soul),
        ]
        if user not in _STUB_USER:
            parts.append(_section("user", hot.user))
        parts.append(_section("today", hot.today))
        if hot.yesterday:
            parts.append(_section("yesterday", hot.yesterday))
        parts.append(_section("rules", self.rules_section()))
        return "\n".join(parts)

    def rules_section(self) -> str:
        return _RULES

    def template(self, name: str) -> str:
        key = name.strip().lower()
        if key not in _TEMPLATE_NAMES:
            raise ValueError(f"unknown prompt template: {name!r}")
        return (_PROMPTS_DIR / f"{key}.md").read_text(encoding="utf-8")


def _section(name: str, body: str) -> str:
    return f"<{name}>\n{body}\n</{name}>"
