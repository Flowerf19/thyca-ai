from __future__ import annotations

from pathlib import Path

import pytest

from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveSnapshot


def _hot(**overrides: str) -> ActiveSnapshot:
    base = {"soul": "soul-text", "user": "user-text", "today": "today-text"}
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
    assert "create-skill" in text
    assert "create-mcp-tool" in text
    assert "no sandbox" in text
    assert "Thyca" in identity


def test_live_identity_wins_over_template() -> None:
    text = PromptManager().build(_hot(identity="# Identity\nName: Live\n"))
    assert "Name: Live" in text
    assert "Name: Thyca" not in text


@pytest.mark.parametrize("soul", ["", "# Soul\n", " \n# Soul \n"])
@pytest.mark.parametrize("user", ["", "# User\n", " \n# User \n"])
def test_stub_soul_uses_packaged_template_and_omits_stub_user(soul: str, user: str) -> None:
    manager = PromptManager()
    text = manager.build(_hot(soul=soul, user=user))
    assert f"<role>\n{manager.template('soul')}\n</role>" in text
    assert "Name: Thyca" in text
    assert "</user>" not in text


@pytest.mark.parametrize("identity", ["", "# Identity\n", " \n# Identity \n"])
def test_stub_identity_uses_packaged_template(identity: str) -> None:
    manager = PromptManager()
    text = manager.build(_hot(identity=identity))
    assert text.startswith(f"<identity>\n{manager.template('identity')}\n</identity>")


def test_build_does_not_inject_previous_day_memory() -> None:
    text = PromptManager().build(_hot())
    assert "<yesterday>" not in text


def test_build_is_deterministic() -> None:
    hot = _hot()
    manager = PromptManager()
    assert manager.build(hot) == manager.build(hot)


@pytest.mark.parametrize("name", ["soul", "identity", "user"])
def test_packaged_profile_templates(name: str) -> None:
    manager = PromptManager()
    template = manager.template(name)
    assert template.startswith(f"# {name.title()}\n")
    assert manager.template(f" {name.upper()} ") == template


@pytest.mark.parametrize("name", ["unknown", "../user", "../../read_before_config", "user.md", "rules"])
def test_unknown_template_is_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="unknown prompt template"):
        PromptManager().template(name)


def test_packaged_persona_is_general_purpose() -> None:
    manager = PromptManager()
    soul = " ".join(manager.template("soul").split())
    identity = " ".join(manager.template("identity").split())
    assert "terminal" not in soul.lower()
    assert "terminal" not in identity.lower()
    assert "Name: Thyca" in identity
    assert "thi ca" in identity
    assert "general-purpose personal assistant" in identity
    assert "Coding is one capability, not your identity" in identity
    assert "identity does not depend on the interface or model" in identity
    assert "limited to the context and tools actually provided" in identity
    assert "Do not assume hidden resources, agents, or permissions" in identity
    assert "Speak the user's language" in soul
    assert "Learn their preferred forms of address" in soul
    assert "do not impose a fixed pronoun style" in soul
    assert "thi ca" not in soul
    assert "memory_remember" not in identity


def test_soul_covers_memory_lifecycle_and_boundaries() -> None:
    soul = " ".join(PromptManager().template("soul").split())
    assert "Recall relevant memory when a request depends on earlier conversations" in soul
    assert "not as a ritual on every turn" in soul
    assert "Try a better-grounded keyword or ask the user" in soul
    assert "Verify current files, systems, or services" in soul
    assert "when the user asks you to remember it" in soul
    assert "only report it saved after a successful write" in soul
    assert "Lasting facts and preferences about the user belong in USER.md" in soul
    assert "belong in daily memory" in soul
    assert "preserve events that were true at the time" in soul
    assert "Do not delete historical notes merely because circumstances changed" in soul
    assert "Never persist credentials or secrets" in soul
    assert "Ask before storing sensitive personal information" in soul
    assert "Change SOUL.md or IDENTITY.md only when the user explicitly requests it" in soul


def test_rules_loaded_from_packaged_file() -> None:
    rules_path = Path(__file__).resolve().parents[1] / "thyca/seeds/prompts/rules.md"
    rules = rules_path.read_text(encoding="utf-8").rstrip("\n")
    manager = PromptManager()
    assert manager.rules_section() == rules
    assert manager.build(_hot()).endswith(f"<rules>\n{rules}\n</rules>")


def test_runtime_guidance_has_one_owner() -> None:
    manager = PromptManager()
    soul = manager.template("soul")
    rules = manager.rules_section()
    assert "Check <skills>" in rules
    assert "<skills>" not in soul
    assert "Today's memory is automatically included in <today> as the daily file's tail" in rules
    assert "even if the user has not repeated it in this conversation" in rules
    assert "Do not search or reread information already present in <today>" in rules
    assert "Today's daily file is not in archive search" in rules
    assert "use read on" not in rules
    assert "lexical" not in rules
    # Tool descriptions own individual operations and their calling conventions.
    for name in ("recent", "get", "reinforce", "forget"):
        assert f"memory_{name}" not in soul


def test_user_template_has_upkeep_and_empty_profile_sections() -> None:
    user = PromptManager().template("user")
    guidance = " ".join(user.split())
    assert "explicitly shared or confirmed by the user" in guidance
    assert "Do not mistake quoted text or information about other people" in guidance
    assert "Current explicit instructions take precedence over stale entries" in guidance
    assert "Never store credentials or secrets" in guidance
    assert "Obtain consent before adding sensitive personal information" in guidance
    for heading in (
        "Upkeep",
        "Name and forms of address",
        "Language and communication preferences",
        "Stable personal and working context",
        "Preferences and boundaries",
        "Long-term goals and responsibilities",
    ):
        assert f"## {heading}\n" in user
    profile = user[user.index("## Name and forms of address"):]
    assert all(not line.strip() or line.startswith("## ") for line in profile.splitlines())
