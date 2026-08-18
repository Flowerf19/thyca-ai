"""Shared heading grammar — JSON comment is the write format."""
from __future__ import annotations

import pytest

from thyca.memory.heading import (
    HeadingMeta,
    is_session_heading,
    parse_heading,
    render_heading,
    resolve_entry_id,
    session_id,
    strip_comment,
    strip_heading_comments,
)


def test_parse_json_and_legacy() -> None:
    json_line = (
        '## 08:00 — ăn sáng bún bò <!-- thyca {"id":"a1b2c3d4","imp":3,'
        '"exp":"2026-09-12T01:00:00Z"} -->'
    )
    meta = parse_heading(json_line)
    assert meta is not None
    assert meta.time == "08:00"
    assert meta.title == "ăn sáng bún bò"
    assert meta.entry_id == "a1b2c3d4"
    assert meta.importance == 3
    assert meta.expires_at == "2026-09-12T01:00:00Z"

    legacy = parse_heading(
        "## 08:00 — ăn sáng bún bò <!-- thyca:a1b2c3d4 imp=3 exp=2026-09-12T01:00:00Z -->"
    )
    assert legacy is not None
    assert legacy.entry_id == "a1b2c3d4"
    assert legacy.expires_at == "2026-09-12T01:00:00Z"


def test_render_is_json_and_roundtrip() -> None:
    rendered = render_heading(
        HeadingMeta("08:00", "ăn sáng bún bò", "a1b2c3d4", 3, "2026-09-12T01:00:00Z")
    )
    assert rendered == (
        '## 08:00 — ăn sáng bún bò <!-- thyca {"id":"a1b2c3d4","imp":3,'
        '"exp":"2026-09-12T01:00:00Z"} -->\n'
    )
    back = parse_heading(rendered)
    assert back == HeadingMeta("08:00", "ăn sáng bún bò", "a1b2c3d4", 3, "2026-09-12T01:00:00Z")


def test_heading_without_comment_is_still_a_session() -> None:
    line = "## 09:00 — keep"
    assert is_session_heading(line)
    meta = parse_heading(line)
    assert meta is not None
    assert meta.entry_id is None
    assert meta.title == "keep"
    assert resolve_entry_id(meta, "/tmp/d.md", 1) == resolve_entry_id(meta, "/tmp/d.md", 1)
    assert parse_heading("# 2026-08-13") is None
    assert parse_heading("- not a heading") is None


def test_broken_json_does_not_crash() -> None:
    meta = parse_heading("## 08:00 — x <!-- thyca {not-json -->")
    assert meta is not None
    assert meta.entry_id is None
    assert meta.importance == 3
    assert meta.expires_at is None
    with pytest.raises(ValueError):
        render_heading(meta)


def test_strip_comment_and_hyphen() -> None:
    line = '## 08:00 - topic <!-- thyca {"id":"aaaaaaaa","imp":2} -->'
    assert strip_comment(line) == "## 08:00 — topic"
    text = line + "\n- leaf\n"
    assert strip_heading_comments(text) == "## 08:00 — topic\n- leaf\n"
    assert session_id("2026-08-13", "a1b2c3d4") == "2026-08-13#a1b2c3d4"
