"""Wave 1 P0 regression tests: group profile stripping, child-bot PathGuard, group flush isolation."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.bench.memory_edge_common import BASE_NOW, TZ_NAME
from tests.bench.memory_edge_pack_a import (
    _sim_child_bot_write_canonical_soul_user_identity,
    _sim_group_chat_leaks_auto_profile_block,
    _sim_idle_sweep_group_flush_leaks_dm_tasks_and_habits,
    _stub_config,
)
from thyca.app.background_jobs.service import BackgroundJobsDeps, BackgroundJobsService
from thyca.bots.bus import Bus
from thyca.bots.card import BotCard, BotMemoryPolicy
from thyca.bots.registry import BotRegistry
from thyca.bots.runtime import BotRuntime
from thyca.conversations.runtime import ConversationManager
from thyca.conversations.store.runtime import ensure_runtime_store
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message, ToolCall
from thyca.llms.llm_base import ChatReply
from thyca.memories.facade import MemoryFacade
from thyca.memories.habits import (
    AUTO_END,
    AUTO_START,
    PROFILE_AUTO_END,
    PROFILE_AUTO_START,
    strip_auto_habits,
)
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.tools.path_guard import PathDenied, PathGuard


def test_strip_auto_habits_removes_both_habits_and_profile_auto_blocks() -> None:
    text = (
        "# User\n"
        "- Manual preference\n\n"
        "## Habits\n"
        f"{AUTO_START}\n- Auto habit line\n{AUTO_END}\n\n"
        "## Profile\n"
        "- Manual profile bullet\n"
        f"{PROFILE_AUTO_START}\n- Auto promoted secret\n{PROFILE_AUTO_END}\n"
    )
    cleaned = strip_auto_habits(text)
    assert "Manual preference" in cleaned
    assert "Manual profile bullet" in cleaned
    assert "Auto habit line" not in cleaned
    assert "Auto promoted secret" not in cleaned


def test_strip_auto_habits_handles_unclosed_profile_auto_start() -> None:
    broken = f"# User\n- Manual\n{PROFILE_AUTO_START}\n- Leaked tail\n"
    cleaned = strip_auto_habits(broken)
    assert "Manual" in cleaned
    assert "Leaked tail" not in cleaned


def test_group_chat_strips_auto_profile_in_prompt_and_canonical_get(tmp_path: Path) -> None:
    res = _sim_group_chat_leaks_auto_profile_block(tmp_path)
    assert res.passed, res.findings


def test_child_bot_denied_write_to_root_canonical_files_but_thyca_and_dm_read_allowed(
    tmp_path: Path,
) -> None:
    res = _sim_child_bot_write_canonical_soul_user_identity(tmp_path)
    assert res.passed, res.findings

    guard = PathGuard(tmp_path)
    user_md = tmp_path / "USER.md"
    user_md.write_text("# User\n", encoding="utf-8")

    tok_child = bind_bot_id("worker-a")
    try:
        assert guard.deny_read(str(user_md)) == user_md.resolve()
        with pytest.raises(PathDenied):
            guard.deny_write(str(user_md))
    finally:
        reset_bot_id(tok_child)

    tok_thyca = bind_bot_id("thyca")
    try:
        assert guard.deny_write(str(user_md)) == user_md.resolve()
    finally:
        reset_bot_id(tok_thyca)


def test_group_idle_sweep_flush_does_not_leak_dm_tasks_or_habits(tmp_path: Path) -> None:
    res = _sim_idle_sweep_group_flush_leaks_dm_tasks_and_habits(tmp_path)
    assert res.passed, res.findings


def test_group_flush_dedupe_never_surfaces_dm_task_or_habit_in_conflict_round(
    tmp_path: Path,
) -> None:
    ensure_runtime_store(tmp_path)
    cfg = _stub_config(tmp_path)
    bots = BotRegistry(tmp_path)
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
    convs = ConversationManager(tmp_path / "sessions", timezone_name=TZ_NAME)
    dm = convs.ensure_dm("worker-a")
    group = convs.create()
    convs.set_bots(group.conversation_id, ["thyca", "worker-a"])

    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    tok = bind_bot_id("worker-a")
    try:
        dm_tid = facade.new_task_id()
        facade.remember(
            "Kế hoạch lương Q4 bí mật",
            "Mức thưởng DM riêng",
            task=dm_tid,
            kind="checkpoint",
            status="open",
            bots=["worker-a"],
            chat=dm.conversation_id,
            now=BASE_NOW - timedelta(days=1),
        )
        facade.record_habit_entry(
            "DM habit",
            "Bí mật DM",
            habit="báo cáo lương riêng tối thứ sáu",
            habit_explicit=True,
            bots=["worker-a"],
            chat=dm.conversation_id,
            now=BASE_NOW - timedelta(days=1),
        )
        facade.archive.reindex(BASE_NOW)
    finally:
        reset_bot_id(tok)

    runtime = BotRuntime(tmp_path, bots, Bus(tmp_path))
    group_window = WindowRef(
        window_id=group.conversation_id,
        channel_id="webui",
        kind="group",
        participants=("thyca", "worker-a"),
        visibility="public",
    )

    class OverlapGroupExtractor:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def chat(self, messages, tools=None):
            self.prompts.append(messages[-1].content or "")
            if len(self.prompts) == 1:
                return ChatReply(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="g1",
                            name="memory_task",
                            arguments={
                                "topic": "Kế hoạch lương Q4",
                                "summary": "Nhóm bàn ngân sách chung",
                            },
                        ),
                        ToolCall(
                            id="g2",
                            name="memory_note",
                            arguments={
                                "topic": "Thói quen",
                                "summary": "Gửi báo cáo tối thứ sáu",
                                "habit": "báo cáo tối thứ sáu",
                            },
                        ),
                    ],
                )
            return ChatReply(content="", tool_calls=[])

    extractor = OverlapGroupExtractor()

    class _NoRunning:
        @staticmethod
        def snapshot() -> set[str]:
            return set()

    deps = BackgroundJobsDeps(
        root=tmp_path,
        conversations=convs,
        bots=bots,
        runtime=runtime,
        turns=_NoRunning(),
        window_for=lambda _conv: group_window,
        current_cfg=lambda: cfg,
        construction_cfg=cfg,
        submit=lambda coro: coro,
        scheduled_turn=lambda _bot, _prompt: {"reply": "ok"},
        injected_connect=extractor,
        facade=facade,
    )
    svc = BackgroundJobsService(deps)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    messages = [
        Message(role="user", content="Nhóm mình chốt kế hoạch Q4 nhé.", ts=ts),
        Message(role="assistant", content="Đã rõ.", ts=ts),
    ]
    asyncio.run(svc._flush_bot_memory("worker-a", messages, cfg, room_id=group.conversation_id))
    assert len(extractor.prompts) == 1
    combined_prompts = "\n".join(extractor.prompts)
    assert dm_tid not in combined_prompts
    assert "Kế hoạch lương Q4 bí mật" not in combined_prompts
    assert "báo cáo lương riêng tối thứ sáu" not in combined_prompts
