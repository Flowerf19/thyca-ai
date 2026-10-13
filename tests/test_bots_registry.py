"""BE-1: Agent Card parsing/validation + BotRegistry + Thyca seed."""
from __future__ import annotations

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


def test_parse_name_must_match_folder() -> None:
    doc = parse_bot_card(VALID, "other")
    assert not doc.ok
    assert "folder" in doc.error


def test_parse_rejects_bad_names() -> None:
    for bad in ("Coder", "has space", "x" * 65, "UPPER"):
        doc = parse_bot_card(VALID.replace("name: coder", f"name: {bad}"), bad.lower())
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
    assert (tmp_path / "bots" / "thyca" / "BOT.md").is_file()


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
    registry.delete("coder")
    assert not registry.exists("coder")


def test_registry_protects_system_bot(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    with pytest.raises(RegistryError):
        registry.delete("thyca")
    with pytest.raises(RegistryError):
        registry.set_paused("thyca", True)


def test_registry_team_index_and_public_list(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    index = registry.team_index_text()
    assert "thyca" in index
    public = registry.public_list()
    assert public[0]["id"] == "thyca"
    assert public[0]["status"] == "active"
    assert set(public[0]) == {"id", "name", "role", "description", "tint", "status", "system"}


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
