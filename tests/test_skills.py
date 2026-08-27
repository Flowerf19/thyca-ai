from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveMemory, ActiveSnapshot
from thyca.skills import SkillStore, _PACKAGED_SKILLS


@pytest.fixture
def thyca_dir(tmp_path: Path) -> Path:
    return tmp_path / "thyca"


def _now() -> datetime:
    return datetime(2026, 8, 28, 10, 0)


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "# Body\n") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return path


def _ok_skill(root: Path, name: str, description: str = "Does the thing. Use when testing.", body: str = "# Body\n") -> Path:
    return _write_skill(root, name, f"name: {name}\ndescription: {description}", body=body)


def test_valid_skill_indexed(thyca_dir: Path) -> None:
    store = SkillStore(thyca_dir)
    _ok_skill(store.root, "weather")
    (meta,) = store.list_meta()
    assert meta.ok and meta.name == "weather"
    assert meta.description == "Does the thing. Use when testing."


def test_name_rules_and_folder_match(thyca_dir: Path) -> None:
    store = SkillStore(thyca_dir)
    _write_skill(store.root, "Bad_Name", "name: Bad_Name\ndescription: x")
    _write_skill(store.root, "-lead", "name: -lead\ndescription: x")
    _write_skill(store.root, "double--hyphen", "name: double--hyphen\ndescription: x")
    _write_skill(store.root, "weather", "name: climate\ndescription: x")
    _write_skill(store.root, "too-long", f"name: {'a' * 65}\ndescription: x")
    errors = {meta.name: meta.error for meta in store.list_meta() if not meta.ok}
    assert errors["Bad_Name"] == "invalid name (a-z, 0-9, hyphens)"
    assert errors["-lead"] == "invalid name (a-z, 0-9, hyphens)"
    assert errors["double--hyphen"] == "invalid name (a-z, 0-9, hyphens)"
    assert errors["weather"] == "name does not match folder"
    assert errors["too-long"] == "name longer than 64 chars"


def test_description_rules(thyca_dir: Path) -> None:
    store = SkillStore(thyca_dir)
    _write_skill(store.root, "no-desc", "name: no-desc")
    _write_skill(store.root, "empty-desc", "name: empty-desc\ndescription: '   '")
    _write_skill(store.root, "long-desc", f"name: long-desc\ndescription: {'x' * 1025}")
    errors = {meta.name: meta.error for meta in store.list_meta() if not meta.ok}
    assert errors["no-desc"] == "missing description"
    assert errors["empty-desc"] == "missing description"
    assert errors["long-desc"] == "description longer than 1024 chars"


def test_yaml_frontmatter_errors_and_unknown_fields(thyca_dir: Path) -> None:
    store = SkillStore(thyca_dir)
    _write_skill(store.root, "broken-yaml", "name: [unclosed")
    _write_skill(store.root, "no-frontmatter", "")
    _write_skill(store.root, "scalar-frontmatter", "just a string")
    ok_path = _write_skill(
        store.root, "extra", "name: extra\ndescription: fine\nlicense: MIT\nmetadata:\n  key: value"
    )
    errors = {meta.name: meta.error for meta in store.list_meta() if not meta.ok}
    assert errors["broken-yaml"] == "invalid frontmatter"
    assert errors["no-frontmatter"] == "invalid frontmatter"
    assert errors["scalar-frontmatter"] == "invalid frontmatter"
    metas = {meta.name: meta for meta in store.list_meta()}
    assert metas["extra"].ok and metas["extra"].path == ok_path


def test_missing_skill_md_and_loose_files(thyca_dir: Path) -> None:
    store = SkillStore(thyca_dir)
    (store.root / "empty").mkdir(parents=True)
    (store.root / "loose.md").write_text("---\nname: loose\n---\n", encoding="utf-8")
    metas = store.list_meta()
    assert [meta.name for meta in metas] == ["empty"]
    assert not metas[0].ok and metas[0].error == "missing SKILL.md"


