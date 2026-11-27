"""Phase 1E: habit_explicit field + per-bot habit sweep."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest as _pytest

from tests.test_memory_task_habits import _schema_without
from thyca.core.protocol import ToolCall
from thyca.memories import habits, jobs
from thyca.memories.archive_store import ArchiveError
from thyca.memories.archived import SCHEMA_VERSION
from thyca.memories.facade import MemoryFacade
from thyca.memories.heading import HeadingMeta, parse_heading, render_heading
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.tools.memory_tools import sidecar_memory_tools

TZ = "Asia/Ho_Chi_Minh"


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=UTC)


def test_heading_explicit_roundtrip_only_when_true() -> None:
    line = render_heading(HeadingMeta(
        time="08:00", title="t", entry_id="a1b2c3d4",
        kind="habit", habit="uong tra", habit_explicit=True))
    assert '"habit_explicit":true' in line
    assert parse_heading(line).habit_explicit is True
    plain = render_heading(HeadingMeta(
        time="08:00", title="t", entry_id="a1b2c3d4",
        kind="habit", habit="uong tra"))
    assert "habit_explicit" not in plain
    assert parse_heading(plain).habit_explicit is False


def test_heading_explicit_requires_habit_kind(tmp_path) -> None:

    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    with pytest.raises(ValueError, match="habit_explicit requires"):
        facade.remember("t", "s", now=at(1), habit_explicit=True)
    plain = facade.remember("t", "s", now=at(1))

    with pytest.raises(ArchiveError, match="habit_explicit requires"):
        facade.update(plain, habit_explicit=True, now=at(1))


def test_remember_search_carries_explicit(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    explicit_sid = facade.remember("tea", "uong tra buoi sang", now=at(1),
                    kind="habit", habit="uong tra", habit_explicit=True)
    plain_sid = facade.remember("tea2", "uong tra chieu", now=at(1),
                    kind="habit", habit="uong tra")
    facade._refresh_index(at(5))
    hits = {h.memory_id: h for h in facade.search("uong tra", now=at(5)).hits}
    assert hits[explicit_sid].habit_explicit is True
    assert hits[plain_sid].habit_explicit is False


def test_migration_v8_1a_gains_explicit_column(tmp_path: Path) -> None:

    db_path = tmp_path / "memory.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(_schema_without("habit_explicit"))
    db.execute("INSERT INTO meta VALUES ('schema_version', '8')")
    db.commit()
    db.close()
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "2026-09-01.md").write_text(
        "# 2026-09-01\n"
        "## 08:00 — t <!-- thyca {\"id\":\"deadbeef\",\"imp\":3,\"kind\":\"habit\",\"habit\":\"x\"} -->\n"
        "- x body\n",
        encoding="utf-8",
    )
    MemoryFacade(tmp_path, timezone_name=TZ)
    db = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        assert "habit_explicit" in cols
        assert "memory_id" in cols and "session_id" not in cols
        assert db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == SCHEMA_VERSION
    finally:
        db.close()


def test_extractor_explicit_roundtrip(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    registry = sidecar_memory_tools(facade)
    out = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="h1",
                    name="memory_note",
                    arguments={
                        "topic": "t",
                        "summary": "s",
                        "habit": "uong tra",
                        "explicit": True,
                    },
                ),
                ToolCall(
                    id="h2",
                    name="memory_note",
                    arguments={"topic": "t2", "summary": "s2", "explicit": True},
                ),
            ],
        )
    )
    assert len(out["written"]) == 1
    day = out["written"][0].split("#")[0]
    lines = (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8").splitlines()
    metas = [m for line in lines if (m := parse_heading(line)) is not None]
    assert len(metas) == 1
    assert metas[0].kind == "habit"
    assert metas[0].habit_explicit is True


def _remember(facade: MemoryFacade, bot: str, summary: str, day: int,
              habit: str = "uong tra buoi sang", explicit: bool = False) -> str:
    token = bind_bot_id(bot)
    try:
        return facade.remember("obs", summary, now=at(day),
                               kind="habit", habit=habit,
                               habit_explicit=explicit)
    finally:
        reset_bot_id(token)


def test_sweep_needs_three_distinct_days(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    for hour_summary in ("a", "b", "c"):
        _remember(facade, "coder", f"obs {hour_summary}", 1)
    facade._refresh_index(at(2))
    out = habits.sweep_bot_habits(
        facade, "coder", tmp_path / "bots" / "coder" / "USER.md", now=at(2))
    assert out == []
    assert not (tmp_path / "bots" / "coder" / "USER.md").exists()


def test_sweep_writes_one_line_on_third_day(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    for day in (1, 2, 3):
        _remember(facade, "coder", f"obs day{day}", day)
    facade._refresh_index(at(4))
    target = tmp_path / "bots" / "coder" / "USER.md"
    out = habits.sweep_bot_habits(facade, "coder", target, now=at(4))
    assert out == ["uong tra buoi sang"]
    text = target.read_text(encoding="utf-8")
    assert text.count("- uong tra buoi sang") == 1
    assert "<!-- habits:auto:start -->" in text
    assert habits.sweep_bot_habits(facade, "coder", target, now=at(4)) == []
    assert target.read_text(encoding="utf-8") == text


def test_sweep_explicit_writes_immediately(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    _remember(facade, "coder", "user said so", 1, explicit=True)
    out = habits.sweep_bot_habits(
        facade, "coder", tmp_path / "bots" / "coder" / "USER.md", now=at(1))
    assert out == ["uong tra buoi sang"]


def test_sweep_groups_normalized_labels(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    _remember(facade, "coder", "one", 1, habit="Uống Trà Buổi Sáng")
    _remember(facade, "coder", "two", 2, habit="uong tra buoi sang")
    _remember(facade, "coder", "three", 3, habit="UONG TRA BUOI SANG")
    facade._refresh_index(at(4))
    target = tmp_path / "bots" / "coder" / "USER.md"
    out = habits.sweep_bot_habits(facade, "coder", target, now=at(4))
    assert out == ["UONG TRA BUOI SANG"]
    assert target.read_text(encoding="utf-8").count("- UONG TRA BUOI SANG") == 1


def test_sweep_preserves_outside_block(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    target = tmp_path / "bots" / "coder" / "USER.md"
    target.parent.mkdir(parents=True)
    target.write_text("# User — bot tự ghi\n\nNotes riêng.\n", encoding="utf-8")
    for day in (1, 2, 3):
        _remember(facade, "coder", f"obs day{day}", day)
    facade._refresh_index(at(4))
    habits.sweep_bot_habits(facade, "coder", target, now=at(4))
    text = target.read_text(encoding="utf-8")
    assert "Notes riêng." in text
    assert "- uong tra buoi sang" in text


def test_sweep_reader_filter_and_expiry(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    for day in (1, 2, 3):
        _remember(facade, "coder", f"obs day{day}", day)
    facade._refresh_index(at(4))
    token = bind_bot_id("designer")
    try:
        assert habits.habit_observations(facade, "designer", now=at(4)) == []
        target = tmp_path / "bots" / "designer" / "USER.md"
        assert habits.sweep_bot_habits(facade, "designer", target, now=at(4)) == []
    finally:
        reset_bot_id(token)
    far = datetime(2026, 11, 20, 12, tzinfo=UTC)
    assert habits.sweep_bot_habits(
        facade, "coder", tmp_path / "bots" / "coder" / "USER.md",
        now=far) == []


def test_sweep_ignores_team_visible_entries(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    facade.remember("shared habit", "everyone knows", now=at(1),
                    kind="habit", habit="uong tra", habit_explicit=True)
    facade.remember("team habit", "team knows", now=at(1),
                    kind="habit", habit="uong tra", habit_explicit=True,
                    bots=["team"])
    facade._refresh_index(at(2))
    assert habits.sweep_bot_habits(
        facade, "coder", tmp_path / "bots" / "coder" / "USER.md",
        now=at(2)) == []
    assert habits.sweep_bot_habits(
        facade, "designer", tmp_path / "bots" / "designer" / "USER.md",
        now=at(2)) == []
    assert not (tmp_path / "bots" / "coder" / "USER.md").exists()
    assert facade.tasks(now=at(2))["tasks"] == []


def test_sweep_counts_coowned_entry_for_each_owner(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    token = bind_bot_id("coder")
    try:
        facade.remember("shared note", "both know", now=at(1),
                        kind="habit", habit="uong tra", habit_explicit=True,
                        bots=["coder", "designer"])
    finally:
        reset_bot_id(token)
    facade._refresh_index(at(2))
    assert habits.sweep_bot_habits(
        facade, "coder", tmp_path / "bots" / "coder" / "USER.md",
        now=at(2)) == ["uong tra"]
    assert habits.sweep_bot_habits(
        facade, "designer", tmp_path / "bots" / "designer" / "USER.md",
        now=at(2)) == ["uong tra"]


def test_remember_rejects_multiline_and_markup_labels(tmp_path) -> None:

    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    with _pytest.raises(ValueError, match="single line"):
        facade.remember("t", "s", now=at(1), kind="habit",
                        habit="uong tra\nbuoi sang")
    with _pytest.raises(ValueError, match="single line"):
        facade.remember("t", "s", now=at(1), kind="habit",
                        habit="uong tra\rbuoi sang")
    with _pytest.raises(ValueError, match="markup"):
        facade.remember("t", "s", now=at(1), kind="habit",
                        habit="x <!-- habits:auto:end -->")
    facade.remember("t", "s", now=at(1), kind="habit",
                          habit="uong   tra\tbuoi sang")
    assert "uong tra buoi sang" in (
        tmp_path / "memory" / "2026-09-01.md").read_text(encoding="utf-8")


def test_sweep_skips_handwritten_marker_label(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "2026-09-01.md").write_text(
        "# 2026-09-01\n"
        "## 08:00 — t <!-- thyca {\"id\":\"deadbeef\",\"imp\":5,"
        "\"kind\":\"habit\",\"habit\":\"x\"} -->\n"
        "- body\n",
        encoding="utf-8",
    )
    facade._refresh_index(at(2))
    target = tmp_path / "bots" / "coder" / "USER.md"
    out = habits.sweep_bot_habits(facade, "coder", target, now=at(2))
    assert out == []
    assert not target.exists()


def test_extractor_drops_bad_habit_side_fields(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    registry = sidecar_memory_tools(facade)
    out = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="bad",
                    name="memory_note",
                    arguments={
                        "topic": "t",
                        "summary": "s",
                        "habit": "a\nb",
                        "explicit": True,
                    },
                )
            ],
        )
    )
    assert out["written"] == []


def test_cross_day_habit_reinforce_count_and_promote(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    target = tmp_path / "bots" / "coder" / "USER.md"
    day1 = datetime(2026, 9, 1, 12, tzinfo=UTC)
    day15 = datetime(2026, 9, 15, 12, tzinfo=UTC)
    day15_evening = datetime(2026, 9, 15, 15, tzinfo=UTC)
    day35 = datetime(2026, 10, 5, 12, tzinfo=UTC)

    token = bind_bot_id("coder")
    try:
        sid1 = facade.record_habit_entry(
            "Trà sáng", "Uống trà đen buổi sáng",
            habit="uong tra buoi sang", now=day1,
        )
        sid2 = facade.record_habit_entry(
            "Trà sáng", "Lặp lại sau 2 tuần",
            habit="Uống  trà buổi sáng", now=day15,
        )
        sid2_same_day = facade.record_habit_entry(
            "Trà sáng", "Nhắc lại cùng ngày",
            habit="uong tra buoi sang", now=day15_evening,
        )
        assert sid1 == sid2 == sid2_same_day
        obs_2 = habits.habit_observations(facade, "coder", now=day15_evening)
        assert len(obs_2) == 1
        assert obs_2[0]["count"] == 2
        assert obs_2[0]["days"] == ["2026-09-01", "2026-09-15"]
        assert habits.sweep_bot_habits(facade, "coder", target, now=day15_evening) == []

        sid3 = facade.record_habit_entry(
            "Trà sáng", "Lặp lại lần 3 ở tháng sau",
            habit="uong tra buoi sang", now=day35,
        )
        assert sid3 == sid1
        assert not (tmp_path / "memory" / "2026-09-15.md").exists()
        assert not (tmp_path / "memory" / "2026-10-05.md").exists()
        day1_text = (tmp_path / "memory" / "2026-09-01.md").read_text(encoding="utf-8")
        assert day1_text.count("## ") == 1

        obs_3 = habits.habit_observations(facade, "coder", now=day35)
        assert len(obs_3) == 1
        assert obs_3[0]["count"] == 3
        assert obs_3[0]["days"] == ["2026-09-01", "2026-09-15", "2026-10-05"]
        promoted = habits.sweep_bot_habits(facade, "coder", target, now=day35)
        assert promoted == ["uong tra buoi sang"]
        assert "- uong tra buoi sang" in target.read_text(encoding="utf-8")
        assert "uong tra buoi sang" in habits.known_habit_labels(facade, "coder", target, now=day35)
    finally:
        reset_bot_id(token)


def test_record_habit_entry_isolates_cross_window_summary(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    day1 = datetime(2026, 9, 1, 12, tzinfo=UTC)
    day2 = datetime(2026, 9, 2, 12, tzinfo=UTC)
    token = bind_bot_id("coder")
    try:
        sid1 = facade.record_habit_entry(
            "Trà", "Uống trà trong group", habit="uong tra",
            chat="group-1", now=day1,
        )
        sid2 = facade.record_habit_entry(
            "Trà", "PRIVATE_DM_HABIT_TEXT", habit="uong tra",
            chat="coder", now=day2,
        )
    finally:
        reset_bot_id(token)
    assert sid1 == sid2
    day1_text = (tmp_path / "memory" / "2026-09-01.md").read_text(encoding="utf-8")
    assert "PRIVATE_DM_HABIT_TEXT" not in day1_text
    assert "  - 2026-09-02: uong tra" in day1_text


def test_memory_note_explicit_habit_sweeps_bot_user_md_immediately(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    bot_dir = tmp_path / "bots" / "march7th"
    bot_dir.mkdir(parents=True, exist_ok=True)
    registry = sidecar_memory_tools(facade)
    note_spec = registry.get("memory_note")
    assert note_spec is not None
    tok = bind_bot_id("march7th")
    try:
        asyncio.run(
            note_spec.handler({
                "topic": "URL format",
                "summary": "Luôn bọc URL trong dấu ngoặc nhọn <...>",
                "habit": "luon boc URL trong dau ngoac nhon",
                "explicit": True,
            })
        )
    finally:
        reset_bot_id(tok)
    bot_user = bot_dir / "USER.md"
    assert bot_user.is_file()
    assert "luon boc URL trong dau ngoac nhon" in bot_user.read_text(encoding="utf-8")
