"""Twin Card (TWIN.md) parsing and validation.

File-first like skills (decision 2026-08-28): each twin bot is a directory
``<bot-id>/`` containing ``TWIN.md`` with YAML frontmatter (``name`` +
card fields) plus a markdown body (persona / ways of working). This module
only parses and validates; :mod:`thyca.bots.registry` owns the directory.
Cards written by earlier builds as ``BOT.md`` remain readable (registry
fallback); new writes always use ``TWIN.md``.
"""
from __future__ import annotations

import re
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from thyca.skills.store import _FM_RE, _NAME_RE, DESCRIPTION_MAX, NAME_MAX

VERSION = 1

#: Card file names. ``TWIN.md`` is canonical (user decision 2026-10-13:
#: every bot in the team is a twin of Thyca); ``BOT.md`` is the legacy
#: name kept readable for directories created before the rename.
CARD_FILENAME = "TWIN.md"
LEGACY_CARD_FILENAME = "BOT.md"
CARD_FILENAMES = (CARD_FILENAME, LEGACY_CARD_FILENAME)

BASH_MODES = frozenset({"ask", "allow", "deny"})

#: Tint is a theme-token name, never a hex literal (contract §6/FE-6).
_TINT_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

#: The team palette (mirrors BOT_TINTS in team-data.js — the tokens the
#: webui resolves through var(--color-…)). Used only to suggest a
#: distinct tint at creation time; any other token-shaped value in a
#: card stays valid and wins.
TEAM_TINTS: tuple[str, ...] = ("accent", "botanical", "success-ink", "ink", "accent-deep")

#: Avatar note variants (design "Bot Avatar System" v1.1). The shape is
#: a card property; the colour stays the card's ``tint``. ``star`` is
#: reserved for the Thyca manager; child bots use the other six.
AVATAR_VARIANTS: tuple[str, ...] = (
    "star", "hollow", "single-flag", "double-flag", "dotted", "fermata", "plain",
)
CHILD_AVATAR_VARIANTS: tuple[str, ...] = AVATAR_VARIANTS[1:]

#: Exact legacy ids from the design registry (plus their thyca- forms).
_AVATAR_BY_ID: dict[str, str] = {
    "designer": "hollow",
    "thyca-designer": "hollow",
    "coder": "single-flag",
    "thyca-coder": "single-flag",
    "researcher": "double-flag",
    "thyca-researcher": "double-flag",
    "bot-a": "dotted",
    "thyca-bot-a": "dotted",
    "bot-b": "fermata",
    "thyca-bot-b": "fermata",
    "generic": "plain",
    "thyca-generic": "plain",
}

#: Tools every newly created bot receives so it can immediately talk to
#: peers and use its own memory (peer send + remember/search/get/propose +
#: open task catalog). Creation paths union these with explicitly requested
#: tools; the card's ``hidden_tools`` and memory/bash policies still win
#: at filter time.
BASELINE_BOT_TOOLS: tuple[str, ...] = (
    "bot_send",
    "bot_fetch",
    "task_open",
    "message_user",
    "ask_user",
    "ask_private",
    "private_followup",
    "report_error",
    "memory_remember",
    "memory_search",
    "memory_get",
    "memory_propose_team",
    "memory_tasks",
    "compact",
)


def with_baseline_tools(tools) -> tuple[str, ...]:
    """Baseline tools first, then explicit tools, deduplicated in order."""
    merged: list[str] = list(BASELINE_BOT_TOOLS)
    for name in tools or ():
        if name not in merged:
            merged.append(name)
    return tuple(merged)


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
    """Validated view of one ``TWIN.md``. ``bot_id`` is the folder name."""

    bot_id: str
    name: str
    role: str
    description: str
    tint: str = "accent"
    avatar: str | None = None
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
    """Parse + validate ``TWIN.md`` text for folder ``bot_id``.

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
    if not isinstance(name, str):
        return BotCardDoc(None, ok=False, error="missing name")
    if any(
        unicodedata.category(char) == "Cc" or char in "\u2028\u2029"
        for char in name
    ):
        return BotCardDoc(None, ok=False, error="invalid name (control characters)")
    name = name.strip()
    if not name:
        return BotCardDoc(None, ok=False, error="missing name")
    if len(name) > 120:
        return BotCardDoc(None, ok=False, error="name longer than 120 chars")

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

    avatar = data.get("avatar")
    if avatar is not None and avatar not in AVATAR_VARIANTS:
        return BotCardDoc(None, ok=False, error=f"invalid avatar (want one of {', '.join(AVATAR_VARIANTS)})")

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
            avatar=avatar,
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
    """Serialize a card back to ``TWIN.md`` text (frontmatter + persona)."""
    payload: dict[str, object] = {
        "name": card.name,
        "role": card.role,
        "description": card.description,
        "tint": card.tint,
        "version": card.version,
    }
    if card.avatar is not None:
        payload["avatar"] = card.avatar
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


def effective_avatar(card: BotCard) -> str:
    """The avatar variant a card renders with.

    An explicit ``avatar`` field wins; the manager is always the star;
    design-registry ids keep their shapes; every other legacy card
    gets a stable per-id variant so existing teams gain distinct
    avatars without rewriting any ``TWIN.md``."""
    if card.system or card.bot_id == "thyca":
        return "star"
    if card.avatar in AVATAR_VARIANTS:
        return str(card.avatar)
    known = _AVATAR_BY_ID.get(card.bot_id)
    if known is not None:
        return known
    return CHILD_AVATAR_VARIANTS[
        zlib.crc32(card.bot_id.encode("utf-8")) % len(CHILD_AVATAR_VARIANTS)
    ]


def identity_text(card: BotCard) -> str:
    """The ``<identity>`` prompt section for one twin, derived from its card.

    Identity is two-layered (user decision 2026-10-13): the shared soul
    (``SOUL.md``) governs how every twin behaves and never names anyone;
    who-this-bot-is comes only from its own card. Child turns must never
    receive Thyca's ``IDENTITY.md`` — that leak made children introduce
    themselves as Thyca and delegate to themselves in third person.
    """
    lines = ["# Identity", "", f"Name: {card.name}"]
    if card.role.strip():
        lines.append(f"Role: {card.role.strip()}")
    if card.description.strip():
        lines.append(card.description.strip())
    lines += [
        "",
        "You are a twin bot in Thyca's team: you share the team's soul "
        "(voice, values, working rules) but you are your own bot with "
        "your own name, role, memory, and skills. Thyca is the team's "
        "manager bot, a teammate — not you. When you introduce yourself "
        "or refer to yourself, use your own name above; refer to Thyca "
        "and other bots as teammates.",
    ]
    return "\n".join(lines)


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
