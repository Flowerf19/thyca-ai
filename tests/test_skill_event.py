from __future__ import annotations

from pathlib import Path

from thyca.agent.skill_event import classify_skill_read, public_skill_name


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    (root / "create-skill").mkdir(parents=True)
    (root / "create-skill" / "SKILL.md").write_text("---\nname: create-skill\n---\n")
    return root


def test_read_skill_md_classifies(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert classify_skill_read(root, root / "create-skill" / "SKILL.md") == "create-skill"


def test_read_resource_inside_skill_classifies(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert classify_skill_read(root, root / "create-skill" / "references" / "api.md") == (
        "create-skill"
    )


def test_read_outside_skills_root_returns_none(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert classify_skill_read(root, tmp_path / "MEMORY.md") is None
    assert classify_skill_read(root, root) is None


def test_symlinked_thyca_dir_still_classifies(tmp_path: Path) -> None:
    # A symlinked ~/.thyca must not silently downgrade skill reads to tool.*.
    real = _root(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(real.parent, target_is_directory=True)
    assert classify_skill_read(link / "skills", real / "create-skill" / "SKILL.md") == (
        "create-skill"
    )


def test_lexical_dotdot_escape_returns_none(tmp_path: Path) -> None:
    root = _root(tmp_path)
    secret = tmp_path / "secret.md"
    secret.write_text("x")
    sneaky = root / "create-skill" / ".." / ".." / "secret.md"
    assert classify_skill_read(root, sneaky) is None


def test_symlink_escape_inside_skill_returns_none(tmp_path: Path) -> None:
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("x")
    (root / "create-skill" / "evil").symlink_to(outside, target_is_directory=True)
    assert classify_skill_read(root, root / "create-skill" / "evil" / "x.md") is None


def test_invalid_name_returns_none(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bad = root / "Not_Valid"
    bad.mkdir()
    assert classify_skill_read(root, bad / "SKILL.md") is None


def test_name_longer_than_store_limit_returns_none(tmp_path: Path) -> None:
    root = _root(tmp_path)
    long_dir = root / ("a" * 65)
    long_dir.mkdir()
    assert classify_skill_read(root, long_dir / "SKILL.md") is None


def test_public_skill_name_fallback() -> None:
    assert public_skill_name(None) == "skill"
    assert public_skill_name("no valid name") == "skill"
    assert public_skill_name("a" * 65) == "skill"
    assert public_skill_name("create-skill") == "create-skill"
