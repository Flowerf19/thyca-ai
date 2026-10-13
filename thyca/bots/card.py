"""Agent Card (BOT.md) parsing and validation.

File-first like skills (decision 2026-08-28): each bot is a directory
``<bot-id>/`` containing ``BOT.md`` with YAML frontmatter (``name`` +
card fields) plus a markdown body (persona / ways of working). This module
only parses and validates; :mod:`thyca.bots.registry` owns the directory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from thyca.skills.store import _FM_RE, _NAME_RE, DESCRIPTION_MAX, NAME_MAX

VERSION = 1

BASH_MODES = frozenset({"ask", "allow", "deny"})

#: Tint is a theme-token name, never a hex literal (contract §6/FE-6).
_TINT_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


@dataclass(frozen=True)
class BotModel:
    id: str
    thinking: str | None = None


@dataclass(frozen=True)
class BotMemoryPolicy:
    read: bool = True
    write: bool = True
    propose_team: bool = True


@dataclass(frozen=True)
class BotCard:
    """Validated view of one ``BOT.md``. ``bot_id`` is the folder name."""

    bot_id: str
    name: str
    role: str
    description: str
    tint: str = "accent"
    version: int = VERSION
    model: BotModel | None = None
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    hidden_tools: tuple[str, ...] = ()
    bash: str = "ask"
    memory: BotMemoryPolicy = field(default_factory=BotMemoryPolicy)
    schedule: str | None = None
    system: bool = False
    paused: bool = False
    persona: str = ""


@dataclass(frozen=True)
class BotCardDoc:
    card: BotCard | None
    ok: bool
    error: str = ""


def parse_bot_card(text: str, bot_id: str) -> BotCardDoc:
    """Parse + validate ``BOT.md`` text for folder ``bot_id``.

    Returns ``ok=False`` with a short machine-readable ``error`` instead of
    raising, so a broken card shows up as a warning line (skills-store
    pattern) and never bricks the registry listing.
    """
    if not _NAME_RE.fullmatch(bot_id) or len(bot_id) > NAME_MAX:
        return BotCardDoc(None, ok=False, error="invalid bot id")
    match = _FM_RE.match(text)
    if match is None:
        return BotCardDoc(None, ok=False, error="invalid frontmatter")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return BotCardDoc(None, ok=False, error="invalid frontmatter")
    if not isinstance(data, dict):
        return BotCardDoc(None, ok=False, error="invalid frontmatter")
    body = text[match.end():]

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return BotCardDoc(None, ok=False, error="missing name")
    if not _NAME_RE.fullmatch(name) or len(name) > NAME_MAX:
        return BotCardDoc(None, ok=False, error="invalid name (a-z, 0-9, hyphens)")
    if name != bot_id:
        return BotCardDoc(None, ok=False, error="name does not match folder")

    role = data.get("role")
    if not isinstance(role, str) or not role.strip():
        return BotCardDoc(None, ok=False, error="missing role")

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        return BotCardDoc(None, ok=False, error="missing description")
    description = description.strip()
    if len(description) > DESCRIPTION_MAX:
        return BotCardDoc(None, ok=False, error="description longer than 1024 chars")

    tint = data.get("tint", "accent")
    if not isinstance(tint, str) or not _TINT_RE.fullmatch(tint.strip()):
        return BotCardDoc(None, ok=False, error="invalid tint (theme token, no hex)")
    tint = tint.strip()

    version = data.get("version", VERSION)
    if version != VERSION:
        return BotCardDoc(None, ok=False, error=f"unsupported version (want {VERSION})")

    model = _parse_model(data.get("model"))
    if model is None and "model" in data:
        return BotCardDoc(None, ok=False, error="invalid model (need {id})")

    skills, ok = _parse_str_list(data.get("skills"))
    if not ok:
        return BotCardDoc(None, ok=False, error="invalid skills (need list of str)")
    tools, ok = _parse_str_list(data.get("tools"))
    if not ok:
        return BotCardDoc(None, ok=False, error="invalid tools (need list of str)")
    hidden, ok = _parse_str_list(data.get("hidden_tools"))
    if not ok:
        return BotCardDoc(None, ok=False, error="invalid hidden_tools (need list of str)")

    bash = data.get("bash", "ask")
    if bash not in BASH_MODES:
        return BotCardDoc(None, ok=False, error="invalid bash (ask|allow|deny)")

    memory = _parse_memory(data.get("memory"))
    if memory is None and "memory" in data and data.get("memory") is not None:
        return BotCardDoc(None, ok=False, error="invalid memory (need {read,write,propose_team})")

    schedule = data.get("schedule")
    if schedule is not None and (not isinstance(schedule, str) or not schedule.strip()):
        return BotCardDoc(None, ok=False, error="invalid schedule (cron string or null)")

    system = data.get("system", False)
    if not isinstance(system, bool):
        return BotCardDoc(None, ok=False, error="invalid system (need bool)")

    paused = data.get("paused", False)
    if not isinstance(paused, bool):
        return BotCardDoc(None, ok=False, error="invalid paused (need bool)")

    return BotCardDoc(
        BotCard(
            bot_id=bot_id,
            name=name,
            role=role.strip(),
            description=description,
            tint=tint,
            version=VERSION,
            model=model,
            skills=skills,
            tools=tools,
            hidden_tools=hidden,
            bash=bash,
            memory=memory or BotMemoryPolicy(),
            schedule=schedule.strip() if isinstance(schedule, str) else None,
            system=system,
            paused=paused,
            persona=body.strip("\n"),
        ),
        ok=True,
    )


def render_bot_card(card: BotCard) -> str:
    """Serialize a card back to ``BOT.md`` text (frontmatter + persona)."""
    payload: dict[str, object] = {
        "name": card.name,
        "role": card.role,
        "description": card.description,
        "tint": card.tint,
        "version": card.version,
    }
    if card.model is not None:
        model: dict[str, object] = {"id": card.model.id}
        if card.model.thinking:
            model["thinking"] = card.model.thinking
        payload["model"] = model
    if card.skills:
        payload["skills"] = list(card.skills)
    if card.tools:
        payload["tools"] = list(card.tools)
    if card.hidden_tools:
        payload["hidden_tools"] = list(card.hidden_tools)
    payload["bash"] = card.bash
    payload["memory"] = {
        "read": card.memory.read,
        "write": card.memory.write,
        "propose_team": card.memory.propose_team,
    }
    payload["schedule"] = card.schedule
    if card.system:
        payload["system"] = True
    if card.paused:
        payload["paused"] = True
    front = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    return f"---\n{front}---\n{card.persona.strip()}\n"


def read_bot_file(path: Path, bot_id: str) -> BotCardDoc:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return BotCardDoc(None, ok=False, error=f"unreadable: {exc}")
    return parse_bot_card(text, bot_id)


def _parse_model(raw: object) -> BotModel | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    model_id = raw.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    thinking = raw.get("thinking")
    if thinking is not None and (not isinstance(thinking, str) or not thinking.strip()):
        return None
    return BotModel(id=model_id.strip(), thinking=thinking.strip() if isinstance(thinking, str) else None)


def _parse_str_list(raw: object) -> tuple[tuple[str, ...], bool]:
    if raw is None:
        return (), True
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        return (), False
    return tuple(item for item in raw), True


def _parse_memory(raw: object) -> BotMemoryPolicy | None:
    if raw is None:
        return BotMemoryPolicy()
    if not isinstance(raw, dict):
        return None
    values = {}
    for key in ("read", "write", "propose_team"):
        value = raw.get(key, True)
        if not isinstance(value, bool):
            return None
        values[key] = value
    return BotMemoryPolicy(**values)
