"""Offline failure-case simulation suite for agent memory & task lifecycle.

Exercises the real MemoryFacade, ActiveMemory, WorkspaceContextBuilder,
PromptManager, checkpoint_state, MemoryView, and BackgroundJobsService
against real-world edge cases (task staleness, ghosting/TTL, zombie tasks,
window priority/isolation, same-day truncation, prompt rule gaps).

Run::

    .venv/bin/python tests/bench/memory_edge_suite.py
    .venv/bin/python tests/bench/memory_edge_suite.py --json-out /tmp/memory_edge.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.bench.memory_edge_common import (
    BASE_NOW,
    TZ,
    TZ_NAME,
    EdgeCaseResult,
    StubExtractorConnect,
)
from tests.bench.memory_edge_pack_a import PACK_A_SIMULATIONS
from thyca.agents.context_budget import checkpoint_state
from thyca.app.background_jobs.service import BackgroundJobsDeps, BackgroundJobsService
from thyca.bots.bus import Bus
from thyca.bots.registry import SYSTEM_BOT_ID, BotRegistry
from thyca.bots.runtime import BotRuntime
from thyca.bots.workspace_context import WorkspaceContextBuilder
from thyca.configs import Config, LimitsCfg, ProviderEntry, load, save
from thyca.conversations.runtime import ConversationManager
from thyca.conversations.store.runtime import ensure_runtime_store
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message, ToolCall
from thyca.llms.prompt_manager import PromptManager
from thyca.memories.active import ELSEWHERE_MAX_LINES, ActiveMemory
from thyca.memories.facade import MemoryFacade
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.memories.visibility import MemoryView, bound_memory_view


def _sim_task_update_via_memory_update_staleness(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    day1 = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    day20 = datetime(2026, 11, 20, 14, 0, tzinfo=TZ)
    day32 = datetime(2026, 12, 3, 10, 0, tzinfo=TZ)

    tid = facade.new_task_id()
    sid = facade.remember(
        "Migration Redis",
        "Bắt đầu kiểm tra Redis cluster",
        content="- done: khảo sát node\n- next: nâng cấp replica",
        task=tid,
        kind="checkpoint",
        status="open",
        now=day1,
    )
    facade.update(
        sid,
        summary="Đã nâng cấp replica, chờ failover",
        content="- done: khảo sát node, nâng cấp replica\n- next: failover",
        now=day20,
    )

    catalog_d20 = facade.tasks(status="open", now=day20)["tasks"]
    last_day_d20 = catalog_d20[0]["last_day"] if catalog_d20 else None
    catalog_d32 = facade.tasks(status="open", now=day32)["tasks"]

    findings: list[str] = []
    if last_day_d20 != "2026-11-20":
        findings.append(
            f"memory_update on day 20 did not advance last_day (got {last_day_d20!r}, "
            "expected '2026-11-20')"
        )
    if not any(t["task_id"] == tid for t in catalog_d32):
        findings.append(
            "task updated via memory_update on day 20 expired before day 32 "
            "(facade.update does not reinforce TTL)"
        )
    return EdgeCaseResult(
        case_id="task_update_via_memory_update_staleness",
        group="task",
        passed=not findings,
        summary="Update task card via memory_update should refresh last_day and extend TTL",
        findings=findings,
        metrics={
            "last_day_after_update": last_day_d20,
            "alive_on_day32": bool(catalog_d32),
        },
    )


def _sim_task_update_checkpoint_recognition(_root: Path) -> EdgeCaseResult:
    messages = [
        Message(role="user", content="Cập nhật giúp mình tiến độ task Redis."),
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="u1",
                    name="memory_task",
                    arguments={
                        "task": "task-abcd1234",
                        "summary": "Xong replica, chờ failover",
                        "status": "open",
                    },
                )
            ],
            meta={"estimate_tokens": 85_000},
        ),
        Message(
            role="tool",
            content='{"memory_id":"2026-11-25#abcd1234","task_id":"task-abcd1234"}',
            tool_call_id="u1",
        ),
    ]
    state = checkpoint_state(messages, context_tokens=100_000, current_ratio=0.85)
    findings: list[str] = []
    if state != "recorded":
        findings.append(
            f"memory_task update yielded checkpoint_state={state!r} instead of 'recorded'"
        )
    return EdgeCaseResult(
        case_id="task_update_checkpoint_recognition",
        group="task",
        passed=not findings,
        summary="Task update via memory_task at 85% context should satisfy checkpoint_state",
        findings=findings,
        metrics={"checkpoint_state": state},
    )


def _sim_task_inplace_decision_overwrite_risk(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    tid = facade.new_task_id()
    sid = facade.remember(
        "Thiết kế DB mới",
        "Chốt chọn PostgreSQL 16 thay MySQL vì hỗ trợ JSONB tốt hơn",
        content="- decisions: PostgreSQL 16, không dùng MySQL\n- next: viết schema",
        task=tid,
        kind="checkpoint",
        status="open",
        chat="dm-thyca",
        now=BASE_NOW,
    )
    facade.upsert_task_card(
        "Thiết kế DB mới",
        "Đã viết xong schema, bước tiếp theo là viết script seed",
        content="- done: viết schema\n- next: viết script seed",
        task=tid,
        chat="dm-thyca",
        target_memory_id=sid,
        now=BASE_NOW + timedelta(hours=1),
    )
    body = facade.get(memory_id=sid, now=BASE_NOW + timedelta(hours=1))
    findings: list[str] = []
    if "PostgreSQL 16" not in body:
        findings.append(
            "in-place upsert_task_card replaced body and dropped prior decision 'PostgreSQL 16' "
            "when follow-up update omitted earlier decisions"
        )
    return EdgeCaseResult(
        case_id="task_inplace_decision_overwrite_risk",
        group="task",
        passed=not findings,
        summary="Check whether in-place task update drops earlier key decisions if not repeated",
        findings=findings,
        metrics={"decision_preserved": "PostgreSQL 16" in body, "body_len": len(body)},
    )


def _sim_ghost_31_days_ttl_silent_deletion(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    day1 = datetime(2026, 10, 1, 9, 0, tzinfo=TZ)
    day32 = datetime(2026, 11, 2, 10, 0, tzinfo=TZ)

    tid = facade.new_task_id()
    facade.remember(
        "Triển khai OAuth2",
        "Đang chờ bên đối tác cấp client_id production",
        content="- done: luồng sandbox\n- next: cấu hình production",
        task=tid,
        kind="checkpoint",
        status="open",
        now=day1,
    )
    facade.append_other_journal(
        "Ghi chú khác",
        "Hôm nay đổi màn hình làm việc 27 inch",
        now=day32,
    )

    open_after = facade.tasks(status="open", now=day32)["tasks"]
    all_after = facade.tasks(now=day32)["tasks"]
    search_after = facade.search("OAuth2 client_id", now=day32).hits

    findings: list[str] = []
    if not any(t["task_id"] == tid for t in all_after):
        findings.append(
            "open task with default imp=3 was permanently purged after 31 days of ghosting "
            "(missing from memory_tasks)"
        )
    if not search_after:
        findings.append(
            "ghosted open task is also missing from memory_search on day 32 after purge_expired"
        )
    return EdgeCaseResult(
        case_id="ghost_31_days_ttl_silent_deletion",
        group="ghost",
        passed=not findings,
        summary="Open task after >30d user absence should remain recoverable, not silently purged",
        findings=findings,
        metrics={
            "open_count_day32": len(open_after),
            "all_task_count_day32": len(all_after),
            "search_hits_day32": len(search_after),
        },
    )


def _sim_ghost_zombie_tasks_prompt_bloat(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    for idx in range(12):
        old_day = BASE_NOW - timedelta(days=10 + idx)
        tid = facade.new_task_id()
        facade.remember(
            f"Old task {idx + 1}",
            f"Still pending old work item #{idx + 1}",
            task=tid,
            kind="checkpoint",
            status="open",
            chat="dm-thyca",
            now=old_day,
        )
    for idx in range(2):
        fresh_day = BASE_NOW - timedelta(days=idx)
        tid = facade.new_task_id()
        facade.remember(
            f"Fresh task {idx + 1}",
            f"Active current work item #{idx + 1}",
            task=tid,
            kind="checkpoint",
            status="open",
            chat="dm-thyca",
            now=fresh_day,
        )

    rows = facade.tasks(status="open", now=BASE_NOW)["tasks"]
    active = ActiveMemory(root, timezone_name=TZ_NAME)
    state = active.open_session(BASE_NOW)
    snap = active.refresh(state, BASE_NOW, conversation_id="dm-thyca")
    window = WindowRef(
        window_id="dm-thyca", channel_id="webui", kind="dm", participants=("thyca",)
    )
    hot = WorkspaceContextBuilder(root).build(
        snap, bot_id="thyca", window=window, memory_catalog=tuple(rows)
    )
    lines = [line for line in hot.memory_tasks.splitlines() if line.startswith("- task-")]
    dormant_in_prompt = [line for line in lines if "Old task " in line]

    findings: list[str] = []
    if dormant_in_prompt:
        findings.append(
            f"{len(dormant_in_prompt)} dormant open tasks (10-21 days idle) were injected "
            "directly into <memory_tasks> on every turn"
        )
    if len(lines) > 8:
        findings.append(f"<memory_tasks> has {len(lines)} task lines (exceeds clean 8-line cap)")
    return EdgeCaseResult(
        case_id="ghost_zombie_tasks_prompt_bloat",
        group="ghost",
        passed=not findings,
        summary="Dormant open tasks (>7d idle) should not clutter every turn's <memory_tasks>",
        findings=findings,
        metrics={
            "injected_task_lines": len(lines),
            "dormant_injected": len(dormant_in_prompt),
            "memory_tasks_chars": len(hot.memory_tasks),
        },
    )


def _sim_ghost_idle_sweep_15m_lifecycle(root: Path) -> EdgeCaseResult:
    ensure_runtime_store(root)
    cfg_path = root / "config.json"
    save(
        Config(
            providers={
                "stub": ProviderEntry(
                    baseUrl="http://127.0.0.1:9999/v1",
                    apiKey="stub-key",
                )
            },
            defaultProvider="stub",
            defaultModel="stub-model",
            limits=LimitsCfg(contextTokens=32000),
        ),
        cfg_path,
    )
    cfg = load(cfg_path)
    bots = BotRegistry(root)
    bots.ensure_thyca()
    convs = ConversationManager(root / "sessions", timezone_name=TZ_NAME)
    dm = convs.ensure_dm(SYSTEM_BOT_ID)
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    runtime = BotRuntime(root, bots, Bus(root))

    window = WindowRef(
        window_id=dm.conversation_id,
        channel_id="webui",
        kind="dm",
        participants=(SYSTEM_BOT_ID,),
    )
    internal_id = "2026-11-25T09-00-00_ab12"
    runtime.workspaces.workspace(SYSTEM_BOT_ID).bind_window(
        window, internal_conversation_id=internal_id
    )
    canonical = ConversationManager(
        limits=cfg.effective_limits(),
        timezone_name=TZ_NAME,
        store=runtime._canonical_store(SYSTEM_BOT_ID, window, internal_id),
    )
    canonical.load(internal_id)
    old_ts = (datetime.now(UTC) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for idx in range(25):
        canonical.append(
            Message(
                role="user",
                content=(
                    f"Lượt {idx + 1}: Đang tối ưu truy vấn báo cáo doanh thu, "
                    "đã thêm index composite (org_id, created_at) và chờ chạy EXPLAIN ANALYZE. "
                    + ("Chi tiết log query chậm trên bảng invoices cần kiểm tra kỹ. " * 32)
                ),
                ts=old_ts,
            )
        )
        canonical.append(
            Message(
                role="assistant",
                content=(
                    f"Ghi nhận bước {idx + 1}: giữ quyết định composite index, "
                    "bước tiếp theo là đo p95 sau khi chạy EXPLAIN ANALYZE. "
                    + ("Phân tích plan quét bảng và bộ đệm chi tiết cho báo cáo. " * 32)
                ),
                ts=old_ts,
            )
        )

    stub = StubExtractorConnect(
        [
            ToolCall(
                id="ext-task-1",
                name="memory_task",
                arguments={
                    "topic": "Tối ưu truy vấn báo cáo doanh thu",
                    "summary": "Đã thêm composite index, chờ chạy EXPLAIN ANALYZE",
                    "content": (
                        "- done: thêm index (org_id, created_at)\n"
                        "- processing: chờ đo p95\n"
                        "- next: chạy EXPLAIN ANALYZE"
                    ),
                    "status": "open",
                },
            )
        ]
    )

    class _NoRunningTurns:
        @staticmethod
        def snapshot() -> set[str]:
            return set()

    loop = asyncio.new_event_loop()
    try:
        deps = BackgroundJobsDeps(
            root=root,
            conversations=convs,
            bots=bots,
            runtime=runtime,
            turns=_NoRunningTurns(),
            window_for=lambda _conv: window,
            current_cfg=lambda: cfg,
            construction_cfg=cfg,
            submit=loop.run_until_complete,
            scheduled_turn=lambda _bot, _prompt: {"reply": "ok"},
            injected_connect=stub,
            facade=facade,
        )
        svc = BackgroundJobsService(deps)
        svc._profile_last_run = 10**12
        svc._profile_signatures = {}
        report1 = svc.sweep_idle_conversations(idle_s=900)
        report2 = svc.sweep_idle_conversations(idle_s=900)
    finally:
        loop.close()

    open_tasks = facade.tasks(status="open")["tasks"]
    reinject_armed = (SYSTEM_BOT_ID, dm.conversation_id) in runtime._reinject_pending

    findings: list[str] = []
    if not open_tasks:
        findings.append("idle sweep did not persist open task from ghosted conversation")
    if stub.calls != 1:
        findings.append(f"expected watermark to keep extractor at 1 call, got {stub.calls}")
    if not reinject_armed:
        findings.append("idle sweep compaction did not arm _reinject_pending for next return turn")
    if report2["bots"]:
        findings.append(f"second idle sweep was not a no-op: {report2['bots']}")
    return EdgeCaseResult(
        case_id="ghost_idle_sweep_15m_lifecycle",
        group="ghost",
        passed=not findings,
        summary="15m idle sweep should flush open task once, compact, arm recall, and not re-flush",
        findings=findings,
        metrics={
            "extractor_calls": stub.calls,
            "open_tasks_after": len(open_tasks),
            "reinject_armed": reinject_armed,
            "first_sweep_bots": len(report1["bots"]),
            "second_sweep_bots": len(report2["bots"]),
        },
    )


def _sim_window_priority_current_vs_other_chat(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    tid_here = facade.new_task_id()
    facade.remember(
        "Current window task",
        "Active work inside dm-here",
        task=tid_here,
        kind="checkpoint",
        status="open",
        chat="dm-here",
        now=BASE_NOW - timedelta(days=2),
    )
    for idx in range(3):
        tid_other = facade.new_task_id()
        facade.remember(
            f"Other window task {idx + 1}",
            f"Work from dm-other #{idx + 1}",
            task=tid_other,
            kind="checkpoint",
            status="open",
            chat=f"dm-other-{idx + 1}",
            now=BASE_NOW,
        )

    rows = facade.tasks(status="open", now=BASE_NOW)["tasks"]
    active = ActiveMemory(root, timezone_name=TZ_NAME)
    state = active.open_session(BASE_NOW)
    snap = active.refresh(state, BASE_NOW, conversation_id="dm-here")
    window = WindowRef(
        window_id="dm-here", channel_id="webui", kind="dm", participants=("thyca",)
    )
    hot = WorkspaceContextBuilder(root).build(
        snap, bot_id="thyca", window=window, memory_catalog=tuple(rows)
    )
    lines = [line for line in hot.memory_tasks.splitlines() if line.startswith("- task-")]
    first_line = lines[0] if lines else ""

    findings: list[str] = []
    if tid_here not in first_line:
        findings.append(
            f"current-window task ({tid_here}) was NOT prioritized first in <memory_tasks>; "
            f"first line was: {first_line!r} (work_items.py leaves current_window=None for memory_row)"
        )
    return EdgeCaseResult(
        case_id="window_priority_current_vs_other_chat",
        group="window",
        passed=not findings,
        summary="Tasks from the active chat window should appear first in <memory_tasks>",
        findings=findings,
        metrics={"first_task_line": first_line, "expected_task_id": tid_here},
    )


def _sim_cross_window_task_update_aggregation(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    tid = facade.new_task_id()
    sid1 = facade.remember(
        "Triển khai CI",
        "Bước 1 tạo workflow",
        task=tid,
        kind="checkpoint",
        status="open",
        chat="window-1",
        now=BASE_NOW - timedelta(hours=2),
    )
    sid2 = facade.upsert_task_card(
        "Triển khai CI",
        "Bước 2 chạy pass trên staging",
        task=tid,
        status="open",
        chat="window-2",
        target_memory_id=sid1,
        now=BASE_NOW - timedelta(hours=1),
    )
    sid3 = facade.upsert_task_card(
        "Triển khai CI",
        "Hoàn tất CI",
        task=tid,
        status="done",
        chat="window-2",
        target_memory_id=sid2,
        now=BASE_NOW,
    )
    open_after = facade.tasks(status="open", now=BASE_NOW)["tasks"]
    done_after = facade.tasks(status="done", now=BASE_NOW)["tasks"]

    findings: list[str] = []
    if open_after:
        findings.append(f"task still appeared in open catalog after close in window-2: {open_after}")
    if not any(t["task_id"] == tid and t["latest_memory_id"] == sid3 for t in done_after):
        findings.append(f"closed cross-window task missing expected latest_memory_id={sid3}")
    return EdgeCaseResult(
        case_id="cross_window_task_update_aggregation",
        group="window",
        passed=not findings,
        summary="Updating and closing a task from a second window should aggregate cleanly",
        findings=findings,
        metrics={
            "sid1": sid1,
            "sid2": sid2,
            "sid3": sid3,
            "split_across_windows": sid1 != sid2,
            "reused_in_window2": sid2 == sid3,
            "done_entry_count": done_after[0]["entry_count"] if done_after else 0,
        },
    )


def _sim_dm_to_group_isolation_guard(root: Path) -> EdgeCaseResult:
    ensure_runtime_store(root)
    convs = ConversationManager(root / "sessions", timezone_name=TZ_NAME)
    dm = convs.ensure_dm("worker-a")
    group = convs.create()
    convs.set_bots(group.conversation_id, ["thyca", "worker-a"])
    facade = MemoryFacade(root, timezone_name=TZ_NAME)

    secret_phrase = "DM_PRIVATE_SALARY_99000"
    past_day = BASE_NOW - timedelta(days=2)
    tid_team = facade.new_task_id()
    facade.remember(
        "Mixed Team DM Task",
        f"Team mixed {secret_phrase}",
        task=tid_team,
        kind="checkpoint",
        status="open",
        bots=["worker-a", "team"],
        chat=dm.conversation_id,
        now=past_day,
    )
    tok = bind_bot_id("worker-a")
    try:
        tid = facade.new_task_id()
        facade.remember(
            "Private DM Task",
            f"Discuss {secret_phrase}",
            task=tid,
            kind="checkpoint",
            status="open",
            bots=["worker-a"],
            chat=dm.conversation_id,
            now=past_day,
        )
        facade.remember(
            "Private DM Today",
            f"Today note {secret_phrase}",
            bots=["worker-a"],
            chat=dm.conversation_id,
            now=BASE_NOW,
        )
        facade.archive.reindex(BASE_NOW)

        group_window = WindowRef(
            window_id=group.conversation_id,
            channel_id="webui",
            kind="group",
            participants=("thyca", "worker-a"),
            visibility="public",
        )
        view = MemoryView(window=group_window, bot_id="worker-a", thyca_dir=root)
        with bound_memory_view(view):
            active = ActiveMemory(root, timezone_name=TZ_NAME)
            state = active.open_session(BASE_NOW)
            snap = active.refresh(
                state, BASE_NOW, conversation_id=group.conversation_id, reader="worker-a"
            )
            auto_tasks = facade.tasks(status="open", now=BASE_NOW, allow_own_cross_window=False)[
                "tasks"
            ]
            tool_tasks = facade.tasks(status="open", now=BASE_NOW, allow_own_cross_window=True)[
                "tasks"
            ]
            tool_search = facade.search(
                f"Team mixed {secret_phrase}", now=BASE_NOW, allow_own_cross_window=True
            ).hits
    finally:
        reset_bot_id(tok)

    findings: list[str] = []
    if secret_phrase in snap.today or secret_phrase in snap.today_elsewhere:
        findings.append("private DM content leaked into group <today>/<today_elsewhere>")
    if any(t["task_id"] in (tid, tid_team) for t in auto_tasks):
        findings.append("private DM task leaked into group automatic <memory_tasks>")
    if any(t["task_id"] == tid_team for t in tool_tasks):
        findings.append("team/mixed DM task leaked into group via memory_tasks")
    if any("Mixed Team DM Task" in h.heading for h in tool_search):
        findings.append("team/mixed DM note leaked into group via memory_search")
    return EdgeCaseResult(
        case_id="dm_to_group_isolation_guard",
        group="window",
        passed=not findings,
        summary="Group auto-context blocks all DM entries; group tools block team/other-bot DM entries (Slice 1)",
        findings=findings,
        metrics={
            "prompt_leaked": secret_phrase in snap.today or secret_phrase in snap.today_elsewhere,
            "auto_tasks_leaked": len(auto_tasks),
            "team_tool_tasks_leaked": sum(1 for t in tool_tasks if t["task_id"] == tid_team),
            "team_tool_search_leaked": sum(
                1 for h in tool_search if "Mixed Team DM Task" in h.heading
            ),
        },
    )


def _sim_same_day_truncation_blind_spot(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    facade.remember(
        "Local Redis Early",
        "Redis local trên máy dev chạy cổng 6389 với mật khẩu test",
        chat="dm-thyca",
        now=BASE_NOW - timedelta(hours=3),
    )
    for idx in range(12):
        facade.remember(
            f"Long note {idx}",
            f"Padding entry {idx} " + ("chi tiết dài " * 25),
            chat="dm-thyca",
            now=BASE_NOW - timedelta(minutes=60 - idx),
        )
    active = ActiveMemory(root, tail_kb=1, timezone_name=TZ_NAME)
    state = active.open_session(BASE_NOW)
    snap = active.refresh(state, BASE_NOW, conversation_id="dm-thyca", tail_kb=1)
    in_today = "6389" in snap.today
    hits = facade.search("Redis 6389", now=BASE_NOW).hits

    findings: list[str] = []
    if not in_today and not hits:
        findings.append(
            "early today note ('6389') was truncated out of <today> AND is absent from "
            "memory_search (same-day blind spot until midnight rollover)"
        )
    return EdgeCaseResult(
        case_id="same_day_truncation_blind_spot",
        group="window",
        passed=not findings,
        summary="Notes truncated from <today> should remain reachable via memory_search",
        findings=findings,
        metrics={"in_today": in_today, "search_hits": len(hits)},
    )


def _sim_today_elsewhere_overflow_blind_spot(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    facade.remember(
        "Hợp đồng Kestrel Early",
        "Mã hợp đồng KST-991 cần ký trước 17:00",
        chat="other-session-1",
        now=BASE_NOW - timedelta(hours=4),
    )
    for idx in range(ELSEWHERE_MAX_LINES + 4):
        facade.remember(
            f"Other session note {idx}",
            f"Background item {idx}",
            chat=f"other-session-{idx + 2}",
            now=BASE_NOW - timedelta(minutes=30 - idx),
        )
    active = ActiveMemory(root, timezone_name=TZ_NAME)
    state = active.open_session(BASE_NOW)
    snap = active.refresh(state, BASE_NOW, conversation_id="dm-here")
    in_elsewhere = "Kestrel Early" in snap.today_elsewhere
    search_hits = facade.search("Kestrel KST-991", now=BASE_NOW).hits
    recent_hits = facade.recent(limit=10, now=BASE_NOW)
    in_recent = any("KST-991" in h.snippet or "Kestrel" in h.heading for h in recent_hits)

    findings: list[str] = []
    if not in_elsewhere and not search_hits and not in_recent:
        findings.append(
            "evicted <today_elsewhere> entry (>25 lines) is unreachable via "
            "<today_elsewhere>, memory_search, and memory_recent"
        )
    return EdgeCaseResult(
        case_id="today_elsewhere_overflow_blind_spot",
        group="window",
        passed=not findings,
        summary="Evicted <today_elsewhere> lines (>25) should remain retrievable before midnight",
        findings=findings,
        metrics={
            "in_elsewhere": in_elsewhere,
            "search_hits": len(search_hits),
            "in_recent": in_recent,
        },
    )


def _sim_prompt_rules_contract_audit(_root: Path) -> EdgeCaseResult:
    pm = PromptManager()
    rules = pm.rules_section()
    rules_low = rules.casefold()
    soul = pm.template("soul")
    combined_low = f"{soul}\n{rules}".casefold()

    findings: list[str] = []
    if "open_tasks" not in rules_low:
        findings.append("<rules> does not distinguish <open_tasks> from <memory_tasks>")
    if "memory_tasks" in rules_low and "never assume" not in rules_low.split("memory_tasks", 1)[1]:
        findings.append(
            "<rules> lacks a topic-switch / background guardrail on <memory_tasks> "
            "(unlike <today_elsewhere>)"
        )
    if "reopen" not in combined_low and "closed" not in combined_low and "done" not in rules_low.split("memory_tasks", 1)[-1]:
        findings.append(
            "<rules> gives no guidance on returning to closed (done/cancelled) or older tasks"
        )
    elif (
        "memory_tasks" in rules_low
        and 'scope="tasks"' not in rules_low
        and "call memory_tasks" not in rules_low
    ):
        findings.append(
            "<rules> does not instruct agent to query older/closed tasks "
            "missing from <memory_tasks>"
        )
    if "user.md" in combined_low and "habit" not in combined_low:
        findings.append(
            "SOUL.md/rules.md direct preferences to USER.md without mentioning kind='habit' "
            "or explaining when to use habit vs direct USER.md edit"
        )
    return EdgeCaseResult(
        case_id="prompt_rules_contract_audit",
        group="prompt",
        passed=not findings,
        summary="System prompt rules should disambiguate tasks, topic switching, return flow, and habits",
        findings=findings,
        metrics={"rules_chars": len(rules), "soul_chars": len(soul)},
    )


EDGE_SIMULATIONS: tuple[tuple[str, str, Callable[[Path], EdgeCaseResult]], ...] = (
    ("task_update_via_memory_update_staleness", "task", _sim_task_update_via_memory_update_staleness),
    ("task_update_checkpoint_recognition", "task", _sim_task_update_checkpoint_recognition),
    ("task_inplace_decision_overwrite_risk", "task", _sim_task_inplace_decision_overwrite_risk),
    ("ghost_31_days_ttl_silent_deletion", "ghost", _sim_ghost_31_days_ttl_silent_deletion),
    ("ghost_zombie_tasks_prompt_bloat", "ghost", _sim_ghost_zombie_tasks_prompt_bloat),
    ("ghost_idle_sweep_15m_lifecycle", "ghost", _sim_ghost_idle_sweep_15m_lifecycle),
    ("window_priority_current_vs_other_chat", "window", _sim_window_priority_current_vs_other_chat),
    ("cross_window_task_update_aggregation", "window", _sim_cross_window_task_update_aggregation),
    ("dm_to_group_isolation_guard", "window", _sim_dm_to_group_isolation_guard),
    ("same_day_truncation_blind_spot", "window", _sim_same_day_truncation_blind_spot),
    ("today_elsewhere_overflow_blind_spot", "window", _sim_today_elsewhere_overflow_blind_spot),
    ("prompt_rules_contract_audit", "prompt", _sim_prompt_rules_contract_audit),
    *PACK_A_SIMULATIONS,
)


def run_all_edge_simulations(root: Path, *, group: str = "all") -> dict[str, Any]:
    cases: list[EdgeCaseResult] = []
    for case_id, case_group, runner in EDGE_SIMULATIONS:
        if group != "all" and case_group != group:
            continue
        case_dir = root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        cases.append(runner(case_dir))
    passed = sum(1 for c in cases if c.passed)
    failed = len(cases) - passed
    return {
        "group": group,
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "cases": [asdict(c) for c in cases],
    }


def format_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "## Offline Memory & Task Failure-Case Simulation Report",
        "",
        f"**{report['passed']}/{report['total']}** passed · **{report['failed']}** failed",
        "",
        "| Case | Group | Sev | Status | Summary |",
        "|------|-------|-----|--------|---------|",
    ]
    for c in report["cases"]:
        mark = "PASS" if c["passed"] else "FAIL"
        sev = c.get("severity", "P1")
        lines.append(f"| `{c['case_id']}` | {c['group']} | {sev} | {mark} | {c['summary']} |")
    lines.append("")
    failed_cases = [c for c in report["cases"] if not c["passed"]]
    if failed_cases:
        lines.append("### Confirmed Failure Findings")
        lines.append("")
        for c in failed_cases:
            lines.append(f"- **`{c['case_id']}`** ({c.get('severity', 'P1')})")
            lines.extend(f"  - {finding}" for finding in c["findings"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline memory/task failure-case simulations")
    parser.add_argument(
        "--group",
        choices=(
            "all",
            "task",
            "ghost",
            "window",
            "prompt",
            "security",
            "compaction",
            "ledger",
            "retrieval",
        ),
        default="all",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="thyca_mem_edge_") as tmp:
        report = run_all_edge_simulations(Path(tmp), group=args.group)
    print(format_markdown_report(report))
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
