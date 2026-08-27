"""Agent Skills store: spec-compliant SKILL.md index over ``~/.thyca/skills``.

Skill format follows the Agent Skills specification (agentskills.io/specification):
a directory ``<name>/`` containing ``SKILL.md`` with YAML frontmatter (``name``,
``description`` required) plus optional ``scripts/`` / ``references/`` / ``assets/``.
The store is read-only: it scans, validates and builds the prompt index. Skills are
authored by the agent itself through ``write``/``edit``; validation happens at scan
time so a broken skill shows up as a warning line and can be self-healed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil

import yaml

from thyca.protocol import RESULT_CAP_BYTES

NAME_MAX = 64
DESCRIPTION_MAX = 1024
INDEX_DESCRIPTION_CHARS = 256

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_PACKAGED_SKILLS = Path(__file__).resolve().parent / "skills_templates"


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    path: Path
    ok: bool
    error: str = ""


class SkillStore:
    """Read-only view + default seeding over the skills directory."""

    def __init__(self, thyca_dir: Path | None = None) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca")

    @property
    def root(self) -> Path:
        return self.thyca_dir / "skills"

    def list_meta(self) -> list[SkillMeta]:
        if not self.root.is_dir():
            return []
        metas = [
            self._meta(entry)
            for entry in sorted(self.root.iterdir())
            if entry.is_dir()
        ]
        metas.sort(key=lambda meta: meta.name)
        return metas

    def index_text(self) -> str:
        lines = []
        for meta in self.list_meta():
            if meta.ok:
                description = meta.description
                if len(description) > INDEX_DESCRIPTION_CHARS:
                    description = description[:INDEX_DESCRIPTION_CHARS].rstrip() + "…"
                line = f"- {meta.name} — {description}"
                try:
                    oversize = meta.path.stat().st_size > RESULT_CAP_BYTES
                except OSError:
                    oversize = False
                if oversize:
                    line += " (SKILL.md larger than 32KB, truncated on load)"
            else:
                line = f"- {meta.name} (SKILL.md invalid: {meta.error})"
            lines.append(line)
        return "\n".join(lines)

    def ensure_defaults(self) -> None:
        if not _PACKAGED_SKILLS.is_dir():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        for packaged in sorted(_PACKAGED_SKILLS.iterdir()):
            template = packaged / "SKILL.md"
            if not packaged.is_dir() or not template.is_file():
                continue
            target = self.root / packaged.name / "SKILL.md"
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template, target)
            target.chmod(0o600)

    def _meta(self, skill_dir: Path) -> SkillMeta:
        name = skill_dir.name
        path = skill_dir / "SKILL.md"
        if not path.is_file():
            return SkillMeta(name, "", path, ok=False, error="missing SKILL.md")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return SkillMeta(name, "", path, ok=False, error=f"unreadable: {exc}")
        data = _parse_frontmatter(text)
        if data is None:
            return SkillMeta(name, "", path, ok=False, error="invalid frontmatter")

        errors: list[str] = []
        fm_name = data.get("name")
        if not isinstance(fm_name, str) or not fm_name.strip():
            errors.append("missing name")
        elif not _NAME_RE.fullmatch(fm_name):
            errors.append("invalid name (a-z, 0-9, hyphens)")
        elif len(fm_name) > NAME_MAX:
            errors.append("name longer than 64 chars")
        elif fm_name != name:
            errors.append("name does not match folder")

        description = ""
        raw_description = data.get("description")
        if not isinstance(raw_description, str) or not raw_description.strip():
            errors.append("missing description")
        else:
            description = raw_description.strip()
            if len(description) > DESCRIPTION_MAX:
                errors.append("description longer than 1024 chars")

        if errors:
            return SkillMeta(name, description, path, ok=False, error=errors[0])
        return SkillMeta(name, description, path, ok=True)


def _parse_frontmatter(text: str) -> dict | None:
    match = _FM_RE.match(text)
    if match is None:
        return None
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None