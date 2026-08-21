"""Leaf get-counts and inventory — memory-usage-stats GOAL-001/002."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.memory.archived import ArchiveError, SCHEMA_VERSION
from thyca.memory.heading import parse_heading
from thyca.tools.memory import MemoryFacade

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def at(day: str, hour: int = 10) -> datetime:
    year, month, day_n = (int(part) for part in day.split("-"))
    return datetime(year, month, day_n, hour, 0, tzinfo=TZ)


def _closed_three(root: Path) -> None:
    (root / "SOUL.md").write_text("# Soul\nbe concise about tools\n", encoding="utf-8")
    (root / "USER.md").write_text("# User\nTên: Hòa\n", encoding="utf-8")
    (root / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (root / "memory").mkdir()
    (root / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        '## 08:00 — one <!-- thyca {"id":"aaaaaaaa","imp":3,"exp":"2026-09-12T00:00:00Z"} -->\n'
        "- first leaf is long enough here\n"
        '## 09:00 — two <!-- thyca {"id":"bbbbbbbb","imp":3,"exp":"2026-09-12T00:00:00Z"} -->\n'
        "- second leaf is long enough here\n"
        '## 10:00 — three <!-- thyca {"id":"cccccccc","imp":3,"exp":"2026-09-12T00:00:00Z"} -->\n'
        "- third leaf is long enough here\n",
        encoding="utf-8",
    )


def test_inventory_excludes_persona_and_counts_unused(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.archive.reindex(at("2026-08-17"))
    stats = facade.stats(now=at("2026-08-17"))
    assert stats.total == 3
    assert stats.used == 0
    assert stats.unused == 3
    assert {item.session_id for item in stats.leaves} == {
        "2026-08-13#aaaaaaaa",
        "2026-08-13#bbbbbbbb",
        "2026-08-13#cccccccc",
    }
    assert len(stats.suggest_removal) == 3
    assert all(not item.is_today for item in stats.suggest_removal)


def test_get_chunk_increments_and_path_search_do_not(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    cid = "2026-08-13#aaaaaaaa#1"
    facade.get(chunk_id=cid, now=now)
    facade.get(chunk_id=cid, now=now)
    facade.search("first leaf", now=now)
    facade.get(path=str(tmp_path / "MEMORY.md"), now=now)
    stats = facade.stats(now=now)
    by_id = {item.chunk_id: item for item in stats.leaves}
    assert by_id[cid].get_count == 2
    assert stats.used == 1
    assert stats.unused == 2


def test_get_session_increments_each_returned_leaf(tmp_path: Path) -> None:
    (tmp_path / "MEMORY.md").write_text(
        "# Memory\n"
        '## 08:00 — cafe <!-- thyca {"id":"dddddddd","imp":3,"exp":"2026-09-12T00:00:00Z"} -->\n'
        "- first cafe leaf is long enough\n"
        "- second cafe leaf is long enough\n",
        encoding="utf-8",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    facade.get(session_id="memory#dddddddd", now=now)
    stats = facade.stats(now=now)
    counts = {item.chunk_id: item.get_count for item in stats.leaves}
    assert counts == {"memory#dddddddd#1": 1, "memory#dddddddd#2": 1}
    assert stats.used == 2
    assert stats.unused == 0


def test_get_miss_does_not_record(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    try:
        facade.get(chunk_id="nope#ffffffff#1", now=now)
    except ArchiveError:
        pass
    else:
        raise AssertionError("expected miss")
    assert facade.archive.store.leaf_get_map() == {}


def test_today_unused_not_in_suggest(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.remember("lunch", "eat pho today maybe yes", target="daily", now=now)
    stats = facade.stats(now=now)
    today = [item for item in stats.leaves if item.is_today]
    assert today
    assert stats.unused >= 1
    assert all(item.chunk_id not in {row.chunk_id for row in stats.suggest_removal} for item in today)


def test_forget_drops_leaf_gets(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    sid = "2026-08-13#aaaaaaaa"
    facade.get(session_id=sid, now=now)
    assert facade.stats(now=now).used == 1
    facade.forget(sid, now=now)
    stats = facade.stats(now=now)
    assert stats.total == 2
    assert stats.used == 0
    assert sid not in {item.session_id for item in stats.leaves}
    assert all(not key.startswith(sid) for key in facade.archive.store.leaf_get_map())


def test_reindex_keeps_get_counts(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "likes ca phe den enough", target="memory", now=t0)
    facade.get(session_id=sid, now=t0)
    facade.remember("tea", "likes tra da enough text", target="memory", now=t0 + timedelta(hours=1))
    stats = facade.stats(now=t0 + timedelta(hours=1))
    cafe = next(item for item in stats.leaves if item.session_id == sid)
    assert cafe.get_count == 1


def test_get_still_slides_ttl(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "likes ca phe den enough", target="memory", now=t0)
    facade.get(session_id=sid, now=t0 + timedelta(days=5))
    meta = next(
        parsed
        for line in (tmp_path / "MEMORY.md").read_text(encoding="utf-8").splitlines()
        if (parsed := parse_heading(line))
    )
    assert meta.expires_at == "2026-09-05T12:00:00Z"


def test_v3_migrates_additive(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.archive.reindex(at("2026-08-17"))
    before = facade.archive.store.chunk_ids()
    assert before
    db_path = tmp_path / "memory.sqlite"
    facade.archive.store.close()
    db = sqlite3.connect(db_path)
    db.execute("DROP TABLE leaf_gets")
    db.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")
    db.commit()
    db.close()
    again = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    row = again.archive.store._db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    assert row[0] == SCHEMA_VERSION
    tables = {
        item[0]
        for item in again.archive.store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "leaf_gets" in tables
    assert set(again.archive.store.chunk_ids()) == set(before)
