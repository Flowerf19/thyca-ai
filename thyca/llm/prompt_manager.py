from __future__ import annotations

from pathlib import Path

from thyca.memory.active import ActiveSnapshot

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "seeds" / "prompts"
_TEMPLATE_NAMES = frozenset({"soul", "identity", "user"})
_STUB_SOUL = frozenset({"", "# Soul"})
_STUB_IDENTITY = frozenset({"", "# Identity"})
_STUB_USER = frozenset({"", "# User"})


class PromptManager:
    def build(self, hot: ActiveSnapshot) -> str:
        # Stub profile files inject nothing: no silent template substitution.
        # (Templates still seed new files on creation via ActiveMemory.)
        soul = hot.soul.strip()
        identity = hot.identity.strip()
        user = hot.user.strip()
        parts = []
        if identity not in _STUB_IDENTITY:
            parts.append(_section("identity", identity))
        if soul not in _STUB_SOUL:
            parts.append(_section("role", soul))
        if user not in _STUB_USER:
            parts.append(_section("user", hot.user))
        parts.append(_section("today", hot.today))
        if hot.today_elsewhere:
            parts.append(_section("today_elsewhere", hot.today_elsewhere))
        if hot.skills:
            parts.append(_section("skills", hot.skills))
        parts.append(_section("rules", self.rules_section()))
        return "\n".join(parts)

    def rules_section(self) -> str:
        return (_PROMPTS_DIR / "rules.md").read_text(encoding="utf-8").rstrip("\n")

    def template(self, name: str) -> str:
        key = name.strip().lower()
        if key not in _TEMPLATE_NAMES:
            raise ValueError(f"unknown prompt template: {name!r}")
        return (_PROMPTS_DIR / f"{key}.md").read_text(encoding="utf-8")


def _section(name: str, body: str) -> str:
    return f"<{name}>\n{body}\n</{name}>"
