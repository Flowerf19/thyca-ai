"""Wave 3 regression tests: TTL scale, task ownership, resurrection, dormancy, decisions, priority."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.bench.memory_edge_common import BASE_NOW, TZ, TZ_NAME
from tests.bench.memory_edge_pack_a import (
    _sim_task_child_bot_close_bypass,
    _sim_task_closing_entry_expiry_resurrection,
    _sim_task_cross_file_last_line_tiebreak_inversion,
    _sim_task_upsert_over_50_tasks_duplicate,
)
from tests.bench.memory_edge_suite import (
    _sim_ghost_31_days_ttl_silent_deletion,
    _sim_ghost_zombie_tasks_prompt_bloat,
    _sim_task_inplace_decision_overwrite_risk,
    _sim_task_update_checkpoint_recognition,
    _sim_task_update_via_memory_update_staleness,
    _sim_window_priority_current_vs_other_chat,
)
from thyca.core.protocol import ToolCall
from thyca.memories import jobs
from thyca.memories.archive_store import ArchiveError
from thyca.memories.facade import MemoryFacade
from thyca.memories.heading import (
    DEFAULT_IMPORTANCE,
    HABIT_DEFAULT_IMPORTANCE,
    TASK_DEFAULT_IMPORTANCE,
    TTL_DAYS,
    parse_heading,
)
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.memories.task_ledger import format_task_catalog
from thyca.tools.memory_tools import sidecar_memory_tools


def test_ttl_days_matches_accepted_3d_to_90d_scale() -> None:
    assert TTL_DAYS == {1: 3, 2: 7, 3: 14, 4: 30, 5: 90}
    assert TASK_DEFAULT_IMPORTANCE == 5


def test_wave3_task_ownership_and_resurrection(tmp_path: Path) -> None:
    for runner in (
        _sim_task_child_bot_close_bypass,
        _sim_task_closing_entry_expiry_resurrection,
        _sim_task_cross_file_last_line_tiebreak_inversion,
        _sim_task_upsert_over_50_tasks_duplicate,
    ):
        case_dir = tmp_path / runner.__name__
        case_dir.mkdir()
        res = runner(case_dir)
        assert res.passed, f"{res.case_id}: {res.findings}"


def test_wave3_task_updates_decisions_dormancy_and_priority(tmp_path: Path) -> None:
    for runner in (
        _sim_task_update_via_memory_update_staleness,
        _sim_task_update_checkpoint_recognition,
        _sim_task_inplace_decision_overwrite_risk,
        _sim_ghost_31_days_ttl_silent_deletion,
        _sim_ghost_zombie_tasks_prompt_bloat,
        _sim_window_priority_current_vs_other_chat,
    ):
        case_dir = tmp_path / runner.__name__
        case_dir.mkdir()
        res = runner(case_dir)
        assert res.passed, f"{res.case_id}: {res.findings}"


def test_dormant_tasks_footer_and_90d_expiry_boundary(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    day1 = datetime(2026, 8, 1, 9, 0, tzinfo=TZ)
    tid = facade.new_task_id()
    facade.remember("Long task", "Initial state", task=tid, status="open", now=day1)

    day80 = day1 + timedelta(days=80)
    assert any(t["task_id"] == tid for t in facade.tasks(status="open", now=day80)["tasks"])
    rendered_d80 = format_task_catalog(
        facade.tasks(status="open", now=day80)["tasks"],
        reference_day=day80.strftime("%Y-%m-%d"),
    )
    assert tid not in rendered_d80
    assert "+1 older open tasks" in rendered_d80

    day92 = day1 + timedelta(days=92)
    facade.append_other_journal("Ping", "Trigger purge check", now=day92)
    assert facade.tasks(status="open", now=day92)["tasks"] == []


def test_new_decisions_replace_old_decisions_when_explicitly_given(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    tid = facade.new_task_id()
    sid = facade.remember(
        "DB choice",
        "Init",
        content="- decisions: MySQL 8\n- next: benchmark",
        task=tid,
        status="open",
        chat="dm-1",
        now=BASE_NOW,
    )
    facade.upsert_task_card(
        "DB choice",
        "Switched engine",
        content="- decisions: PostgreSQL 16\n- next: migrate",
        task=tid,
        chat="dm-1",
        target_memory_id=sid,
        now=BASE_NOW + timedelta(hours=1),
    )
    body = facade.get(memory_id=sid, now=BASE_NOW + timedelta(hours=1))
    assert "PostgreSQL 16" in body
    assert "MySQL 8" not in body


def test_child_bot_cannot_close_thyca_task_via_any_bypass_path(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    tok_thyca = bind_bot_id("thyca")
    try:
        tid = facade.new_task_id()
        facade.remember(
            "Release prod",
            "Thyca opened task",
            task=tid,
            kind="checkpoint",
            status="open",
            bots=["thyca"],
            now=BASE_NOW,
        )
    finally:
        reset_bot_id(tok_thyca)

    tok_child = bind_bot_id("worker-a")
    try:
        own_sid = facade.remember(
            "Worker note",
            "Unrelated child note",
            bots=["worker-a"],
            now=BASE_NOW + timedelta(minutes=1),
        )
        with pytest.raises(ArchiveError, match="cannot close another owner's task"):
            facade.upsert_task_card(
                "Release prod",
                "Bypass via own cont",
                task=tid,
                status="done",
                target_memory_id=own_sid,
                now=BASE_NOW + timedelta(minutes=2),
            )
        progress_sid = facade.upsert_task_card(
            "Release prod",
            "Worker added progress",
            task=tid,
            status="open",
            now=BASE_NOW + timedelta(minutes=3),
        )
        with pytest.raises(ArchiveError, match="cannot close another owner's task"):
            facade.upsert_task_card(
                "Release prod",
                "Bypass via 2nd call close",
                task=tid,
                status="done",
                now=BASE_NOW + timedelta(minutes=4),
            )
        with pytest.raises(ArchiveError, match="cannot close another owner's task"):
            facade.remember(
                "Release prod",
                "Bypass via habit kind",
                task=tid,
                kind="habit",
                habit="release",
                status="done",
                now=BASE_NOW + timedelta(minutes=5),
            )
        with pytest.raises(ArchiveError, match="cannot close another owner's task"):
            facade.update(
                progress_sid,
                summary="Bypass via memory_update",
                status="done",
                now=BASE_NOW + timedelta(minutes=6),
            )

        own_tid = facade.new_task_id()
        facade.remember(
            "Worker own task",
            "Opened by worker-a",
            task=own_tid,
            kind="checkpoint",
            status="open",
            now=BASE_NOW + timedelta(minutes=7),
        )
        facade.upsert_task_card(
            "Worker own task",
            "Closed by opener worker-a",
            task=own_tid,
            status="done",
            now=BASE_NOW + timedelta(minutes=8),
        )
    finally:
        reset_bot_id(tok_child)

    tok_check = bind_bot_id("thyca")
    try:
        open_ids = [t["task_id"] for t in facade.tasks(status="open", now=BASE_NOW + timedelta(minutes=10))["tasks"]]
        done_ids = [t["task_id"] for t in facade.tasks(status="done", now=BASE_NOW + timedelta(minutes=10))["tasks"]]
    finally:
        reset_bot_id(tok_check)
    assert tid in open_ids
    assert tid not in done_ids
    assert own_tid in done_ids


def test_ledger_default_importances_preserved_in_remember_and_sidecar_extraction(
    tmp_path: Path,
) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    facade.remember(
        "Tea habit",
        "Drinks tea each morning",
        kind="habit",
        habit="morning tea",
        now=BASE_NOW,
    )
    registry = sidecar_memory_tools(facade)
    asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="w1",
                    name="memory_task",
                    arguments={"topic": "Extract Task", "summary": "New multi-step work"},
                ),
                ToolCall(
                    id="w2",
                    name="memory_note",
                    arguments={
                        "topic": "Extract Habit",
                        "summary": "Short replies",
                        "habit": "short replies",
                    },
                ),
                ToolCall(
                    id="w3",
                    name="memory_note",
                    arguments={"topic": "Extract Other", "summary": "Server IP 10.0.0.5"},
                ),
            ],
            bot_id="thyca",
            chat="dm-1",
        )
    )
    day_files = sorted((tmp_path / "memory").glob("*.md"))
    by_title = {}
    for day_file in day_files:
        for line in day_file.read_text(encoding="utf-8").splitlines():
            meta = parse_heading(line)
            if meta is not None:
                by_title[meta.title] = meta.importance
    assert by_title["Tea habit"] == HABIT_DEFAULT_IMPORTANCE
    assert by_title["Extract Task"] == TASK_DEFAULT_IMPORTANCE
    assert by_title["Extract Habit"] == HABIT_DEFAULT_IMPORTANCE
    assert by_title["Extract Other"] == DEFAULT_IMPORTANCE
