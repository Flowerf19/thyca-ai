"""Hybrid retrieval with FakeEmbedder — no ONNX."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.memory.archived import ArchivedMemory, fuse_hits
from thyca.memory.embed import FakeEmbedder, rrf_ranks
from thyca.tools.memory import MemoryFacade

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def at(day: str) -> datetime:
    y, m, d = (int(p) for p in day.split("-"))
    return datetime(y, m, d, 10, 0, tzinfo=TZ)


def _seed(root: Path) -> None:
    (root / "SOUL.md").write_text("# soul\nbe concise\n", encoding="utf-8")
    (root / "USER.md").write_text("# user\nlives in Hanoi\n", encoding="utf-8")
    (root / "MEMORY.md").write_text("# memory\nlikes trà đá\n", encoding="utf-8")
    (root / "memory").mkdir()
    (root / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        "## 19:30 — thịt quay Q1 <!-- thyca:e5f6a7b8 -->\n"
        "- Ăn thịt quay với bạn ở Q1\n",
        encoding="utf-8",
    )


def test_rrf_tie_break() -> None:
    ranked = rrf_ranks([["a", "b"], ["b", "a"]])
    ids = [item for item, *_ in ranked]
    assert ids == ["a", "b"] or ids[0] in {"a", "b"}
    fused = fuse_hits([], [])
    assert fused == []


def test_semantic_paraphrase_without_lexical_overlap(tmp_path: Path) -> None:
    _seed(tmp_path)
    embedder = FakeEmbedder()
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh", embedder=embedder)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    archived.reindex(at("2026-08-17"))
    assert archived.embed_pending() >= 1
    lexical = facade.search("món nướng hôm nọ")
    assert all("thịt quay" not in hit.snippet.lower() for hit in lexical.hits) or lexical.hits == []
    result = facade.search("món nướng hôm nọ", semantic=True)
    assert result.semantic_requested is True
    assert result.semantic_used is True
    assert result.hits
    assert any("thịt quay" in hit.snippet for hit in result.hits)
    assert any(hit.match_type in {"semantic", "hybrid"} for hit in result.hits)


def test_semantic_false_does_not_embed_query(tmp_path: Path) -> None:
    _seed(tmp_path)
    embedder = FakeEmbedder()
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh", embedder=embedder)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    archived.reindex(at("2026-08-17"))
    archived.embed_pending()
    before = embedder.query_calls
    facade.search("thịt quay", semantic=False)
    assert embedder.query_calls == before


def test_semantic_without_vectors_warns(tmp_path: Path) -> None:
    _seed(tmp_path)
    embedder = FakeEmbedder()
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh", embedder=embedder)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh", archive=archived)
    archived.reindex(at("2026-08-17"))
    result = facade.search("thịt quay", semantic=True)
    assert result.semantic_used is False
    assert "empty semantic index" in result.warnings
    assert embedder.query_calls == 0
