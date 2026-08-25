from __future__ import annotations

import pytest

from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveSnapshot


def _hot(**overrides: str) -> ActiveSnapshot:
    base = dict(soul="soul-text", user="user-text", today="today-text", yesterday="")
    base.update(overrides)
    return ActiveSnapshot(**base)


def test_build_order_identity_then_custom_soul() -> None:
    manager = PromptManager()
    text = manager.build(_hot())
    identity = manager.template("identity")
    assert text.startswith(f"<identity>\n{identity}\n</identity>\n<role>\nsoul-text\n</role>\n")
    assert "<user>\nuser-text\n</user>" in text
    assert text.index("<identity>") < text.index("<role>") < text.index("<user>")
    assert "<memory>" not in text
    assert text.index("<today>") < text.index("<rules>")
    assert "~/.thyca" in text
    assert "mcpServers" in text
    assert "do not say you cannot add tools" in text
    assert "restart" in text
    assert "Thyca" in identity


def test_live_identity_wins_over_template() -> None:
    text = PromptManager().build(_hot(identity="# Identity\nName: Live\n"))
    assert "Name: Live" in text
    assert "Name: Thyca" not in text


def test_stub_soul_uses_packaged_template_and_omits_stub_user() -> None:
    text = PromptManager().build(_hot(soul="# Soul\n", user="# User\n"))
    assert "<identity>" in text
    assert "You are Thyca" in text
    assert "Name: Thyca" in text
    assert "<user>" not in text


def test_yesterday_only_when_present() -> None:
    assert "<yesterday>" not in PromptManager().build(_hot())
    text = PromptManager().build(_hot(yesterday="yday-text"))
    assert "<yesterday>\nyday-text\n</yesterday>" in text
    assert text.index("<today>") < text.index("<yesterday>") < text.index("<rules>")


def test_build_is_deterministic() -> None:
    hot = _hot(yesterday="keep")
    manager = PromptManager()
    assert manager.build(hot) == manager.build(hot)


def test_soul_and_identity_templates() -> None:
    manager = PromptManager()
    soul = manager.template("soul")
    identity = manager.template("identity")
    assert soul.startswith("# Soul\n")
    assert "Thyca" in soul
    assert identity.startswith("# Identity\n")
    assert "Thyca" in identity
    with pytest.raises(ValueError, match="unknown prompt template"):
        manager.template("user")
