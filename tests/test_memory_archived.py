"""Archived lexical memory — GOAL-002 / TASK-104-107."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.memory import ArchivedMemory, ArchiveError, Chunker
from thyca.memory.archive_store import ArchiveStore
from thyca.memory.chunk import Chunk
from thyca.memory.facade import MemoryFacade
from thyca.memory.usage import guarded, is_lock_error

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def at(day: str) -> datetime:
    y, m, d = (int(p) for p in day.split("-"))
    return datetime(y, m, d, 10, 0, tzinfo=TZ)


def _seed(root: Path) -> None:
    (root / "SOUL.md").write_text("# soul\nbe concise\n", encoding="utf-8")
    (root / "USER.md").write_text("# user\nlives in Hanoi\n", encoding="utf-8")
    (root / "memory").mkdir()
    (root / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        "## 08:00 — ăn sáng bún bò <!-- thyca:a1b2c3d4 -->\n"
        "- Ăn bún bò Huế ở quán X\n"
        "- nói chuyện với Luna\n"
        "- likes cà phê\n"
        "## 19:30 — bàn đồ nướng <!-- thyca:e5f6a7b8 -->\n"
        "- Ăn thịt quay với bạn ở Q1\n",
        encoding="utf-8",
    )


def test_chunk_daily_and_legacy(tmp_path: Path) -> None:
    chunker = Chunker()
    text = (tmp_path / "skip.md")
    daily = (
        "# 2026-08-13\n"
        "## 08:00 — topic <!-- thyca:aaaaaaaa -->\n"
        "- one leaf here\n"
        "- x\n"
        "- second leaf that is long enough\n"
    )
    chunks = chunker.chunk_markdown(text, daily, source_kind="daily", timeline_day="2026-08-13")
    assert [c.session_id for c in chunks] == ["2026-08-13#aaaaaaaa"] * len(chunks)
    assert chunks[0].chunk_id.endswith("#1")
    assert any("second leaf" in c.text_raw for c in chunks)
    legacy = chunker.chunk_markdown(
        tmp_path / "SOUL.md",
        "just a paragraph about tools\n\nand another\n",
        source_kind="canonical",
        timeline_day=None,
    )
    assert all(c.session_id == "canonical#soul" for c in legacy)
    assert len(legacy) == 2


def test_reindex_fts_and_skip_today(tmp_path: Path) -> None:
    _seed(tmp_path)
    (tmp_path / "memory" / "2026-08-17.md").write_text(
        "# 2026-08-17\n## 09:00 — secret <!-- thyca:11111111 -->\n- UNIQUE_TODAY_TOKEN\n",
        encoding="utf-8",
    )
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    archived.reindex(at("2026-08-17"))
    found = facade.search("ca phe")
    assert found.hits
    assert any("cà phê" in hit.snippet or "cà phê" in hit.heading for hit in found.hits) or any(
        "cà phê" in archived.get(chunk_id=hit.chunk_id) for hit in found.hits
    )
    meat = facade.search("thịt quay")
    assert meat.hits
    today = facade.search("UNIQUE_TODAY_TOKEN")
    assert today.hits == []


def test_trigram_typo_and_get(tmp_path: Path) -> None:
    _seed(tmp_path)
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    archived.reindex(at("2026-08-17"))
    hits = facade.search("thit quya")
    assert hits.hits
    session = archived.get(session_id=hits.hits[0].session_id)
    assert "thịt quay" in session or "bún bò" in session or "Luna" in session
    whole = archived.get(session_id=hits.hits[0].session_id)
    assert "cà phê" in whole or "thịt quay" in whole or "Luna" in whole
    for kwargs in ({}, {"chunk_id": "x", "session_id": "y"}):
        try:
            archived.get(**kwargs)
            raise AssertionError(f"should reject {kwargs}")
        except ArchiveError as exc:
            assert "exactly one of chunk_id, session_id" in str(exc)
    try:
        archived.get(session_id="2026-08-13#zzzzzzzz")
        raise AssertionError("should reject")
    except ArchiveError:
        pass


def test_duplicate_minute_and_fence(tmp_path: Path) -> None:
    chunker = Chunker()
    text = (
        "# 2026-08-13\n"
        "## 08:00 — first <!-- thyca:aaaaaaaa -->\n"
        "- leaf a\n"
        "## 08:00 — second <!-- thyca:bbbbbbbb -->\n"
        "```\ncode fence body\n```\n"
    )
    chunks = chunker.chunk_markdown(
        tmp_path / "d.md", text, source_kind="daily", timeline_day="2026-08-13"
    )
    ids = {c.session_id for c in chunks}
    assert ids == {"2026-08-13#aaaaaaaa", "2026-08-13#bbbbbbbb"}
    assert any(c.text_raw.startswith("```") for c in chunks)


def test_delete_source_cascades(tmp_path: Path) -> None:
    _seed(tmp_path)
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    archived.reindex(at("2026-08-17"))
    (tmp_path / "memory" / "2026-08-13.md").unlink()
    archived.reindex(at("2026-08-17"))
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    assert facade.search("thịt quay").hits == []
    assert facade.search("Hanoi").hits


def test_leftover_memory_md_dropped_on_open(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    text = (
        "# Memory\n"
        '## 08:00 — leftover <!-- thyca {"id":"eeeeeeee","imp":3,"exp":"2026-09-12T00:00:00Z"} -->\n'
        "- leftover-memory-md-token xyz\n"
    )
    path.write_text(text, encoding="utf-8")
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    chunks = Chunker().chunk_markdown(path, text, source_kind="canonical", timeline_day=None)
    stat = path.stat()
    archived.store.replace_source(
        str(path), "canonical", None, stat.st_mtime_ns, stat.st_size, chunks
    )
    cid = chunks[0].chunk_id
    archived.store.usage.record_gets([cid], chunks[0].session_id, "2026-08-17T03:00:00Z")
    before_expiry = at("2026-08-17")
    assert archived.fts_hits("leftover-memory-md-token", None, before_expiry, 5)
    assert archived.fts_hits("leftover-memory-md-token", None, at("2026-09-13"), 5) == []
    assert cid in archived.store.usage.get_map()
    planted = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    assert planted.search("leftover-memory-md-token", now=before_expiry).hits == []
    assert cid not in planted.archive.store.usage.get_map()
    assert all(
        not item.session_id.startswith("memory#")
        for item in planted.stats(now=before_expiry).leaves
    )


def test_normalize_maps_d_stroke_to_d() -> None:
    assert Chunker.normalize("Đồ án") == "do an"
    assert Chunker.normalize("đ") == "d"


def test_in_order_span_uses_full_leaf_not_snippet() -> None:
    from thyca.memory.archive_store import Hit
    from thyca.memory.rank import _promote_in_order_span

    chunker = Chunker()
    filler = "lorem " * 80
    deep = Hit(
        path="p",
        source_kind="daily",
        chunk_id="deep",
        timeline_day="2026-08-13",
        session_id="2026-08-13#aaaaaaaa",
        heading="ghi chu lat vat",
        snippet="session id chat project",
        score=1.0,
        match_type="fts",
    )
    bait = Hit(
        path="p",
        source_kind="daily",
        chunk_id="bait",
        timeline_day="2026-08-13",
        session_id="2026-08-13#bbbbbbbb",
        heading="session id chat project",
        snippet="id session id chat id project id",
        score=2.0,
        match_type="fts",
    )
    hays = {
        "deep": f"ghi chu lat vat {filler} lien ket memories cua tung du an va tung session",
        "bait": "session id chat project id session id chat id project id",
    }
    out = _promote_in_order_span(
        "lien ket memories project id chat session id",
        [bait, deep],
        chunker,
        hays,
    )
    assert [hit.chunk_id for hit in out][:1] == ["deep"]


def test_search_promotes_in_order_leaf_and_skips_soul_perfume(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text("thơm tho hương cốm\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text("# user\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    filler = "lorem " * 80
    (tmp_path / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        "## 08:00 — ghi chú lặt vặt <!-- thyca:aaaaaaaa -->\n"
        f"- {filler}\n"
        "- liên kết memories của từng dự án và từng session\n"
        "## 09:00 — session id chat project <!-- thyca:bbbbbbbb -->\n"
        "- id session id chat id project id\n",
        encoding="utf-8",
    )
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    archived.reindex(at("2026-08-17"))
    found = facade.search(
        "liên kết memories project id chat session id", now=at("2026-08-17")
    )
    assert found.hits
    assert found.hits[0].session_id.endswith("aaaaaaaa")
    assert all(not hit.session_id.startswith("canonical#") for hit in found.hits)


def test_norm_version_rebuilds_stale_d_stroke(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text("# soul\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text("# user\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        "## 08:00 — đồ án <!-- thyca:aaaaaaaa -->\n"
        "- token_dstroke đồ ăn đặc biệt\n",
        encoding="utf-8",
    )
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    archived.reindex(at("2026-08-17"))
    row = archived.store._db.execute(
        "SELECT chunk_id, text_norm FROM chunks WHERE text_norm LIKE '%token_dstroke%'"
    ).fetchone()
    assert row is not None
    assert "đ" not in row["text_norm"]
    archived.store._db.execute(
        "UPDATE chunks SET text_norm = ? WHERE chunk_id = ?",
        ("token_dstroke đo an dac biet", row["chunk_id"]),
    )
    archived.store._db.execute(
        "UPDATE meta SET value = '1' WHERE key = 'norm_version'"
    )
    archived.store._db.commit()
    archived.store.close()
    again = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    rebuilt = again.archive.store._db.execute(
        "SELECT text_norm FROM chunks WHERE text_norm LIKE '%token_dstroke%'"
    ).fetchone()
    assert rebuilt is not None
    assert "đ" not in rebuilt["text_norm"]
    assert "do an" in rebuilt["text_norm"]
    hits = again.search("do an", now=at("2026-08-17"))
    assert any("token_dstroke" in hit.snippet for hit in hits.hits)


# --- F21: usage transaction + shared-connection races + lock errors ---


def _chunk(cid: str, path: str, sid: str) -> Chunk:
    return Chunk(
        chunk_id=cid,
        path=path,
        source_kind="daily",
        timeline_day="2026-09-01",
        session_id=sid,
        session_title="t",
        heading_raw="## 08:00",
        leaf_ord=1,
        line_start=1,
        line_end=2,
        text_raw="hello world leaf content here",
        text_norm="hello world leaf content here",
        content_hash="abc",
    )


def _seeded_store(tmp_path: Path) -> ArchiveStore:
    store = ArchiveStore(tmp_path / "m.sqlite")
    store.replace_source(
        "/m/2026-09-01.md", "daily", "2026-09-01", 1, 9,
        [_chunk("c1", "/m/2026-09-01.md", "2026-09-01#aaaaaaaa")],
    )
    store.usage.record_gets(["c1"], "2026-09-01#aaaaaaaa", "2026-09-01T00:00:00Z")
    store.usage.record_searches(["c1"], "2026-09-01#aaaaaaaa", "2026-09-01T00:00:00Z")
    return store


def test_f21_drop_source_is_one_transaction(tmp_path: Path, monkeypatch) -> None:
    store = _seeded_store(tmp_path)

    def boom(chunk_ids: list[str]) -> None:
        raise RuntimeError("crash between chunk delete and usage delete")

    monkeypatch.setattr(store.usage, "_delete_ids", boom)
    try:
        store.drop_source("/m/2026-09-01.md")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the injected crash")

    # Rollback covered everything: source, chunk, and both usage rows survive.
    assert store.source_stat("/m/2026-09-01.md") == (1, 9)
    assert store.chunk_ids() == ["c1"]
    assert store.usage.get_map() == {"c1": (1, "2026-09-01T00:00:00Z")}
    assert store.usage.search_map() == {"c1": (1, "2026-09-01T00:00:00Z")}
    store.close()


def test_f21_concurrent_record_and_read_never_collide(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path)
    errors: list[BaseException] = []
    now = "2026-09-01T00:00:00Z"

    def worker(n: int) -> None:
        try:
            for i in range(25):
                store.usage.record_searches(["c1"], "s", now)
                store.usage.record_gets(["c1"], "s", now)
                store.fts_search("hello", None, 5, now)
                store.trigram_search("hello world", None, 5, now)
                store.get_chunk("c1", now)
                if i % 5 == 0:
                    store.usage.search_map()
        except BaseException as exc:  # noqa: BLE001 — collected, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert store.usage.search_map()["c1"][0] == 1 + 8 * 25
    assert store.usage.get_map()["c1"][0] == 1 + 8 * 25
    store.close()


def test_f21_cross_process_lock_surfaces_as_archive_error(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path)
    store._db.execute("PRAGMA busy_timeout=0")
    holder = sqlite3.connect(tmp_path / "m.sqlite")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        try:
            store.usage.record_searches(["c1"], "s", "2026-09-01T00:00:00Z")
        except ArchiveError as exc:
            assert "locked" in str(exc)
            assert isinstance(exc.__cause__, sqlite3.OperationalError)
        else:
            raise AssertionError("expected ArchiveError")
        try:
            store.replace_source("/m/x.md", "daily", "2026-09-01", 1, 1, [])
        except ArchiveError as exc:
            assert isinstance(exc.__cause__, sqlite3.OperationalError)
        else:
            raise AssertionError("expected ArchiveError")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        store.close()


def test_f21_non_lock_operational_error_propagates_raw() -> None:
    assert is_lock_error(sqlite3.OperationalError("database is locked"))
    assert is_lock_error(sqlite3.OperationalError("database table is locked"))
    assert not is_lock_error(sqlite3.OperationalError("no such table: chunks"))

    class Probe:
        import threading as _t

        _lock = _t.RLock()
        _error_cls = ArchiveError

        @guarded
        def bug(self) -> None:
            raise sqlite3.OperationalError("no such table: chunks")

        @guarded
        def contention(self) -> None:
            raise sqlite3.OperationalError("database is locked")

    try:
        Probe().bug()
    except sqlite3.OperationalError as exc:
        assert "no such table" in str(exc)
    else:
        raise AssertionError("expected raw OperationalError")
    try:
        Probe().contention()
    except ArchiveError:
        pass
    else:
        raise AssertionError("expected ArchiveError")


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
def test_m5_split_parts_carry_own_spans_and_long_lines_split() -> None:
    from thyca.memory.chunk import MAX_LEAF_CHARS, Chunker

    chunker = Chunker()
    body = "Sentence one is here. Sentence two follows along. Sentence three ends it."
    assert len(body) < MAX_LEAF_CHARS
    long_body = " ".join([body] * 12)
    assert len(long_body) > MAX_LEAF_CHARS
    text = f"## 08:00 — t\n- {long_body}\n- short tail\n"
    chunks = chunker.chunk_markdown(
        "/m/2026-08-01.md", text, source_kind="daily", timeline_day="2026-08-01"
    )
    assert len(chunks) >= 2
    for chunk in chunks:
        assert 1 <= chunk.line_start <= chunk.line_end <= 3
        assert len(chunk.text_raw) <= MAX_LEAF_CHARS
    single = "x" * 2000
    chunks = chunker.chunk_markdown(
        "/m/2026-08-01.md",
        f"## 08:00 — t\n- {single}\n",
        source_kind="daily",
        timeline_day="2026-08-01",
    )
    assert len(chunks) >= 2
    assert all(len(chunk.text_raw) <= MAX_LEAF_CHARS for chunk in chunks)
    assert "".join(chunk.text_raw for chunk in chunks).replace(" ", "") == "-" + single
