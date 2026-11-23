"""Agent Skills store package."""
from __future__ import annotations

from .store import (
    _NAME_RE,
    _PACKAGED_SKILLS,
    DESCRIPTION_MAX,
    INDEX_DESCRIPTION_CHARS,
    NAME_MAX,
    SkillMeta,
    SkillStore,
)

__all__ = [
    "DESCRIPTION_MAX",
    "INDEX_DESCRIPTION_CHARS",
    "NAME_MAX",
    "_NAME_RE",
    "_PACKAGED_SKILLS",
    "SkillMeta",
    "SkillStore",
]
