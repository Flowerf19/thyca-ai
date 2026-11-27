"""Pack A offline deterministic failure-case simulations (17 P0/P1 regression cases)."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.bench.memory_edge_common import (
    BASE_NOW,
    TZ,
    TZ_NAME,
    EdgeCaseResult,
    StubExtractorConnect,
)
from thyca.agents.act import Act
from thyca.agents.assemble import Assemble
from thyca.agents.loop import AgentLoop
from thyca.agents.observe import Observe
from thyca.agents.think import ChatReply, Think
from thyca.app.background_jobs.service import BackgroundJobsDeps, BackgroundJobsService
from thyca.bots.bus import Bus
from thyca.bots.card import BotCard, BotMemoryPolicy
from thyca.bots.context import BotTurnContext, bind_bot_turn_context, reset_bot_turn_context
from thyca.bots.registry import SYSTEM_BOT_ID, BotRegistry
from thyca.bots.runtime import BotRuntime, _segment_checkpointed
from thyca.bots.timeline import CanonicalConversationStore, WorkspaceTimeline
from thyca.bots.workspace_context import WorkspaceContextBuilder
from thyca.configs import Config, LimitsCfg, ProviderEntry, load, save
from thyca.conversations.runtime import ConversationManager
from thyca.conversations.store.runtime import ensure_runtime_store
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message, ToolCall
from thyca.memories.active import ActiveMemory
from thyca.memories.facade import MemoryFacade
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.memories.profile import (
    PROFILE_AUTO_END,
    PROFILE_AUTO_START,
    update_profile_block,
)
from thyca.memories.visibility import MemoryView, bound_memory_view
from thyca.tools.conversation_tools import register_conversation_tools
from thyca.tools.gateway import ToolGateway
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.path_guard import PathDenied, PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def _stub_config(root: Path, *, context_tokens: int = 32000) -> Config:
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
            limits=LimitsCfg(contextTokens=context_tokens),
        ),
        cfg_path,
    )
    return load(cfg_path)


def _sim_group_chat_leaks_auto_profile_block(root: Path) -> EdgeCaseResult:
    ensure_runtime_store(root)
    convs = ConversationManager(root / "sessions", timezone_name=TZ_NAME)
    group = convs.create()
    convs.set_bots(group.conversation_id, ["thyca", "worker-a"])

    secret_profile = "Khám bệnh định kỳ sáng thứ Ba tại viện tim"
    (root / "USER.md").write_text(
        "# User\n\n"
        "## Profile\n"
        f"{PROFILE_AUTO_START}\n"
        f"- {secret_profile}\n"
        f"{PROFILE_AUTO_END}\n",
        encoding="utf-8",
    )
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    group_window = WindowRef(
        window_id=group.conversation_id,
        channel_id="webui",
        kind="group",
        participants=("thyca", "worker-a"),
        visibility="public",
    )
    view = MemoryView(window=group_window, bot_id="worker-a", thyca_dir=root)
    tok = bind_bot_id("worker-a")
    try:
        with bound_memory_view(view):
            active = ActiveMemory(root, timezone_name=TZ_NAME)
            state = active.open_session(BASE_NOW)
            snap = active.refresh(
                state, BASE_NOW, conversation_id=group.conversation_id, reader="worker-a"
            )
            hot = WorkspaceContextBuilder(root).build(
                snap, bot_id="worker-a", window=group_window
            )
            canonical_user = facade.get(memory_id="canonical#user", now=BASE_NOW)
    finally:
        reset_bot_id(tok)

    findings: list[str] = []
    if secret_profile in hot.user:
        findings.append(
            "auto <!-- profile:auto:start --> block leaked into group <user> prompt "
            "(strip_auto_habits only strips habits:auto)"
        )
    if secret_profile in canonical_user:
        findings.append(
            "auto <!-- profile:auto:start --> block leaked via memory_get('canonical#user') "
            "inside group view"
        )
    return EdgeCaseResult(
        case_id="group_chat_leaks_auto_profile_block",
        group="security",
        severity="P0",
        passed=not findings,
        summary="Auto-promoted ## Profile block must be stripped in group prompt and canonical#user",
        findings=findings,
        metrics={
            "leaked_in_prompt": secret_profile in hot.user,
            "leaked_in_get": secret_profile in canonical_user,
        },
    )


def _sim_child_bot_write_canonical_soul_user_identity(root: Path) -> EdgeCaseResult:
    guard = PathGuard(root)
    findings: list[str] = []
    allowed_targets: list[str] = []
    tok = bind_bot_id("worker-a")
    try:
        for filename in ("USER.md", "SOUL.md", "IDENTITY.md"):
            target = root / filename
            target.write_text(f"# {filename}\n", encoding="utf-8")
            try:
                guard.deny_write(str(target))
            except PathDenied:
                continue
            allowed_targets.append(filename)
            findings.append(
                f"child bot 'worker-a' was NOT blocked by PathGuard.deny_write on root {filename}"
            )
    finally:
        reset_bot_id(tok)
    return EdgeCaseResult(
        case_id="child_bot_write_canonical_soul_user_identity",
        group="security",
        severity="P0",
        passed=not findings,
        summary="Child bots must be denied write/edit access to shared root USER/SOUL/IDENTITY.md",
        findings=findings,
        metrics={"allowed_canonical_writes": allowed_targets},
    )


def _sim_denied_origins_bypassed_by_allow_own_cross_window(root: Path) -> EdgeCaseResult:
    ensure_runtime_store(root)
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    secret_own = "SECRET_ROOM_ALPHA_OWN_888"
    secret_team = "SECRET_ROOM_ALPHA_TEAM_999"
    secret_other = "SECRET_ROOM_ALPHA_OTHER_777"
    past_day = BASE_NOW - timedelta(days=2)

    tid_own = facade.new_task_id()
    facade.remember(
        "Restricted room own note",
        secret_own,
        task=tid_own,
        kind="checkpoint",
        status="open",
        bots=["worker-a"],
        chat="room-secret",
        now=past_day,
    )
    tid_team = facade.new_task_id()
    sid_team = facade.remember(
        "Restricted room team note",
        secret_team,
        task=tid_team,
        kind="checkpoint",
        status="open",
        bots=["worker-a", "team"],
        chat="room-secret",
        now=past_day,
    )
    facade.remember(
        "Restricted room other bot note",
        secret_other,
        bots=["worker-b"],
        chat="room-secret",
        now=past_day,
    )
    facade.archive.reindex(BASE_NOW)

    tok = bind_bot_id("worker-a")
    try:
        view = MemoryView(
            window=None,
            bot_id="worker-a",
            thyca_dir=root,
            denied_origins=frozenset({"room-secret"}),
        )
        with bound_memory_view(view):
            auto_tasks = facade.tasks(status="open", now=BASE_NOW, allow_own_cross_window=False)[
                "tasks"
            ]
            auto_search = facade.search(
                secret_own, now=BASE_NOW, allow_own_cross_window=False
            ).hits
            team_hits = facade.search(
                secret_team, now=BASE_NOW, allow_own_cross_window=True
            ).hits
            other_hits = facade.search(
                secret_other, now=BASE_NOW, allow_own_cross_window=True
            ).hits
            tool_tasks = facade.tasks(status="open", now=BASE_NOW, allow_own_cross_window=True)[
                "tasks"
            ]
            try:
                got_team = facade.get(memory_id=sid_team, now=BASE_NOW, allow_own_cross_window=True)
            except Exception:
                got_team = ""
    finally:
        reset_bot_id(tok)

    findings: list[str] = []
    if auto_tasks:
        findings.append("denied_origins leaked into automatic tasks(allow_own_cross_window=False)")
    if auto_search:
        findings.append("denied_origins leaked into automatic search(allow_own_cross_window=False)")
    if team_hits or secret_team in got_team or any(t["task_id"] == tid_team for t in tool_tasks):
        findings.append("team/mixed denied_origins entry leaked under allow_own_cross_window=True")
    if other_hits:
        findings.append("other-bot denied_origins entry leaked under allow_own_cross_window=True")
    return EdgeCaseResult(
        case_id="denied_origins_bypassed_by_allow_own_cross_window",
        group="security",
        severity="P0",
        passed=not findings,
        summary="denied_origins blocks all auto-reads and blocks team/other-bot tool reads (Slice 1)",
        findings=findings,
        metrics={
            "auto_tasks_leaked": len(auto_tasks),
            "auto_search_leaked": len(auto_search),
            "team_hits_leaked": len(team_hits),
            "other_hits_leaked": len(other_hits),
        },
    )


def _sim_idle_sweep_group_flush_leaks_dm_tasks_and_habits(root: Path) -> EdgeCaseResult:
    ensure_runtime_store(root)
    cfg = _stub_config(root)
    bots = BotRegistry(root)
    bots.ensure_thyca()
    bots.create(
        BotCard(
            bot_id="worker-a",
            name="Worker A",
            role="Engineer",
            description="Backend helper",
            memory=BotMemoryPolicy(read=True, write=True),
        )
    )
    convs = ConversationManager(root / "sessions", timezone_name=TZ_NAME)
    dm = convs.ensure_dm("worker-a")
    group = convs.create()
    convs.set_bots(group.conversation_id, ["thyca", "worker-a"])

    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    dm_secret_task = "DM_SECRET_TASK_NEGOTIATION"
    dm_secret_habit = "dm_secret_habit_late_night"
    tok = bind_bot_id("worker-a")
    try:
        tid = facade.new_task_id()
        facade.remember(
            dm_secret_task,
            "Private DM salary review task",
            task=tid,
            kind="checkpoint",
            status="open",
            bots=["worker-a"],
            chat=dm.conversation_id,
            now=BASE_NOW - timedelta(days=1),
        )
        facade.record_habit_entry(
            "Private habit",
            "Works late on confidential DM topic",
            habit=dm_secret_habit,
            habit_explicit=True,
            bots=["worker-a"],
            chat=dm.conversation_id,
            now=BASE_NOW - timedelta(days=1),
        )
        facade.archive.reindex(BASE_NOW)
    finally:
        reset_bot_id(tok)

    runtime = BotRuntime(root, bots, Bus(root))
    group_window = WindowRef(
        window_id=group.conversation_id,
        channel_id="webui",
        kind="group",
        participants=("thyca", "worker-a"),
        visibility="public",
    )
    internal_id = "2026-11-25T09-00-00_c0de"
    runtime.workspaces.workspace("worker-a").bind_window(
        group_window, internal_conversation_id=internal_id
    )
    canonical = ConversationManager(
        limits=cfg.effective_limits(),
        timezone_name=TZ_NAME,
        store=runtime._canonical_store("worker-a", group_window, internal_id),
    )
    canonical.load(internal_id)
    old_ts = (datetime.now(UTC) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    canonical.append(Message(role="user", content="Trong nhóm này ta chốt lịch deploy thứ Năm.", ts=old_ts))
    canonical.append(Message(role="assistant", content="Đã ghi nhận lịch deploy thứ Năm.", ts=old_ts))

    stub = StubExtractorConnect([])

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
            window_for=lambda _conv: group_window,
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
        svc.sweep_idle_conversations(idle_s=900)
    finally:
        loop.close()

    prompt_sent = "\n".join(stub.prompts)
    findings: list[str] = []
    if dm_secret_task in prompt_sent:
        findings.append(
            "group idle sweep extractor prompt received private DM open task "
            "(_flush_bot_memory runs without bound MemoryView)"
        )
    if dm_secret_habit in prompt_sent:
        findings.append(
            "group idle sweep extractor prompt received private DM habit label "
            "(_flush_bot_memory runs without bound MemoryView)"
        )
    return EdgeCaseResult(
        case_id="idle_sweep_group_flush_leaks_dm_tasks_and_habits",
        group="security",
        severity="P0",
        passed=not findings,
        summary="Group idle-sweep extraction must not see private DM open tasks or habits",
        findings=findings,
        metrics={
            "extractor_calls": stub.calls,
            "dm_task_in_group_prompt": dm_secret_task in prompt_sent,
            "dm_habit_in_group_prompt": dm_secret_habit in prompt_sent,
        },
    )


def _sim_task_child_bot_close_bypass(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    live_now = datetime.now(TZ)

    tok_thyca = bind_bot_id(SYSTEM_BOT_ID)
    try:
        tid = facade.new_task_id()
        facade.remember(
            "Triển khai production",
            "Thyca mở task kiểm soát release",
            task=tid,
            kind="checkpoint",
            status="open",
            bots=["thyca"],
            now=live_now - timedelta(minutes=10),
        )
    finally:
        reset_bot_id(tok_thyca)

    tok_child = bind_bot_id("worker-a")
    try:
        res = asyncio.run(
            gateway.submit(
                ToolCall(
                    id="c1",
                    name="memory_task",
                    arguments={
                        "topic": "Triển khai production",
                        "summary": "Worker tự đóng task của Thyca",
                        "task": tid,
                        "status": "done",
                    },
                )
            )
        )
    finally:
        reset_bot_id(tok_child)

    tok_thyca2 = bind_bot_id(SYSTEM_BOT_ID)
    try:
        open_tasks = facade.tasks(status="open", now=live_now)["tasks"]
        done_tasks = facade.tasks(status="done", now=live_now)["tasks"]
    finally:
        reset_bot_id(tok_thyca2)

    findings: list[str] = []
    if not res.is_error and any(t["task_id"] == tid for t in done_tasks):
        findings.append(
            "child bot 'worker-a' bypassed _may_mutate and closed Thyca's task to status='done' "
            "via upsert_task_card fallback to facade.remember"
        )
    if not any(t["task_id"] == tid for t in open_tasks):
        findings.append("Thyca's task disappeared from open catalog after unauthorized child-bot close")
    return EdgeCaseResult(
        case_id="task_child_bot_close_bypass",
        group="task",
        severity="P0",
        passed=not findings,
        summary="Child bot must not be able to close another owner/Thyca task via memory_remember",
        findings=findings,
        metrics={"tool_is_error": res.is_error, "open_count": len(open_tasks), "done_count": len(done_tasks)},
    )


def _sim_task_closing_entry_expiry_resurrection(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    day1 = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    day2 = datetime(2026, 11, 2, 10, 0, tzinfo=TZ)
    day6 = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)

    tid = facade.new_task_id()
    sid1 = facade.remember(
        "Cấu hình SSL",
        "Mở đầu việc gia hạn cert",
        importance=4,
        task=tid,
        kind="checkpoint",
        status="open",
        chat="win-1",
        now=day1,
    )
    facade.upsert_task_card(
        "Cấu hình SSL",
        "Đã gia hạn xong SSL",
        importance=1,
        task=tid,
        status="done",
        chat="win-2",
        target_memory_id=sid1,
        now=day2,
    )
    open_day6 = facade.tasks(status="open", now=day6)["tasks"]
    findings: list[str] = []
    if any(t["task_id"] == tid for t in open_day6):
        findings.append(
            "closed task resurrected as status='open' on day 6 after its imp=1 closing entry expired"
        )
    return EdgeCaseResult(
        case_id="task_closing_entry_expiry_resurrection",
        group="task",
        severity="P0",
        passed=not findings,
        summary="A closed task must never resurrect as open when its closing entry expires earlier",
        findings=findings,
        metrics={"resurrected_open_on_day6": any(t["task_id"] == tid for t in open_day6)},
    )


def _sim_task_cross_file_last_line_tiebreak_inversion(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    day1 = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    day3 = datetime(2026, 11, 3, 10, 0, tzinfo=TZ)

    for idx in range(25):
        facade.remember(f"Filler {idx}", f"Padding line {idx}\n" + ("extra\n" * 3), now=day1)

    tid = facade.new_task_id()
    sid1 = facade.remember(
        "Tối ưu cache",
        "Bắt đầu ở cuối file day1",
        task=tid,
        kind="checkpoint",
        status="open",
        chat="win-1",
        now=day1,
    )
    facade.upsert_task_card(
        "Tối ưu cache",
        "Cập nhật in-place trong day1 vào ngày day3",
        task=tid,
        status="open",
        chat="win-1",
        target_memory_id=sid1,
        now=day3,
    )
    facade.upsert_task_card(
        "Tối ưu cache",
        "Đóng xong ở đầu file day3",
        task=tid,
        status="done",
        chat="win-2",
        target_memory_id=sid1,
        now=day3 + timedelta(hours=1),
    )

    open_d3 = facade.tasks(status="open", now=day3 + timedelta(hours=2))["tasks"]
    findings: list[str] = []
    if any(t["task_id"] == tid for t in open_d3):
        findings.append(
            "day1 open entry (high line_start + updated: day3) beat newer day3 done entry "
            "(low line_start) due to cross-file (last_day, last_line) tie-break"
        )
    return EdgeCaseResult(
        case_id="task_cross_file_last_line_tiebreak_inversion",
        group="task",
        severity="P1",
        passed=not findings,
        summary="Newer day closing entry must beat older day entry updated in-place at higher line number",
        findings=findings,
        metrics={"still_open": any(t["task_id"] == tid for t in open_d3)},
    )


def _sim_task_upsert_over_50_tasks_duplicate(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    day0 = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    oldest_tid = facade.new_task_id()
    facade.remember(
        "Oldest tracked task",
        "Step 1 initial",
        task=oldest_tid,
        kind="checkpoint",
        status="open",
        chat="dm-thyca",
        now=day0,
    )
    for idx in range(52):
        tid = facade.new_task_id()
        facade.remember(
            f"Newer task {idx}",
            f"Status {idx}",
            task=tid,
            kind="checkpoint",
            status="open",
            chat="dm-thyca",
            now=day0 + timedelta(days=1 + (idx // 5), minutes=idx),
        )
    facade.upsert_task_card(
        "Oldest tracked task",
        "Step 2 progress without explicit target_memory_id",
        task=oldest_tid,
        chat="dm-thyca",
        now=BASE_NOW,
    )
    rows = facade.tasks(status="open", limit=50, now=BASE_NOW)["tasks"]
    matched = [t for t in rows if t["task_id"] == oldest_tid]
    entry_count = matched[0]["entry_count"] if matched else 0
    findings: list[str] = []
    if entry_count != 1:
        findings.append(
            f"upsert_task_card missed {oldest_tid} beyond tasks(limit=50) cap and minted duplicate "
            f"(entry_count={entry_count}, expected 1)"
        )
    return EdgeCaseResult(
        case_id="task_upsert_over_50_tasks_duplicate",
        group="task",
        severity="P1",
        passed=not findings,
        summary="upsert_task_card should locate task cards even when total tasks exceed 50",
        findings=findings,
        metrics={"entry_count": entry_count},
    )


def _sim_compaction_skips_memory_flush_on_stale_checkpoint(_root: Path) -> EdgeCaseResult:
    messages = [
        Message(role="user", content="Bắt đầu làm task lớn."),
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="cp1",
                    name="memory_task",
                    arguments={
                        "topic": "Refactor auth",
                        "summary": "Khởi tạo bước 1",
                    },
                )
            ],
            meta={"estimate_tokens": 30_000},
        ),
        Message(
            role="tool",
            content='{"memory_id":"2026-11-25#11112222","task_id":"task-11112222"}',
            tool_call_id="cp1",
        ),
        Message(role="user", content="Thêm 50k tokens quyết định kiến trúc mới chưa checkpoint."),
    ]
    skip_flush = _segment_checkpointed(messages, context_tokens=100_000)
    findings: list[str] = []
    if skip_flush:
        findings.append(
            "_segment_checkpointed returned True for a stale 30%-band checkpoint at 95% compaction "
            "(passes current_ratio=0.0, skipping memory_flush and losing new post-checkpoint work)"
        )
    return EdgeCaseResult(
        case_id="compaction_skips_memory_flush_on_stale_checkpoint",
        group="compaction",
        severity="P0",
        passed=not findings,
        summary="Stale early checkpoint must NOT skip pre-compaction memory_flush at 95% usage",
        findings=findings,
        metrics={"segment_checkpointed_skipped_flush": skip_flush},
    )


def _sim_midturn_compact_tool_does_not_shrink_stage(root: Path) -> EdgeCaseResult:
    ensure_runtime_store(root)
    timeline = WorkspaceTimeline(root, SYSTEM_BOT_ID)
    window = WindowRef(
        window_id="thyca",
        channel_id="webui",
        kind="dm",
        participants=(SYSTEM_BOT_ID,),
    )
    conversation_id = "2026-11-25T10-00-00_abcd"
    store = CanonicalConversationStore(
        root / "bots" / SYSTEM_BOT_ID / "sessions",
        timeline=timeline,
        window=window,
        conversation_id=conversation_id,
    )
    limits = LimitsCfg(contextTokens=32000)
    manager = ConversationManager(limits=limits, timezone_name=TZ_NAME, store=store)
    manager.load(conversation_id)
    for idx in range(22):
        manager.append(Message(role="user", content=f"Turn {idx} " + ("dữ liệu lịch sử dài " * 115)))
        manager.append(Message(role="assistant", content=f"Reply {idx} " + ("kết quả phân tích " * 115)))

    registry = ToolRegistry()
    register_conversation_tools(registry)
    gateway = ToolGateway(registry, TaskStore())

    class _CompactScriptConnect:
        def __init__(self) -> None:
            self.round_prompt_sizes: list[int] = []
            self.round_has_compaction_marker: list[bool] = []

        async def chat(self, messages: list[Message], _tools: list | None = None) -> ChatReply:
            self.round_prompt_sizes.append(sum(len(str(m.content or "")) for m in messages))
            self.round_has_compaction_marker.append(
                any(
                    m.role == "system" and str(m.content or "").startswith("[compaction: ")
                    for m in messages
                )
            )
            if len(self.round_prompt_sizes) == 1:
                return ChatReply(
                    content=None,
                    tool_calls=[ToolCall(id="cmp1", name="compact", arguments={})],
                )
            return ChatReply(content="Tiếp tục sau khi compact thành công.")

    connect = _CompactScriptConnect()
    loop = AgentLoop(
        conversations=manager,
        assemble=Assemble(),
        think=Think(connect),
        act=Act(gateway),
        observe=Observe(manager),
        loop_max=4,
        tools=registry.to_openai_schema(),
        model="stub-model",
        context_tokens=32000,
    )

    async def _do_compact(*, drop_prior: bool = False) -> dict:
        return manager.compact_manual(drop_prior=drop_prior)

    tok = bind_bot_turn_context(
        BotTurnContext(sender_id=SYSTEM_BOT_ID, room_id="thyca", compact=_do_compact)
    )
    try:
        reply = asyncio.run(loop.run("Hãy compact ngay để lấy chỗ rồi trả lời tiếp."))
    finally:
        reset_bot_turn_context(tok)

    shrank = (
        len(connect.round_prompt_sizes) >= 2
        and connect.round_prompt_sizes[1] < connect.round_prompt_sizes[0]
        and connect.round_has_compaction_marker[1]
    )
    findings: list[str] = []
    if len(connect.round_prompt_sizes) < 2:
        findings.append(
            f"loop aborted before round 2 after mid-turn compact (reply={reply[:120]!r})"
        )
    elif not shrank:
        findings.append(
            "mid-turn compact tool shrank persisted store, but in-flight stage.messages was NOT "
            f"shrunk (round1={connect.round_prompt_sizes[0]} chars, "
            f"round2={connect.round_prompt_sizes[1]} chars, "
            f"marker_in_round2={connect.round_has_compaction_marker[1]})"
        )
    return EdgeCaseResult(
        case_id="midturn_compact_tool_does_not_shrink_stage",
        group="compaction",
        severity="P0",
        passed=not findings,
        summary="Calling compact tool mid-turn should shrink stage.messages for subsequent rounds",
        findings=findings,
        metrics={
            "round_prompt_chars": connect.round_prompt_sizes,
            "marker_in_rounds": connect.round_has_compaction_marker,
            "shrank": shrank,
        },
    )


def _sim_idle_sweep_compacts_after_silent_extractor_failure(root: Path) -> EdgeCaseResult:
    ensure_runtime_store(root)
    cfg = _stub_config(root, context_tokens=32000)
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
    internal_id = "2026-11-25T09-00-00_f00d"
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
                content=f"Lượt {idx}: quyết định quan trọng cần nhớ " + ("chi tiết kỹ thuật " * 45),
                ts=old_ts,
            )
        )
        canonical.append(
            Message(
                role="assistant",
                content=f"Xác nhận {idx}: " + ("phương án triển khai " * 45),
                ts=old_ts,
            )
        )

    failing_stub = StubExtractorConnect(raise_exc=RuntimeError("provider 503 timeout"))

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
            injected_connect=failing_stub,
            facade=facade,
        )
        svc = BackgroundJobsService(deps)
        svc._profile_last_run = 10**12
        svc._profile_signatures = {}
        report1 = svc.sweep_idle_conversations(idle_s=900)
        report2 = svc.sweep_idle_conversations(idle_s=900)
    finally:
        loop.close()

    first_entry = next(iter(report1["bots"].values()), {})
    compacted_despite_error = bool(first_entry.get("compacted"))
    findings: list[str] = []
    if compacted_despite_error:
        findings.append(
            "idle sweep ran compact_manual() and discarded transcript even though extractor raised "
            "RuntimeError (extract_from_transcript swallowed error as [])"
        )
    if not report2["bots"]:
        findings.append(
            "idle sweep recorded _idle_swept watermark after silent extractor failure, preventing retry"
        )
    return EdgeCaseResult(
        case_id="idle_sweep_compacts_after_silent_extractor_failure",
        group="compaction",
        severity="P0",
        passed=not findings,
        summary="Idle sweep must not compact or mark watermark when memory extraction fails",
        findings=findings,
        metrics={
            "compacted_despite_error": compacted_despite_error,
            "second_sweep_retried": bool(report2["bots"]),
            "extractor_calls": failing_stub.calls,
        },
    )


def _sim_other_fuzzy_dedupe_drops_negation_flip(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    sid1 = facade.append_other_journal(
        "Docker", "Mình đang dùng Docker trên máy dev cá nhân", chat="dm-1", now=BASE_NOW
    )
    facade.append_other_journal(
        "Docker",
        "Mình bỏ dùng Docker trên máy dev cá nhân",
        chat="dm-1",
        now=BASE_NOW + timedelta(minutes=5),
    )
    sid2 = facade.append_other_journal(
        "Deploy",
        "Đã bật auto deploy trên môi trường staging",
        chat="dm-1",
        now=BASE_NOW + timedelta(minutes=10),
    )
    facade.append_other_journal(
        "Deploy",
        "Đã tắt auto deploy trên môi trường staging",
        chat="dm-1",
        now=BASE_NOW + timedelta(minutes=15),
    )
    body_docker = facade.get(memory_id=sid1, now=BASE_NOW + timedelta(minutes=20)) if sid1 else ""
    body_deploy = facade.get(memory_id=sid2, now=BASE_NOW + timedelta(minutes=20)) if sid2 else ""
    findings: list[str] = []
    if "bỏ dùng" not in body_docker.casefold():
        findings.append(
            "Other fuzzy dedupe swallowed negation flip 'Mình bỏ dùng Docker...' as duplicate "
            "(_POLARITY_TOKENS misses 'bỏ')"
        )
    if "tắt auto deploy" not in body_deploy.casefold():
        findings.append(
            "Other fuzzy dedupe swallowed state flip 'Đã tắt auto deploy...' as duplicate "
            "(_POLARITY_TOKENS misses 'bật'/'tắt')"
        )
    return EdgeCaseResult(
        case_id="other_fuzzy_dedupe_drops_negation_flip",
        group="ledger",
        severity="P0",
        passed=not findings,
        summary="Other-journal fuzzy dedupe must not swallow Vietnamese reversal/toggle updates",
        findings=findings,
        metrics={
            "has_bo_dung": "bỏ dùng" in body_docker.casefold(),
            "has_tat_deploy": "tắt auto deploy" in body_deploy.casefold(),
        },
    )


def _sim_other_single_heading_forget_collateral_damage(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    sid1 = facade.append_other_journal("Staging IP", "Server staging ở IP 10.42.7.19", chat="dm-1", now=BASE_NOW)
    sid2 = facade.append_other_journal("Lịch họp", "Họp sprint lúc 14:00 thứ Tư", chat="dm-1", now=BASE_NOW + timedelta(minutes=1))
    sid3 = facade.append_other_journal("Máy in", "Mã máy in tầng 3 là PRN-09", chat="dm-1", now=BASE_NOW + timedelta(minutes=2))
    assert sid3 is not None
    facade.forget(sid3, now=BASE_NOW + timedelta(minutes=3))
    remaining = facade.today_entries(chat="dm-1", now=BASE_NOW + timedelta(minutes=4))
    all_text = "\n".join(e["body"] for e in remaining)

    findings: list[str] = []
    if "10.42.7.19" not in all_text or "14:00" not in all_text:
        findings.append(
            "memory_forget on 1 Other fact wiped ALL unrelated Other facts of the day "
            f"(same shared memory_id={sid1 == sid2 == sid3})"
        )
    return EdgeCaseResult(
        case_id="other_single_heading_forget_collateral_damage",
        group="ledger",
        severity="P0",
        passed=not findings,
        summary="Forgetting one Other fact must not destroy unrelated facts recorded the same day",
        findings=findings,
        metrics={"shared_memory_id": sid1 == sid2 == sid3, "remaining_entries": len(remaining)},
    )


def _sim_profile_manual_heading_corrupts_markers(root: Path) -> EdgeCaseResult:
    user_md = root / "USER.md"
    user_md.write_text("# User\n\n## Profile\n- Ghi chú tay của user\n", encoding="utf-8")
    update_profile_block(user_md, ["Thích trả lời ngắn gọn", "Dùng Fedora Bazzite"])
    update_profile_block(user_md, ["Thích trả lời ngắn gọn", "Dùng Fedora Bazzite"])
    final_text = user_md.read_text(encoding="utf-8")

    findings: list[str] = []
    if PROFILE_AUTO_START not in final_text:
        findings.append("update_profile_block failed to insert PROFILE_AUTO_START when manual ## Profile existed")
    if final_text.count(PROFILE_AUTO_END) != 1:
        findings.append(f"corrupted PROFILE_AUTO_END count={final_text.count(PROFILE_AUTO_END)} (expected 1)")
    if final_text.count("Thích trả lời ngắn gọn") != 1:
        findings.append(
            f"duplicate auto profile line after 2nd idempotent update "
            f"(count={final_text.count('Thích trả lời ngắn gọn')})"
        )
    if "Ghi chú tay của user" not in final_text:
        findings.append("manual user bullet under ## Profile was lost")
    return EdgeCaseResult(
        case_id="profile_manual_heading_corrupts_markers",
        group="ledger",
        severity="P1",
        passed=not findings,
        summary="update_profile_block must stay valid and idempotent when manual ## Profile exists",
        findings=findings,
        metrics={
            "has_start_marker": PROFILE_AUTO_START in final_text,
            "end_marker_count": final_text.count(PROFILE_AUTO_END),
            "item_count": final_text.count("Thích trả lời ngắn gọn"),
        },
    )


def _sim_trigram_search_fails_single_word_typo(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    past = BASE_NOW - timedelta(days=2)
    facade.remember("Database", "Cụm staging chạy PostgreSQL 16 trên Kubernetes", now=past)
    facade.archive.reindex(BASE_NOW)

    hits_pg = facade.search("Postrges", now=BASE_NOW).hits
    hits_k8s = facade.search("Kubernets", now=BASE_NOW).hits
    findings: list[str] = []
    if not hits_pg:
        findings.append(
            "single-word typo 'Postrges' returned 0 hits (trigram_search pre-filter requires exact substring)"
        )
    if not hits_k8s:
        findings.append(
            "single-word typo 'Kubernets' returned 0 hits (trigram_search pre-filter requires exact substring)"
        )
    return EdgeCaseResult(
        case_id="trigram_search_fails_single_word_typo",
        group="retrieval",
        severity="P0",
        passed=not findings,
        summary="Trigram typo fallback should match single-word 1-char typos like Postrges/Kubernets",
        findings=findings,
        metrics={"hits_postrges": len(hits_pg), "hits_kubernets": len(hits_k8s)},
    )


def _sim_fts_vs_trigram_unnormalized_score_inversion(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    past = BASE_NOW - timedelta(days=2)
    exact_sid = facade.remember(
        "Triển khai Redis",
        "Kế hoạch triển khai Redis cluster trên staging với 3 node",
        now=past,
    )
    facade.remember(
        "Ghi chú rời",
        "Đang trển khia Redsi trên server kiểm thử",
        now=past + timedelta(minutes=1),
    )
    facade.remember(
        "Báo cáo khác",
        "Lịch trình trển khia Redsi giao diện mới",
        now=past + timedelta(minutes=2),
    )
    facade.archive.reindex(BASE_NOW)

    result = facade.search("triển khai redis", limit=5, now=BASE_NOW)
    top_id = result.hits[0].memory_id if result.hits else None
    top_source = result.hits[0].match_type if result.hits else None
    has_trigram = any(h.match_type == "trigram" for h in result.hits)
    findings: list[str] = []
    if not has_trigram:
        findings.append(
            "test fixture did not produce any trigram fallback hits to verify FTS-vs-trigram merge"
        )
    if top_id != exact_sid:
        findings.append(
            f"exact FTS match ({exact_sid}) lost rank #1 to weaker {top_source!r} hit ({top_id}) "
            f"due to unnormalized BM25 vs RapidFuzz score merge: "
            f"{[(h.memory_id, h.match_type, round(h.score, 2)) for h in result.hits]}"
        )
    return EdgeCaseResult(
        case_id="fts_vs_trigram_unnormalized_score_inversion",
        group="retrieval",
        severity="P1",
        passed=not findings,
        summary="Exact FTS match must outrank weaker trigram fallback hits when merged",
        findings=findings,
        metrics={
            "expected_top": exact_sid,
            "actual_top": top_id,
            "has_trigram": has_trigram,
            "ranked": [(h.memory_id, h.match_type, round(h.score, 2)) for h in result.hits],
        },
    )


def _sim_purge_expired_rewrites_every_daily_file_unconditionally(root: Path) -> EdgeCaseResult:
    facade = MemoryFacade(root, timezone_name=TZ_NAME)
    past_files: list[Path] = []
    for offset in range(1, 6):
        day = BASE_NOW - timedelta(days=offset)
        facade.remember(f"Archive {offset}", f"Durable note day -{offset}", importance=4, now=day)
        past_files.append(root / "memory" / f"{day.strftime('%Y-%m-%d')}.md")
    facade.archive.reindex(BASE_NOW)
    mtimes_before = {p.name: p.stat().st_mtime_ns for p in past_files}

    facade.remember("Today note", "Fresh note written today", now=BASE_NOW)
    rewritten = [p.name for p in past_files if p.stat().st_mtime_ns != mtimes_before[p.name]]

    findings: list[str] = []
    if rewritten:
        findings.append(
            f"purge_expired unconditionally rewrote {len(rewritten)}/{len(past_files)} untouched "
            "archived daily files (churning mtime_ns and forcing full archive reindex)"
        )
    return EdgeCaseResult(
        case_id="purge_expired_rewrites_every_daily_file_unconditionally",
        group="retrieval",
        severity="P1",
        passed=not findings,
        summary="purge_expired should only rewrite daily files that actually have expired blocks",
        findings=findings,
        metrics={"checked_files": len(past_files), "rewritten_files": len(rewritten)},
    )


PACK_A_SIMULATIONS: tuple[tuple[str, str, Callable[[Path], EdgeCaseResult]], ...] = (
    ("group_chat_leaks_auto_profile_block", "security", _sim_group_chat_leaks_auto_profile_block),
    (
        "child_bot_write_canonical_soul_user_identity",
        "security",
        _sim_child_bot_write_canonical_soul_user_identity,
    ),
    (
        "denied_origins_bypassed_by_allow_own_cross_window",
        "security",
        _sim_denied_origins_bypassed_by_allow_own_cross_window,
    ),
    (
        "idle_sweep_group_flush_leaks_dm_tasks_and_habits",
        "security",
        _sim_idle_sweep_group_flush_leaks_dm_tasks_and_habits,
    ),
    ("task_child_bot_close_bypass", "task", _sim_task_child_bot_close_bypass),
    (
        "task_closing_entry_expiry_resurrection",
        "task",
        _sim_task_closing_entry_expiry_resurrection,
    ),
    (
        "task_cross_file_last_line_tiebreak_inversion",
        "task",
        _sim_task_cross_file_last_line_tiebreak_inversion,
    ),
    ("task_upsert_over_50_tasks_duplicate", "task", _sim_task_upsert_over_50_tasks_duplicate),
    (
        "compaction_skips_memory_flush_on_stale_checkpoint",
        "compaction",
        _sim_compaction_skips_memory_flush_on_stale_checkpoint,
    ),
    (
        "midturn_compact_tool_does_not_shrink_stage",
        "compaction",
        _sim_midturn_compact_tool_does_not_shrink_stage,
    ),
    (
        "idle_sweep_compacts_after_silent_extractor_failure",
        "compaction",
        _sim_idle_sweep_compacts_after_silent_extractor_failure,
    ),
    (
        "other_fuzzy_dedupe_drops_negation_flip",
        "ledger",
        _sim_other_fuzzy_dedupe_drops_negation_flip,
    ),
    (
        "other_single_heading_forget_collateral_damage",
        "ledger",
        _sim_other_single_heading_forget_collateral_damage,
    ),
    (
        "profile_manual_heading_corrupts_markers",
        "ledger",
        _sim_profile_manual_heading_corrupts_markers,
    ),
    (
        "trigram_search_fails_single_word_typo",
        "retrieval",
        _sim_trigram_search_fails_single_word_typo,
    ),
    (
        "fts_vs_trigram_unnormalized_score_inversion",
        "retrieval",
        _sim_fts_vs_trigram_unnormalized_score_inversion,
    ),
    (
        "purge_expired_rewrites_every_daily_file_unconditionally",
        "retrieval",
        _sim_purge_expired_rewrites_every_daily_file_unconditionally,
    ),
)
