from __future__ import annotations

import pytest

from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveSnapshot


def _hot(**overrides: str) -> ActiveSnapshot:
    base = dict(soul="soul-text", user="user-text", memory="mem-text", today="today-text", yesterday="")
    base.update(overrides)
    return ActiveSnapshot(**base)


def test_build_order_and_rules() -> None:
    text = PromptManager().build(_hot())
    assert text == (
        "<role>\nsoul-text\n</role>\n"
        "<user>\nuser-text\n</user>\n"
        "<memory>\nmem-text\n</memory>\n"
        "<today>\ntoday-text\n</today>\n"
        "<rules>\n"
        f"{PromptManager().rules_section()}\n"
        "</rules>"
    )
    assert "~/.thyca" in text
    assert "memory_remember" in text
    assert "memory_search" in text


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
