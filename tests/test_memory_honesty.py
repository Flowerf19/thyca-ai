"""T6 (memory honesty) + T8 (chunk fallback) — facade/archived/archive_store."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from thyca.memory.archive_store import ArchiveError
from thyca.memory.facade import MemoryFacade

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def at(day: str) -> datetime:
    y, m, d = (int(p) for p in day.split("-"))
    return datetime(y, m, d, 10, 0, tzinfo=TZ)


def _seed(tmp_path: Path, body: str, day: str = "2026-08-13") -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / f"{day}.md").write_text(f"# {day}\n{body}", encoding="utf-8")


def _heading(entry: str, title: str, exp: str) -> str:
    return (
        f"## 08:00 — {title} "
        f"<!-- thyca {{\"id\":\"{entry}\",\"imp\":3,\"exp\":\"{exp}\"}} -->\n"
    )


# --- T6a: expired/forgotten vs never-existed ---


def test_t6a_expired_errors_name_the_cause(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        _heading("eeeeeeee", "gone", "2020-01-01T00:00:00Z")
        + "- this leaf expired long ago indeed\n"
        + _heading("ffffffff", "live", "2027-09-12T00:00:00Z")
        + "- this leaf is still alive here\n",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid, cid = "2026-08-13#eeeeeeee", "2026-08-13#eeeeeeee#1"
    with pytest.raises(ArchiveError, match="^session expired: "):
        facade.archive.get(session_id=sid)
    with pytest.raises(ArchiveError, match="^chunk expired: "):
        facade.archive.get(chunk_id=cid)
    # Facade must not mask the cause behind the writer fallback.
    with pytest.raises(ArchiveError, match="^session expired: "):
        facade.get(session_id=sid)
    with pytest.raises(ArchiveError, match="^chunk expired: "):
        facade.get(chunk_id=cid)
    # Live rows and never-existed ids keep their behavior.
    assert "still alive" in facade.get(session_id="2026-08-13#ffffffff")
    with pytest.raises(ArchiveError, match="^session not found: "):
        facade.archive.get(session_id="2026-08-13#zzzzzzzz")
    with pytest.raises(ArchiveError, match="^chunk not found: "):
        facade.archive.get(chunk_id="2026-08-13#zzzzzzzz#1")


def test_t6a_forgotten_errors_name_the_cause(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        _heading("dddddddd", "doomed", "2027-09-12T00:00:00Z")
        + "- this leaf will be forgotten soon\n",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid, cid = "2026-08-13#dddddddd", "2026-08-13#dddddddd#1"
    facade.archive.store._db.execute(
        "UPDATE chunks SET forgotten_at = ? WHERE chunk_id = ?",
        ("2026-08-14T00:00:00Z", cid),
    )
    facade.archive.store._db.commit()
    with pytest.raises(ArchiveError, match="^session forgotten: "):
        facade.archive.get(session_id=sid)
    with pytest.raises(ArchiveError, match="^chunk forgotten: "):
        facade.archive.get(chunk_id=cid)
    with pytest.raises(ArchiveError, match="^session forgotten: "):
        facade.get(session_id=sid)
    with pytest.raises(ArchiveError, match="^chunk forgotten: "):
        facade.get(chunk_id=cid)


def test_t6a_lookup_session_id_is_precise(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        _heading("eeeeeeee", "gone", "2020-01-01T00:00:00Z")
        + "- this leaf expired long ago indeed\n",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    with pytest.raises(ArchiveError, match="^chunk expired: "):
        facade.archive.lookup_session_id("2026-08-13#eeeeeeee#1")
    with pytest.raises(ArchiveError, match="^chunk not found: "):
        facade.archive.lookup_session_id("2026-08-13#zzzzzzzz#1")


# --- T6b: search warnings ---


def test_t6b_limit_clamp_warns(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    assert facade.search("x", limit=0).warnings == ["limit clamped from 0 to 1"]
    assert facade.search("x", limit=99).warnings == ["limit clamped from 99 to 10"]
    assert facade.search("x", limit=5).warnings == []
    # Early validation returns keep their single warning.
    assert facade.search("", limit=0).warnings == ["empty query"]


def test_t6b_dedup_warns_and_baseline_is_quiet(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        _heading("aaaaaaaa", "two leaves", "2027-09-12T00:00:00Z")
        + "- first deduphonest leaf is here\n"
        + "- second deduphonest leaf is here\n"
        + _heading("bbbbbbbb", "one leaf", "2027-09-12T00:00:00Z")
        + "- a lonely baseline leaf token\n",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.archive.reindex(at("2026-08-17"))
    found = facade.search("deduphonest", now=at("2026-08-17"))
    assert len(found.hits) == 1
    assert found.warnings == ["dedup hid 1 sibling hit"]
    quiet = facade.search("lonely baseline", now=at("2026-08-17"))
    assert len(quiet.hits) == 1
    assert quiet.warnings == []


def test_t6b_candidate_cap_warns(tmp_path: Path) -> None:
    body = "".join(
        _heading(f"{i:08x}", f"note {i}", "2027-09-12T00:00:00Z")
        + f"- capstresszz leaf number {i} here\n"
        for i in range(55)
    )
    _seed(tmp_path, body)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.archive.reindex(at("2026-08-17"))
    found = facade.search("capstresszz", now=at("2026-08-17"))
    assert len(found.hits) == 5
    assert found.warnings == [
        "candidate cap reached (50): some matches may be hidden"
    ]


# --- T6c: timeline_day=today warns ---


def test_t6c_timeline_day_today_warns(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    assert facade.archive.day(now) == "2026-08-17"
    found = facade.search("anything", timeline_day="2026-08-17", now=now)
    assert found.hits == []
    assert found.warnings == [
        "timeline_day 2026-08-17 is not indexed "
        "(today and future files are excluded)"
    ]
    past = facade.search("anything", timeline_day="2026-08-13", now=now)
    assert past.warnings == []


# --- T8: chunk_id writer fallback ---


def test_t8_today_chunk_falls_back_to_writer(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("lunch", "eat pho today maybe uniquetokenzz")
    cid = f"{sid}#1"
    leaf = facade.get(chunk_id=cid)
    assert "uniquetokenzz" in leaf
    assert "## " not in leaf  # leaf text, not the whole session
    assert cid in facade.archive.store.usage.get_map()
    # Session fallback still works alongside.
    assert "uniquetokenzz" in facade.get(session_id=sid)


def test_t8_chunk_miss_stays_not_found(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("lunch", "eat pho today maybe uniquetokenzz")
    for bad in ("nohash", f"{sid}#9", "canonical#soul#99",
                "2026-08-13#deadbeef#1"):
        with pytest.raises(ArchiveError, match="^chunk not found: "):
            facade.get(chunk_id=bad)
    with pytest.raises(ArchiveError, match="^chunk not found: "):
        facade.archive.get(chunk_id="2026-08-13#deadbeef#1")


def test_t8_chunk_fallback_resolves_legacy_duplicate_occurrence(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "## 08:00 — same title\n"
        "- first uniquetoken-firstzz\n"
        "## 09:00 — same title\n"
        "- second uniquetoken-secondzz\n",
        day="2026-08-17",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = at("2026-08-17")
    by_token = {}
    for chunk in facade._today_chunks(now):
        for token in ("uniquetoken-firstzz", "uniquetoken-secondzz"):
            if token in chunk.text_raw:
                by_token[token] = chunk.chunk_id
    assert set(by_token) == {"uniquetoken-firstzz", "uniquetoken-secondzz"}
    assert by_token["uniquetoken-firstzz"] != by_token["uniquetoken-secondzz"]
    assert "uniquetoken-firstzz" in facade.get(
        chunk_id=by_token["uniquetoken-firstzz"], now=now
    )
    assert "uniquetoken-secondzz" in facade.get(
        chunk_id=by_token["uniquetoken-secondzz"], now=now
    )
