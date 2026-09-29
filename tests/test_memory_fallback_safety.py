"""GOAL-001: safe, truthful memory fallback (regression).

Unindexed-today chunk reads must reuse the shared read policy
(symlink-safe), honor expiry without auto-renewal, keep typed read
errors distinct from chunk-not-found, and preserve explicit limit=0.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from thyca.core.protocol import ToolCall
from thyca.memory.archive_store import ArchiveError
from thyca.memory.facade import MemoryFacade
from thyca.tools.gateway import ToolGateway
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TODAY = "2026-09-29"


def _now() -> datetime:
    return datetime(2026, 9, 29, 10, 0, tzinfo=TZ)


def _seed(tmp_path: Path, body: str, day: str = TODAY) -> Path:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / f"{day}.md"
    path.write_text(f"# {day}\n{body}", encoding="utf-8")
    return path


def _heading(entry: str, title: str, exp: str) -> str:
    return (
        f"## 08:00 — {title} "
        f"<!-- thyca {{\"id\":\"{entry}\",\"imp\":3,\"exp\":\"{exp}\"}} -->\n"
    )


def _facade(tmp_path: Path) -> MemoryFacade:
    return MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")


def test_expired_today_chunk_is_rejected_not_renewed(tmp_path: Path) -> None:
    path = _seed(
        tmp_path,
        _heading("eeeeeeee", "old note", "2020-01-01T00:00:00Z")
        + "- this leaf expired long ago\n",
    )
    facade = _facade(tmp_path)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ArchiveError, match="chunk expired:"):
        facade.get(chunk_id=f"{TODAY}#eeeeeeee#1", now=_now())
    # No auto-renewal: the file bytes are untouched.
    assert path.read_text(encoding="utf-8") == before
    assert "2020-01-01T00:00:00Z" in before
    # No usage recorded for the rejected read.
    assert facade.archive.store.usage.get_map() == {}


def test_live_today_chunk_still_reads(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        _heading("eeeeeeee", "fresh note", "2027-09-12T00:00:00Z")
        + "- this leaf is still alive\n",
    )
    facade = _facade(tmp_path)
    assert "still alive" in facade.get(
        chunk_id=f"{TODAY}#eeeeeeee#1", now=_now()
    )


def test_today_symlink_is_not_followed_or_replaced(tmp_path: Path) -> None:
    external = tmp_path / "external.md"
    external.write_text(
        "# external\n"
        + _heading("eeeeeeee", "outside note", "2027-09-12T00:00:00Z")
        + "- outside note here\n",
        encoding="utf-8",
    )
    link = tmp_path / "memory" / f"{TODAY}.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external)
    facade = _facade(tmp_path)
    before = external.read_bytes()
    with pytest.raises(ArchiveError, match="chunk not found:"):
        facade.get(chunk_id=f"{TODAY}#eeeeeeee#1", now=_now())
    assert link.is_symlink()
    assert "outside note here" in external.read_text(encoding="utf-8")
    assert external.read_bytes() == before
    assert facade.archive.store.usage.get_map() == {}


def test_today_undecodable_file_keeps_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "memory" / f"{TODAY}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"# 2026-09-29\n\xff not utf8 \xfe\n")
    facade = _facade(tmp_path)
    with pytest.raises(ArchiveError, match="not valid UTF-8"):
        facade.get(chunk_id=f"{TODAY}#eeeeeeee#1", now=_now())


def test_duplicate_legacy_titles_resolve_second_occurrence(
    tmp_path: Path,
) -> None:
    from thyca.memory.heading import legacy_entry_id

    day_path = str(tmp_path / "memory" / f"{TODAY}.md")
    first = legacy_entry_id(day_path, "same title", 1)
    second = legacy_entry_id(day_path, "same title", 2)
    assert first != second
    _seed(
        tmp_path,
        "## 08:00 — same title\n- first body text\n"
        "## 09:00 — same title\n- second body text\n",
    )
    facade = _facade(tmp_path)
    assert "first body" in facade.get(
        chunk_id=f"{TODAY}#{first}#1", now=_now()
    )
    assert "second body" in facade.get(
        chunk_id=f"{TODAY}#{second}#1", now=_now()
    )


async def test_gateway_memory_search_preserves_explicit_zero(
    tmp_path: Path,
) -> None:
    facade = _facade(tmp_path)
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(
        ToolCall(id="s0", name="memory_search", arguments={"query": "x", "limit": 0})
    )
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["warnings"] == ["limit clamped from 0 to 1"]
