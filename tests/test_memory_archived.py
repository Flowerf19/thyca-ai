"""Archived lexical memory — GOAL-002 / TASK-104-107."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.memory import ArchiveError, ArchivedMemory, Chunker
from thyca.tools.memory import MemoryFacade

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def at(day: str) -> datetime:
    y, m, d = (int(p) for p in day.split("-"))
    return datetime(y, m, d, 10, 0, tzinfo=TZ)


def _seed(root: Path) -> None:
    (root / "SOUL.md").write_text("# soul\nbe concise\n", encoding="utf-8")
    (root / "USER.md").write_text("# user\nlives in Hanoi\n", encoding="utf-8")
    (root / "MEMORY.md").write_text("# memory\nlikes cà phê\n", encoding="utf-8")
    (root / "memory").mkdir()
    (root / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        "## 08:00 — ăn sáng bún bò <!-- thyca:a1b2c3d4 -->\n"
        "- Ăn bún bò Huế ở quán X\n"
        "- nói chuyện với Luna\n"
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
    assert archived.store.vec_version is not None
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
    semantic = facade.search("thịt quay", semantic=True)
    assert semantic.semantic_requested is True
    assert semantic.semantic_used is False
    assert "semantic unavailable" in semantic.warnings


def test_trigram_typo_and_get(tmp_path: Path) -> None:
    _seed(tmp_path)
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    archived.reindex(at("2026-08-17"))
    hits = facade.search("thit quya")
    assert hits.hits
    session = archived.get(session_id=hits.hits[0].session_id)
    assert "thịt quay" in session or "bún bò" in session or "Luna" in session
    raw = archived.get(path=str(tmp_path / "MEMORY.md"))
    assert "cà phê" in raw
    try:
        archived.get(path=str(tmp_path / "sessions" / "x.jsonl"))
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
    assert facade.search("cà phê").hits
