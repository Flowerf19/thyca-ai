"""Phase 1E: habit_explicit field + per-bot habit sweep."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from thyca.memory import habits
from thyca.memory.facade import MemoryFacade
from thyca.memory.heading import parse_heading, render_heading, HeadingMeta
from thyca.memory.identity import bind_bot_id, reset_bot_id

UTC = timezone.utc
TZ = "Asia/Ho_Chi_Minh"


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=UTC)


# -- habit_explicit field -----------------------------------------------------

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
    import pytest

    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    with pytest.raises(ValueError, match="habit_explicit requires"):
        facade.remember("t", "s", now=at(1), habit_explicit=True)
    plain = facade.remember("t", "s", now=at(1))
    with pytest.raises(Exception, match="habit_explicit requires"):
        facade.update(plain, habit_explicit=True, now=at(1))


def test_remember_search_carries_explicit(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    explicit_sid = facade.remember("tea", "uong tra buoi sang", now=at(1),
                    kind="habit", habit="uong tra", habit_explicit=True)
    plain_sid = facade.remember("tea2", "uong tra chieu", now=at(1),
                    kind="habit", habit="uong tra")
    facade._refresh_index(at(5))
    hits = {h.session_id: h for h in facade.search("uong tra", now=at(5)).hits}
    assert hits[explicit_sid].habit_explicit is True
    assert hits[plain_sid].habit_explicit is False


def test_migration_v8_1a_gains_explicit_column(tmp_path: Path) -> None:
    from tests.test_memory_task_habits import _schema_without

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
        assert db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "8"
    finally:
        db.close()


def test_extractor_explicit_roundtrip(tmp_path: Path) -> None:
    import json as _json

    from thyca.memory import jobs

    items = jobs.parse_extraction(_json.dumps([
        {"topic": "t", "summary": "s", "kind": "habit",
         "habit": "uong tra", "habit_explicit": True},
        {"topic": "t2", "summary": "s2", "habit_explicit": True},
    ]))
    assert [(i.habit_explicit, i.kind) for i in items] == [
        (True, "habit"), (False, None)]
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    out = jobs.apply_extracted(facade, items)
    day = out["written"][0].split("#")[0]
    lines = (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8").splitlines()
    metas = [parse_heading(line) for line in lines]
    metas = [m for m in metas if m is not None]
    assert metas[0].habit_explicit is True
    assert metas[1].habit_explicit is False


# -- sweep --------------------------------------------------------------------

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
    # Idempotent rerun.
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
    # Display label = newest observation's raw label, one line only.
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
    # Expired observations don't count (importance 3 = 30d TTL).
    far = datetime(2026, 11, 20, 12, tzinfo=UTC)
    assert habits.sweep_bot_habits(
        facade, "coder", tmp_path / "bots" / "coder" / "USER.md",
        now=far) == []


# -- phase 1F: team aggregation -------------------------------------------------

def _write_bot_user(tmp_path: Path, bot_id: str, labels: list[str]) -> Path:
    from thyca.memory.habits import AUTO_END, AUTO_START

    path = tmp_path / "bots" / bot_id / "USER.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# User — bot tự ghi\n## Habits\n" + AUTO_START + "\n"
        + "".join(f"- {label}\n" for label in labels) + AUTO_END + "\n",
        encoding="utf-8",
    )
    return path


def test_aggregate_two_bots_share_one_line(tmp_path: Path) -> None:
    from thyca.memory import habits as mem_habits

    a = _write_bot_user(tmp_path, "coder", ["Uống Trà Buổi Sáng"])
    b = _write_bot_user(tmp_path, "designer", ["uong tra buoi sang"])
    out = mem_habits.aggregate_team_habits(
        {"coder": a, "designer": b}, tmp_path / "USER.md")
    # Display label from the smallest sorted bot_id ("coder").
    assert out == ["Uống Trà Buổi Sáng"]
    shared = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert shared.count("- Uống Trà Buổi Sáng") == 1
    # Idempotent rerun.
    assert mem_habits.aggregate_team_habits(
        {"coder": a, "designer": b}, tmp_path / "USER.md") == []
    assert (tmp_path / "USER.md").read_text(encoding="utf-8") == shared


def test_aggregate_single_bot_stays_local(tmp_path: Path) -> None:
    from thyca.memory import habits as mem_habits

    a = _write_bot_user(tmp_path, "coder", ["uong tra"])
    out = mem_habits.aggregate_team_habits({"coder": a}, tmp_path / "USER.md")
    assert out == []
    assert not (tmp_path / "USER.md").exists()


def test_aggregate_three_bots_still_one_line(tmp_path: Path) -> None:
    from thyca.memory import habits as mem_habits

    paths = {b: _write_bot_user(tmp_path, b, ["uong tra"]) for b in ("a1", "b2", "c3")}
    out = mem_habits.aggregate_team_habits(paths, tmp_path / "USER.md")
    assert out == ["uong tra"]
    assert (tmp_path / "USER.md").read_text(encoding="utf-8").count("- uong tra") == 1


def test_aggregate_preserves_user_text_and_manual_bullets(tmp_path: Path) -> None:
    from thyca.memory import habits as mem_habits

    shared = tmp_path / "USER.md"
    shared.write_text(
        "# User\n\nThích cà phê đen, không đường.\n\n## Habits\n\n"
        "- Uống trà buổi sáng\n",
        encoding="utf-8",
    )
    a = _write_bot_user(tmp_path, "coder", ["uong tra buoi sang"])
    b = _write_bot_user(tmp_path, "designer", ["uong tra buoi sang"])
    out = mem_habits.aggregate_team_habits(
        {"coder": a, "designer": b}, shared)
    # Manual bullet with the same key blocks the auto line.
    assert out == []
    text = shared.read_text(encoding="utf-8")
    assert "Thích cà phê đen, không đường." in text
    assert text.count("Uống trà buổi sáng") == 1
    # A genuinely new shared habit appends without touching the rest.
    c = _write_bot_user(tmp_path, "coder", ["uong tra buoi sang", "day som"])
    d = _write_bot_user(tmp_path, "designer", ["uong tra buoi sang", "dậy sớm"])
    out = mem_habits.aggregate_team_habits({"coder": c, "designer": d}, shared)
    assert out == ["day som"] or out == ["dậy sớm"]
    text = shared.read_text(encoding="utf-8")
    assert "Thích cà phê đen, không đường." in text


def test_aggregate_missing_block_and_missing_files(tmp_path: Path) -> None:
    from thyca.memory import habits as mem_habits

    # Bot file without any block counts as zero habits.
    plain = tmp_path / "bots" / "coder" / "USER.md"
    plain.parent.mkdir(parents=True)
    plain.write_text("# User — bot tự ghi\nNo habits here.\n", encoding="utf-8")
    missing = tmp_path / "bots" / "ghost" / "USER.md"
    out = mem_habits.aggregate_team_habits(
        {"coder": plain, "ghost": missing}, tmp_path / "USER.md")
    assert out == []
    # Shared file without ## Habits gains the section when needed.
    a = _write_bot_user(tmp_path, "coder", ["uong tra"])
    b = _write_bot_user(tmp_path, "designer", ["uong tra"])
    shared = tmp_path / "USER.md"
    shared.write_text("# User\n\n## Name\nX\n", encoding="utf-8")
    out = mem_habits.aggregate_team_habits({"coder": a, "designer": b}, shared)
    assert out == ["uong tra"]
    text = shared.read_text(encoding="utf-8")
    assert "## Name\nX" in text and "- uong tra" in text


def test_flush_writes_local_only_watch_aggregates_to_shared(tmp_path: Path) -> None:
    import asyncio
    import json as _json

    from thyca.app.chat_app import ChatApp
    from thyca.bots.card import BotCard
    from thyca.config import default_config, load, save
    from thyca.core.protocol import Message
    from thyca.llm.llm_base import ChatReply

    cfg = default_config()
    save(cfg, tmp_path / "config.json")

    class HabitLLM:
        async def chat(self, messages, tools=None):
            return type("R", (), {"content": _json.dumps([{
                "topic": "t", "summary": "s", "kind": "habit",
                "habit": "uong tra buoi sang", "habit_explicit": True,
            }])})()

    app = ChatApp(tmp_path, load(tmp_path / "config.json"), connect=HabitLLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C",
                                 description="d", persona=""))
        app._bots.create(BotCard(bot_id="designer", name="designer", role="D",
                                 description="d", persona=""))
        # Designer already recorded the same habit in its own file.
        _write_bot_user(tmp_path, "designer", ["uong tra buoi sang"])
        asyncio.run(app._flush_bot_memory(
            "coder",
            [Message(role="user", content="tôi luôn uống trà buổi sáng",
                     ts="2026-10-16T10:00:00Z")],
            app._current_cfg(),
        ))
        # Flush only wrote the coder-local file.
        local = (tmp_path / "bots" / "coder" / "USER.md").read_text(encoding="utf-8")
        assert "- uong tra buoi sang" in local
        assert "uong tra buoi sang" not in (tmp_path / "USER.md").read_text(
            encoding="utf-8")
        # The watch aggregates to shared on first scan.
        assert app.aggregate_changed_bot_habits() == ["uong tra buoi sang"]
        shared = (tmp_path / "USER.md").read_text(encoding="utf-8")
        assert "- uong tra buoi sang" in shared
        # Unchanged rescan does nothing.
        assert app.aggregate_changed_bot_habits() == []
    finally:
        app.shutdown()


def test_watch_retries_after_failure(tmp_path: Path, monkeypatch) -> None:
    from thyca.app.chat_app import ChatApp
    from thyca.bots.card import BotCard
    from thyca.config import default_config, load, save
    from thyca.memory import habits as mem_habits

    cfg = default_config()
    save(cfg, tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"), connect=None)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C",
                                 description="d", persona=""))
        app._bots.create(BotCard(bot_id="designer", name="designer", role="D",
                                 description="d", persona=""))
        _write_bot_user(tmp_path, "coder", ["uong tra"])
        _write_bot_user(tmp_path, "designer", ["uong tra"])
        calls = []
        real = mem_habits.aggregate_team_habits

        def flaky(paths, shared, facade=None):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("disk hiccup")
            return real(paths, shared, facade=facade)

        monkeypatch.setattr(mem_habits, "aggregate_team_habits", flaky)
        assert app.aggregate_changed_bot_habits() == []
        assert len(calls) == 1
        # Signatures were kept: the next scan retries and succeeds.
        assert app.aggregate_changed_bot_habits() == ["uong tra"]
        assert len(calls) == 2
    finally:
        app.shutdown()



# -- review rework: explicit ownership ------------------------------------------

def test_sweep_ignores_team_visible_entries(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    # Legacy (owners None): visible to all, owned by none.
    facade.remember("shared habit", "everyone knows", now=at(1),
                    kind="habit", habit="uong tra", habit_explicit=True)
    # Explicit team entry.
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
    # …and nothing reaches the shared file either.
    from thyca.memory import habits as mem_habits

    before = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert mem_habits.aggregate_team_habits({}, tmp_path / "USER.md") == []
    assert (tmp_path / "USER.md").read_text(encoding="utf-8") == before


def test_sweep_counts_coowned_entry_for_each_owner(tmp_path: Path) -> None:
    # One entry owned by two bots counts for both sweeps (documented:
    # co-ownership is not two independent observations — the aggregate
    # ≥2-bot rule counts bots holding the habit, per the user decision).
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


# -- review rework: label sanitization ------------------------------------------

def test_remember_rejects_multiline_and_markup_labels(tmp_path) -> None:
    import pytest as _pytest

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
    # Inner whitespace collapses to one space on write.
    sid = facade.remember("t", "s", now=at(1), kind="habit",
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
    import json as _json

    from thyca.memory import jobs

    items = jobs.parse_extraction(_json.dumps([
        {"topic": "t", "summary": "s", "kind": "habit",
         "habit": "a\nb", "habit_explicit": True},
    ]))
    assert [(i.topic, i.kind, i.habit, i.habit_explicit) for i in items] == [
        ("t", None, None, False)]


def test_watch_picks_up_modified_bot_file(tmp_path: Path) -> None:
    from thyca.app.chat_app import ChatApp
    from thyca.bots.card import BotCard
    from thyca.config import default_config, load, save

    cfg = default_config()
    save(cfg, tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"), connect=None)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C",
                                 description="d", persona=""))
        app._bots.create(BotCard(bot_id="designer", name="designer", role="D",
                                 description="d", persona=""))
        _write_bot_user(tmp_path, "coder", ["uong tra"])
        _write_bot_user(tmp_path, "designer", ["uong tra"])
        assert app.aggregate_changed_bot_habits() == ["uong tra"]
        assert app.aggregate_changed_bot_habits() == []
        # A new habit shared by both files promotes on the next scan.
        _write_bot_user(tmp_path, "coder", ["uong tra"])
        coder_path = tmp_path / "bots" / "coder" / "USER.md"
        with coder_path.open("a", encoding="utf-8") as stream:
            stream.write("")
        text = coder_path.read_text(encoding="utf-8")
        text = text.replace("<!-- habits:auto:end -->",
                            "- day som\n<!-- habits:auto:end -->")
        coder_path.write_text(text, encoding="utf-8")
        designer_path = tmp_path / "bots" / "designer" / "USER.md"
        text = designer_path.read_text(encoding="utf-8")
        text = text.replace("<!-- habits:auto:end -->",
                            "- day som\n<!-- habits:auto:end -->")
        designer_path.write_text(text, encoding="utf-8")
        assert app.aggregate_changed_bot_habits() == ["day som"]
    finally:
        app.shutdown()
