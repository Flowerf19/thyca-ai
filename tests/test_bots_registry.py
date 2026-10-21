"""BE-1: TWIN.md card parsing/validation + BotRegistry + Thyca seed."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from thyca.bots.card import BotCard, parse_bot_card, render_bot_card
from thyca.bots.registry import BotRegistry, RegistryError

VALID = """---
name: coder
role: Coder
description: Writes code carefully.
tint: accent
version: 1
model:
  id: default
skills: []
tools: []
hidden_tools: []
bash: ask
memory:
  read: true
  write: true
  propose_team: true
schedule: null
---

# Coder persona
You write code.
"""


def test_parse_valid_card() -> None:
    doc = parse_bot_card(VALID, "coder")
    assert doc.ok and doc.card is not None
    assert doc.card.name == "coder"
    assert doc.card.role == "Coder"
    assert doc.card.bash == "ask"
    assert doc.card.memory.propose_team is True
    assert doc.card.persona.strip().startswith("# Coder persona")


def test_parse_display_name_is_independent_from_folder_id() -> None:
    doc = parse_bot_card(
        VALID.replace("name: coder", "name: 'Trợ lý viết code'"), "coder"
    )
    assert doc.ok and doc.card is not None
    assert doc.card.bot_id == "coder"
    assert doc.card.name == "Trợ lý viết code"


def test_parse_rejects_invalid_display_names() -> None:
    valid = parse_bot_card(VALID, "coder").card
    assert valid is not None
    for name in ("", "   ", "x" * 121, "two\nlines", "bad\x01name"):
        doc = parse_bot_card(render_bot_card(replace(valid, name=name)), "coder")
        assert not doc.ok, repr(name)


def test_parse_rejects_invalid_folder_ids() -> None:
    for bad in ("../etc", "has space", "x" * 65, "UPPER"):
        doc = parse_bot_card(VALID, bad)
        assert not doc.ok, bad


def test_parse_rejects_hex_tint_and_bad_bash() -> None:
    doc = parse_bot_card(VALID.replace("tint: accent", "tint: '#ff0000'"), "coder")
    assert not doc.ok
    doc = parse_bot_card(VALID.replace("bash: ask", "bash: always"), "coder")
    assert not doc.ok
    assert "bash" in doc.error


def test_parse_requires_role_and_description() -> None:
    doc = parse_bot_card(VALID.replace("role: Coder", "role: ''"), "coder")
    assert not doc.ok
    doc = parse_bot_card(VALID.replace("description: Writes code carefully.", ""), "coder")
    assert not doc.ok


def test_render_round_trip() -> None:
    doc = parse_bot_card(VALID, "coder")
    assert doc.ok and doc.card is not None
    doc2 = parse_bot_card(render_bot_card(doc.card), "coder")
    assert doc2.ok and doc2.card == doc.card


def test_registry_seeds_thyca_system_card(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    card = registry.ensure_thyca()
    assert card.bot_id == "thyca"
    assert card.system is True
    assert (tmp_path / "bots" / "thyca" / "TWIN.md").is_file()


def test_registry_create_pause_restore_delete(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    card = BotCard(bot_id="coder", name="coder", role="Coder",
                   description="Writes code.", persona="You code.")
    created = registry.create(card)
    assert created.bot_id == "coder"
    with pytest.raises(RegistryError):
        registry.create(card)
    paused = registry.set_paused("coder", True)
    assert paused.paused is True
    # Team index skips paused bots.
    assert "coder" not in registry.team_index_text()
    registry.set_paused("coder", False)
    assert "coder" in registry.team_index_text()
    renamed = registry.update_card("coder", replace(card, name="code-helper"))
    assert renamed.bot_id == "coder"
    assert renamed.name == "code-helper"
    assert registry.list()[0].card.bot_id == "coder"
    registry.delete("coder")
    assert not registry.exists("coder")


def test_registry_protects_system_bot(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    with pytest.raises(RegistryError):
        registry.delete("thyca")
    with pytest.raises(RegistryError):
        registry.set_paused("thyca", True)

    card = registry.require("thyca")
    with pytest.raises(RegistryError, match="cannot rename"):
        registry.update_card("thyca", replace(card, name="Thyca trợ lý"))
    assert registry.require("thyca") == card

    updated = registry.update_card(
        "thyca", replace(card, description="Updated system description."),
    )
    assert updated.name == card.name
    assert updated.system is True
    assert updated.description == "Updated system description."


def test_registry_team_index_and_public_list(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    index = registry.team_index_text()
    assert "thyca" in index
    public = registry.public_list()
    assert public[0]["id"] == "thyca"
    assert public[0]["status"] == "active"
    assert set(public[0]) == {
        "id", "name", "role", "description", "tint", "avatar", "status", "system",
        "model", "bash", "skills", "schedule",
    }
    assert public[0]["avatar"] == "star"


def test_registry_skill_index_private_wins(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    private = tmp_path / "bots" / "thyca" / "skills" / "mine"
    private.mkdir(parents=True)
    (private / "SKILL.md").write_text(
        "---\nname: mine\ndescription: private copy\n---\nbody\n", encoding="utf-8")
    team = tmp_path / "skills-team" / "mine"
    team.mkdir(parents=True)
    (team / "SKILL.md").write_text(
        "---\nname: mine\ndescription: team copy\n---\nbody\n", encoding="utf-8")
    index = registry.skill_index_text("thyca")
    assert "private copy" in index
    assert "team copy" not in index


# --- TWIN.md rename (2026-10-13): legacy BOT.md readable, writes canonical --

def test_legacy_bot_md_card_stays_readable(tmp_path: Path) -> None:
    folder = tmp_path / "bots" / "coder"
    folder.mkdir(parents=True)
    (folder / "BOT.md").write_text(VALID, encoding="utf-8")
    registry = BotRegistry(tmp_path)
    doc = registry.get("coder")
    assert doc.ok and doc.card is not None
    assert doc.card.bot_id == "coder"
    assert registry.exists("coder")


def test_update_card_migrates_legacy_bot_md_to_twin_md(tmp_path: Path) -> None:
    folder = tmp_path / "bots" / "coder"
    folder.mkdir(parents=True)
    (folder / "BOT.md").write_text(VALID, encoding="utf-8")
    registry = BotRegistry(tmp_path)
    card = registry.require("coder")
    registry.update_card("coder", card)
    assert (folder / "TWIN.md").is_file()
    assert not (folder / "BOT.md").exists(), "legacy copy must not linger"
    assert registry.require("coder").role == "Coder"
