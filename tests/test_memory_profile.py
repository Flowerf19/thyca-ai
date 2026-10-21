"""Shared USER.md profile block mechanics (TURN-CURATE write side)."""
from __future__ import annotations

from pathlib import Path

from thyca.memory.habits import AUTO_END as LEGACY_END
from thyca.memory.habits import AUTO_START as LEGACY_START
from thyca.memory.profile import (
    PROFILE_AUTO_END,
    PROFILE_AUTO_START,
    PROFILE_COMMENT,
    legacy_profile_lines,
    maybe_strip_legacy_block,
    parse_profile_lines,
    profile_block_lines,
    strip_legacy_habits_block,
    update_profile_block,
)


def _user_md(extra: str = "") -> str:
    return "# User\n\n## Name and forms of address\n- Hòa\n" + extra


def test_update_creates_section_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    path.write_text(_user_md(), encoding="utf-8")
    added, removed = update_profile_block(
        path, ["uong ca phe den", "Discord: tra loi ngan gon"]
    )
    assert (added, removed) == (2, 0)
    text = path.read_text(encoding="utf-8")
    assert "## Profile" in text
    assert PROFILE_AUTO_START in text
    assert profile_block_lines(text) == [
        "uong ca phe den", "Discord: tra loi ngan gon",
    ]
    # Manual sections stay untouched.
    assert "## Name and forms of address" in text
    assert "- Hòa" in text


def test_update_replaces_lines_and_counts_diff(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    path.write_text(_user_md(), encoding="utf-8")
    update_profile_block(path, ["a", "b", "c"])
    added, removed = update_profile_block(path, ["a", "c", "d"])
    assert (added, removed) == (1, 1)
    assert profile_block_lines(path.read_text(encoding="utf-8")) == ["a", "c", "d"]


def test_update_cleans_invalid_lines(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    path.write_text("# User\n", encoding="utf-8")
    update_profile_block(
        path, ["ok line", "<!-- nope -->", "", "## Heading", "dup", "dup"]
    )
    assert profile_block_lines(path.read_text(encoding="utf-8")) == ["ok line", "dup"]


def test_parse_reads_legacy_and_new_markers() -> None:
    text = (
        f"## Profile\n{PROFILE_AUTO_START}\n- new one\n{PROFILE_AUTO_END}\n"
        f"## Habits\n{LEGACY_START}\n- old one\n- old two\n{LEGACY_END}\n"
    )
    assert parse_profile_lines(text) == ["new one", "old one", "old two"]
    assert profile_block_lines(text) == ["new one"]
    assert legacy_profile_lines(text) == ["old one", "old two"]


def test_strip_legacy_keeps_manual_bullets() -> None:
    text = (
        "# User\n\n## Habits\n- manual habit\n"
        f"{LEGACY_START}\n- auto old\n{LEGACY_END}\n\n## Name\n- Hòa\n"
    )
    out = strip_legacy_habits_block(text)
    assert "- auto old" not in out
    assert "- manual habit" in out
    assert "## Habits" in out
    assert "## Name" in out


def test_strip_legacy_drops_empty_section() -> None:
    text = (
        f"# User\n\n## Habits\n{LEGACY_START}\n- auto\n{LEGACY_END}\n"
        "\n## Name\n- Hòa\n"
    )
    out = strip_legacy_habits_block(text)
    assert "## Habits" not in out
    assert "auto" not in out
    assert "## Name" in out


def test_strip_legacy_drops_section_with_only_auto_comment() -> None:
    text = (
        f"# User\n\n## Habits\n<!-- Habits Thyca tự tổng hợp (auto). -->\n"
        f"{LEGACY_START}\n- auto\n{LEGACY_END}\n\n## Name\n- Hòa\n"
    )
    out = strip_legacy_habits_block(text)
    assert "## Habits" not in out
    assert "auto" not in out
    assert "## Name" in out


def test_replace_block_keeps_newline_after_marker() -> None:
    from thyca.memory.profile import _replace_block

    text = (
        f"# User\n## Profile\n{PROFILE_COMMENT}\n"
        f"{PROFILE_AUTO_START}\n{PROFILE_AUTO_END}\n"
    )
    out = _replace_block(text, PROFILE_AUTO_START, PROFILE_AUTO_END, ["a", "b"])
    marker = out.index(PROFILE_AUTO_START) + len(PROFILE_AUTO_START)
    assert out[marker] == "\n"
    assert "- a\n" in out and "- b\n" in out


def test_maybe_strip_legacy_only_when_migrated(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    # Legacy line chưa nằm trong profile block → KHÔNG strip.
    path.write_text(
        f"## Profile\n{PROFILE_AUTO_START}\n- new fact\n{PROFILE_AUTO_END}\n"
        f"## Habits\n{LEGACY_START}\n- old fact\n{LEGACY_END}\n",
        encoding="utf-8",
    )
    assert maybe_strip_legacy_block(path) is False
    assert "## Habits" in path.read_text(encoding="utf-8")
    # Đã migrate đủ → strip, line vẫn còn trong profile block.
    path.write_text(
        f"## Profile\n{PROFILE_AUTO_START}\n- new fact\n- old fact\n{PROFILE_AUTO_END}\n"
        f"## Habits\n{LEGACY_START}\n- old fact\n{LEGACY_END}\n",
        encoding="utf-8",
    )
    assert maybe_strip_legacy_block(path) is True
    text = path.read_text(encoding="utf-8")
    assert "## Habits" not in text
    assert "- old fact" in text