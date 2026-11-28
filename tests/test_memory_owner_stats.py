"""Ownership-scoped inventory and existing leaf counters; no actor inference."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from thyca.memories.chunk import Chunk
from thyca.memories.facade import MemoryFacade
from thyca.memories.heading import format_ts
from thyca.memories.stats import CanonicalFile, MemoryStats


def archived(entry, owners, **metadata):
    sid = f"2026-08-16#{entry}"
    return {
        "chunk_id": sid + "#1", "memory_id": sid,
        "heading_raw": "## 10:00 — title", "text_raw": "memory body",
        "source_kind": "daily", "timeline_day": "2026-08-16",
        "expires_at": None, "bots": json.dumps(owners) if owners is not None else None,
        **metadata,
    }


def today(entry, owners):
    sid = f"2026-08-17#{entry}"
    return Chunk(
        chunk_id=sid + "#1", path="/isolated/memory/2026-08-17.md",
        source_kind="daily", timeline_day="2026-08-17", memory_id=sid,
        memory_title="today", heading_raw="## 10:00 — today", leaf_ord=1,
        line_start=1, line_end=2, text_raw="today memory", text_norm="today memory",
        content_hash="hash", bots=tuple(owners) if owners is not None else None,
    )


def test_owner_filter_precedes_every_inventory_and_ranking_metric():
    rows = [
        archived("aaaaaaaa", ["coder"], kind="checkpoint", task="task-11223344", status="open"),
        archived("bbbbbbbb", ["researcher"]),
        archived("cccccccc", ["team"]),
        archived("dddddddd", None),
        archived("eeeeeeee", ["coder", "researcher"]),
        archived("ffffffff", ["coder", "team"]),
    ]
    gets = {rows[0]["chunk_id"]: (3, "2026-08-16T10:00:00Z"),
            rows[1]["chunk_id"]: (999, "2026-08-16T10:00:00Z"),
            rows[4]["chunk_id"]: (1, "2026-08-16T10:00:00Z")}
    searches = {rows[0]["chunk_id"]: (7, "2026-08-16T10:00:00Z")}
    chunks = [today("11111111", ["coder"]), today("22222222", ["researcher"]),
              today("33333333", ["team"]), today("44444444", None)]
    kwargs = {"archived": [*rows, rows[0]], "today_chunks": chunks, "gets": gets,
                  "searches": searches, "today": "2026-08-17", "now_ts": "2026-08-17T12:00:00Z",
                  "files": [CanonicalFile("USER.md", "global profile")]}
    scoped = MemoryStats.build(**kwargs, bot_id="coder")
    assert scoped.bot_id == "coder"
    assert scoped.total == 3
    assert (scoped.used, scoped.unused, scoped.searched, scoped.untouched) == (2, 1, 1, 1)
    assert {leaf.bots for leaf in scoped.leaves} == {("coder",), ("coder", "researcher")}
    assert sum(leaf.get_count for leaf in scoped.leaves) == 4
    assert sum(leaf.search_count for leaf in scoped.leaves) == 7
    assert scoped.files == []
    checkpoint = next(leaf for leaf in scoped.leaves if leaf.kind == "checkpoint")
    assert checkpoint.task == "task-11223344" and checkpoint.status == "open"
    assert all(leaf.bots and "team" not in leaf.bots for leaf in scoped.expiring)
    assert all(leaf.bots and "team" not in leaf.bots for leaf in scoped.suggest_removal)
    overall = MemoryStats.build(**kwargs)
    assert overall.bot_id is None
    assert overall.total == 10
    assert len(overall.files) == 1
    other = MemoryStats.build(**kwargs, bot_id="researcher")
    assert other.total == 3
    assert sum(leaf.get_count for leaf in other.leaves) == 1000


def test_facade_scopes_today_and_archived_using_stored_owner_metadata(tmp_path):
    now = datetime(2026, 8, 17, 12, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    tomorrow = datetime(2026, 8, 18, 12, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    own = facade.remember("Own", "coder-owned habit observation", bots=["coder"],
                          kind="habit", habit="language", now=now)
    facade.remember("Other", "researcher-owned observation", bots=["researcher"], now=now)
    facade.remember("Team", "shared memory observation", bots=["team"], now=now)
    facade.remember("Unassigned", "entry without owner metadata", now=now)
    before = facade.stats(now=now, bot_id="coder")
    assert before.total == 1
    assert before.leaves[0].is_today is True
    assert before.leaves[0].bots == ("coder",)
    assert before.leaves[0].kind == "habit" and before.leaves[0].habit == "language"
    facade.archive.reindex(tomorrow)
    facade.archive.store.usage.record_gets([own + "#1"], own, format_ts(tomorrow))
    facade.archive.store.usage.record_searches([own + "#1"], own, format_ts(tomorrow))
    scoped = facade.stats(now=tomorrow, bot_id="coder")
    assert scoped.bot_id == "coder"
    assert scoped.total == 1 and scoped.used == 1 and scoped.searched == 1
    assert scoped.leaves[0].is_today is False
    assert scoped.leaves[0].bots == ("coder",)
    assert scoped.leaves[0].kind == "habit" and scoped.leaves[0].habit == "language"
    assert scoped.leaves[0].get_count == scoped.leaves[0].search_count == 1
    assert facade.stats(now=tomorrow).total == 4
    assert facade.stats(now=tomorrow, bot_id="absent").total == 0


def test_owner_scoped_stats_merges_multichunk_entries(tmp_path):
    now = datetime(2026, 8, 17, 12, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    tomorrow = datetime(2026, 8, 18, 12, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    own = facade.remember(
        "Coder long entry",
        "Đoạn mở đầu rất dài để chắc chắn vượt ngưỡng tách chunk " + ("chi tiết kỹ thuật " * 55) + "PART_ONE.",
        content="Đoạn bổ sung thứ hai sau dấu chấm để tách sang chunk tiếp theo. " + ("dữ liệu cấu hình " * 45) + "PART_TWO.",
        bots=["coder"],
        now=now,
    )
    facade.remember(
        "Researcher long entry",
        "Đoạn một của researcher rất dài " + ("nghiên cứu hệ thống " * 55) + "RES_ONE.",
        content="Đoạn hai của researcher tiếp theo RES_TWO.",
        bots=["researcher"],
        now=now,
    )
    facade.archive.reindex(tomorrow)
    assert facade.archive.store.memory_leaf_count(own, format_ts(tomorrow)) >= 2
    facade.archive.store.usage.record_gets([f"{own}#1"], own, format_ts(tomorrow))
    facade.archive.store.usage.record_searches([f"{own}#2"], own, format_ts(tomorrow))

    scoped = facade.stats(now=tomorrow, bot_id="coder")
    assert scoped.total == 1
    assert scoped.used == 1
    assert scoped.searched == 1
    assert len(scoped.leaves) == 1
    leaf = scoped.leaves[0]
    assert leaf.memory_id == own
    assert leaf.chunk_id == f"{own}#1"
    assert "PART_ONE" in leaf.snippet and "PART_TWO" in leaf.snippet
    assert facade.stats(now=tomorrow).total == 2