def test_index_text_format_and_truncation(thyca_dir: Path) -> None:
    store = SkillStore(thyca_dir)
    _ok_skill(store.root, "good", description="short description")
    _ok_skill(store.root, "wordy", description="w" * 400)
    _write_skill(store.root, "bad", "name: bad")
    lines = store.index_text().splitlines()
    by_name = {line.split(" ")[1]: line for line in lines}
    assert by_name["good"] == "- good — short description"
    assert by_name["wordy"].startswith("- wordy — ")
    assert by_name["wordy"].endswith("…") and len(by_name["wordy"]) < 280
    assert by_name["bad"] == "- bad (SKILL.md invalid: missing description)"


def test_index_body_oversize_warning(thyca_dir: Path) -> None:
    store = SkillStore(thyca_dir)
    _ok_skill(store.root, "big", body="# Body\n" + "x" * 33_000)
    _ok_skill(store.root, "small")
    lines = store.index_text().splitlines()
    assert "larger than 32KB" in lines[0]
    assert "larger than 32KB" not in lines[1]


def test_missing_root_is_empty(thyca_dir: Path) -> None:
    assert SkillStore(thyca_dir).list_meta() == []
    assert SkillStore(thyca_dir).index_text() == ""


def test_ensure_defaults_seeds_and_preserves(thyca_dir: Path) -> None:
    store = SkillStore(thyca_dir)
    store.ensure_defaults()
    names = {meta.name for meta in store.list_meta()}
    assert {"create-skill", "create-mcp-tool"} <= names
    seeded = (store.root / "create-skill" / "SKILL.md").read_text(encoding="utf-8")
    store.ensure_defaults()
    assert (store.root / "create-skill" / "SKILL.md").read_text(encoding="utf-8") == seeded
    (store.root / "create-skill" / "SKILL.md").write_text("edited", encoding="utf-8")
    store.ensure_defaults()
    assert (store.root / "create-skill" / "SKILL.md").read_text(encoding="utf-8") == "edited"


def test_packaged_templates_are_spec_valid(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    store.ensure_defaults()
    assert store.list_meta()
    assert all(meta.ok for meta in store.list_meta())


def test_active_memory_seeds_and_exposes_index(thyca_dir: Path) -> None:
    memory = ActiveMemory(thyca_dir)
    state = memory.open_session(_now())
    snapshot = memory.refresh(state, _now())
    assert "- create-skill — " in snapshot.skills
    assert "- create-mcp-tool — " in snapshot.skills
    broken = SkillStore(thyca_dir).root / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no fm", encoding="utf-8")
    snapshot = memory.refresh(state, _now())
    assert "- broken (SKILL.md invalid: invalid frontmatter)" in snapshot.skills


def _hot(**overrides: str) -> ActiveSnapshot:
    base = dict(soul="soul-text", user="user-text", today="today-text", yesterday="")
    base.update(overrides)
    return ActiveSnapshot(**base)


def test_prompt_renders_skills_before_rules() -> None:
    text = PromptManager().build(_hot(skills="- create-skill — write skills"))
    assert "<skills>\n- create-skill — write skills\n</skills>" in text
    assert text.index("<skills>") < text.index("<rules>")


def test_prompt_omits_skills_when_empty() -> None:
    # "<skills>" appears inside _RULES text; the section only renders with a closing tag.
    assert "</skills>" not in PromptManager().build(_hot())


def test_rules_point_to_seeded_skills() -> None:
    rules = PromptManager().rules_section()
    assert "create-skill" in rules
    assert "create-mcp-tool" in rules
    assert "FastMCP" not in rules


def test_packaged_templates_exist() -> None:
    assert (_PACKAGED_SKILLS / "create-skill" / "SKILL.md").is_file()
    assert (_PACKAGED_SKILLS / "create-mcp-tool" / "SKILL.md").is_file()