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
    assert stats.suggest_removal == []
    assert stats.searched == 0
    assert stats.untouched == 3
    assert [item.name for item in stats.files] == ["SOUL.md", "USER.md", "IDENTITY.md"]
    assert "be concise" in stats.files[0].content
    assert "Hòa" in stats.files[1].content


def test_get_increments_search_does_not_increment_get(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    cid = "2026-08-13#aaaaaaaa#1"
    facade.get(chunk_id=cid, now=now)
    facade.get(chunk_id=cid, now=now)
    facade.search("first", now=now)
    facade.get(path=str(tmp_path / "memory" / "2026-08-13.md"), now=now)
    stats = facade.stats(now=now)
    by_id = {item.chunk_id: item for item in stats.leaves}
    assert by_id[cid].get_count == 2
    assert by_id[cid].search_count >= 1
    assert stats.used == 1
    assert stats.unused == 2
    assert stats.searched == 1


def test_get_session_increments_each_returned_leaf(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        '## 08:00 — cafe <!-- thyca {"id":"dddddddd","imp":3,"exp":"2026-09-12T00:00:00Z"} -->\n'
        "- first cafe leaf is long enough\n"
        "- second cafe leaf is long enough\n",
        encoding="utf-8",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    facade.get(session_id="2026-08-13#dddddddd", now=now)
    stats = facade.stats(now=now)
    counts = {item.chunk_id: item.get_count for item in stats.leaves}
    assert counts == {"2026-08-13#dddddddd#1": 1, "2026-08-13#dddddddd#2": 1}
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
    assert facade.archive.store.usage.get_map() == {}


def test_today_unused_not_in_suggest(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.remember("lunch", "eat pho today maybe yes", now=now)
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
    assert all(not key.startswith(sid) for key in facade.archive.store.usage.get_map())


def test_reindex_keeps_get_counts(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "likes ca phe den enough", now=t0)
    facade.get(session_id=sid, now=t0)
    facade.remember("tea", "likes tra da enough text", now=t0 + timedelta(hours=1))
    stats = facade.stats(now=t0 + timedelta(hours=1))
    cafe = next(item for item in stats.leaves if item.session_id == sid)
    assert cafe.get_count == 1


def test_get_still_slides_ttl(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "likes ca phe den enough", now=t0)
    facade.get(session_id=sid, now=t0 + timedelta(days=5))
    meta = next(
        parsed
        for line in (tmp_path / "memory" / "2026-08-01.md").read_text(encoding="utf-8").splitlines()
        if (parsed := parse_heading(line))
    )
    assert meta.expires_at == "2026-09-05T12:00:00Z"


def test_stats_sees_remember_from_other_facade(tmp_path: Path) -> None:
    reader = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    reader.archive.reindex(at("2026-08-17"))
    assert reader.stats(now=at("2026-08-17")).total == 0
    writer = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    writer.remember("cafe", "likes ca phe den enough", now=at("2026-08-17"))
    stats = reader.stats(now=at("2026-08-17"))
    assert stats.total == 1
    assert stats.leaves[0].session_id.startswith("2026-08-17#")


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
    assert "leaf_searches" in tables
    assert set(again.archive.store.chunk_ids()) == set(before)


def test_search_increments_returned_hits_only(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    first = facade.search("first", now=now)
    assert first.hits
    cid = first.hits[0].chunk_id
    by_id = {item.chunk_id: item for item in facade.stats(now=now).leaves}
    assert by_id[cid].search_count == 1
    facade.search("first", now=now)
    by_id = {item.chunk_id: item for item in facade.stats(now=now).leaves}
    assert by_id[cid].search_count == 2
    assert by_id[cid].get_count == 0
    stats = facade.stats(now=now)
    assert stats.used == 0
    assert stats.searched == 1
    assert stats.untouched == 2
    assert stats.suggest_removal == []


def test_empty_and_invalid_search_do_not_record(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    facade.search("", now=now)
    facade.search("x", timeline_day="nope", now=now)
    facade.search("zzzz-no-such-token", now=now)
    facade.recent(now=now)
    facade.get(path=str(tmp_path / "memory" / "2026-08-13.md"), now=now)
    assert facade.archive.store.usage.search_map() == {}


def test_forget_drops_leaf_searches(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    sid = "2026-08-13#aaaaaaaa"
    facade.search("first", now=now)
    assert facade.archive.store.usage.search_map()
    facade.forget(sid, now=now)
    assert all(not key.startswith(sid) for key in facade.archive.store.usage.search_map())
    assert sid not in {item.session_id for item in facade.stats(now=now).leaves}


def test_v4_migrates_additive(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.archive.reindex(at("2026-08-17"))
    cid = "2026-08-13#aaaaaaaa#1"
    facade.get(chunk_id=cid, now=at("2026-08-17"))
    before = facade.archive.store.chunk_ids()
    gets = facade.archive.store.usage.get_map()
    db_path = tmp_path / "memory.sqlite"
    facade.archive.store.close()
    db = sqlite3.connect(db_path)
    db.execute("DROP TABLE leaf_searches")
    db.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
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
    assert "leaf_searches" in tables
    assert set(again.archive.store.chunk_ids()) == set(before)
    assert again.archive.store.usage.get_map() == gets


def test_one_search_increments_each_returned_hit(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        '## 08:00 — one <!-- thyca {"id":"aaaaaaaa","imp":3,"exp":"2026-09-12T00:00:00Z"} -->\n'
        "- sharedtokxyz first cafe leaf is long enough\n"
        '## 09:00 — two <!-- thyca {"id":"bbbbbbbb","imp":3,"exp":"2026-09-12T00:00:00Z"} -->\n'
        "- sharedtokxyz second cafe leaf is long enough\n",
        encoding="utf-8",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    facade.archive.reindex(now)
    found = facade.search("sharedtokxyz", now=now)
    assert {hit.chunk_id for hit in found.hits} == {
        "2026-08-13#aaaaaaaa#1",
        "2026-08-13#bbbbbbbb#1",
    }
    stats = facade.stats(now=now)
    counts = {item.chunk_id: item.search_count for item in stats.leaves}
    assert counts == {"2026-08-13#aaaaaaaa#1": 1, "2026-08-13#bbbbbbbb#1": 1}
    assert stats.searched == 2


def test_expiring_within_14_days_not_beyond(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        '## 08:00 — soon <!-- thyca {"id":"aaaaaaaa","imp":3,"exp":"2026-08-24T10:00:00Z"} -->\n'
        "- expires in a week token soonleaf\n"
        '## 09:00 — later <!-- thyca {"id":"bbbbbbbb","imp":3,"exp":"2026-09-20T10:00:00Z"} -->\n'
        "- expires in a month token laterleaf\n",
        encoding="utf-8",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    facade.archive.reindex(now)
    stats = facade.stats(now=now)
    ids = {item.chunk_id for item in stats.expiring}
    assert "2026-08-13#aaaaaaaa#1" in ids
    assert "2026-08-13#bbbbbbbb#1" not in ids


def test_suggest_after_seven_idle_days(tmp_path: Path) -> None:
    _closed_three(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.archive.reindex(at("2026-08-17"))
    young = facade.stats(now=at("2026-08-17"))
    assert young.suggest_removal == []
    aged = facade.stats(now=at("2026-08-20"))
    assert len(aged.suggest_removal) == 3
    assert all(not item.is_today for item in aged.suggest_removal)
