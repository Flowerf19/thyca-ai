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


def test_proj_chat_roundtrip_and_old_heading_stays_empty() -> None:
    rendered = render_heading(
        HeadingMeta(
            "08:00",
            "link",
            "a1b2c3d4",
            3,
            "2026-09-12T01:00:00Z",
            proj="/home/flowerf/Projects/thyca-ai",
            chat="2026-09-16T14-39-01_a1b2",
        )
    )
    assert '"proj":"/home/flowerf/Projects/thyca-ai"' in rendered
    assert '"chat":"2026-09-16T14-39-01_a1b2"' in rendered
    back = parse_heading(rendered)
    assert back is not None
    assert back.proj == "/home/flowerf/Projects/thyca-ai"
    assert back.chat == "2026-09-16T14-39-01_a1b2"
    old = parse_heading(
        '## 08:00 — x <!-- thyca {"id":"a1b2c3d4","imp":3} -->'
    )
    assert old is not None
    assert old.proj is None
    assert old.chat is None


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

def test_x8_read_text_file_policy(tmp_path: Path) -> None:
    from thyca.memory.heading import read_text_file

    assert read_text_file(tmp_path / "missing.md") is None
    link = tmp_path / "link.md"
    real = tmp_path / "real.md"
    real.write_text("hi", encoding="utf-8")
    link.symlink_to(real)
    assert read_text_file(link) is None
    assert read_text_file(real) == "hi"
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe binary")
    with pytest.raises(UnicodeDecodeError):
        read_text_file(bad)


def test_x9_day_shared_by_active_and_archived(tmp_path: Path) -> None:
    from thyca.memory.active import ActiveMemory
    from thyca.memory.archived import ArchivedMemory
    from thyca.memory.heading import day

    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    assert day(datetime(2026, 1, 1, 23, 30), zone) == "2026-01-01"
    assert day(datetime(2026, 1, 1, 17, 0, tzinfo=ZoneInfo("UTC")), zone) == "2026-01-02"
    assert day(None, zone) == datetime.now(zone).date().isoformat()
    active = ActiveMemory(tmp_path)
    archived = ArchivedMemory(tmp_path)
    moment = datetime(2026, 5, 5, 12, 0)
    assert active._day(moment) == archived.day(moment) == "2026-05-05"
