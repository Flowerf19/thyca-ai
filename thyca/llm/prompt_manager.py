from __future__ import annotations

from pathlib import Path

from thyca.memory.active import ActiveSnapshot

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_TEMPLATE_NAMES = frozenset({"soul", "identity"})

_RULES = (
    "Use memory_remember to persist facts; do not write or edit under ~/.thyca.\n"
    "memory_search is lexical-first (semantic=false); paraphrase then retry semantic only if needed.\n"
    "If search returns nothing, say so. Do not invent memories."
)


class PromptManager:
    def build(self, hot: ActiveSnapshot) -> str:
        parts = [
            _section("role", hot.soul),
            _section("user", hot.user),
            _section("memory", hot.memory),
            _section("today", hot.today),
        ]
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
