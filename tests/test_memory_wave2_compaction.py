"""Wave 2 P0 regression tests: stale checkpoint flush, mid-turn compact, idle extractor error."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.bench.memory_edge_common import TZ_NAME, StubExtractorConnect
from tests.bench.memory_edge_pack_a import (
    _sim_compaction_skips_memory_flush_on_stale_checkpoint,
    _sim_idle_sweep_compacts_after_silent_extractor_failure,
    _sim_midturn_compact_tool_does_not_shrink_stage,
    _stub_config,
)
from thyca.app.background_jobs.service import BackgroundJobsDeps, BackgroundJobsService
from thyca.bots.bus import Bus
from thyca.bots.registry import SYSTEM_BOT_ID, BotRegistry
from thyca.bots.runtime import BotRuntime, _segment_checkpointed
from thyca.conversations.runtime import ConversationManager
from thyca.conversations.store.runtime import ensure_runtime_store
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message, ToolCall
from thyca.memories.facade import MemoryFacade
from thyca.memories.jobs import ExtractionError, extract_from_transcript, parse_extraction


def test_stale_checkpoint_does_not_skip_pre_compaction_flush(tmp_path: Path) -> None:
    res = _sim_compaction_skips_memory_flush_on_stale_checkpoint(tmp_path)
    assert res.passed, res.findings


def test_fresh_backstop_checkpoint_still_skips_flush() -> None:
    messages = [
        Message(role="user", content="Final step before compact"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="cp_fresh",
                    name="memory_task",
                    arguments={
                        "topic": "Refactor auth",
                        "summary": "Up-to-date checkpoint",
                        "task": "task-11112222",
                    },
                )
            ],
            meta={"estimate_tokens": 96_000},
        ),
        Message(
            role="tool",
            content='{"memory_id":"2026-11-25#11112222","task_id":"task-11112222"}',
            tool_call_id="cp_fresh",
        ),
    ]
    assert _segment_checkpointed(messages, context_tokens=100_000) is True


def test_unmetered_tail_memory_task_status_checkpoint_matches_checkpoint_state() -> None:
    messages = [
        Message(role="user", content="Cập nhật tiến độ task"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="u_tail",
                    name="memory_task",
                    arguments={
                        "task": "task-11112222",
                        "summary": "Đang xử lý bước 2",
                        "status": "open",
                    },
                )
            ],
        ),
        Message(role="tool", content='{"memory_id":"2026-11-25#11112222","task_id":"task-11112222"}', tool_call_id="u_tail"),
    ]
    assert _segment_checkpointed(messages, context_tokens=100_000) is True


def test_midturn_compact_tool_shrinks_in_flight_stage_messages(tmp_path: Path) -> None:
    res = _sim_midturn_compact_tool_does_not_shrink_stage(tmp_path)
    assert res.passed, res.findings


def test_idle_sweep_aborts_compaction_on_extractor_exception(tmp_path: Path) -> None:
    res = _sim_idle_sweep_compacts_after_silent_extractor_failure(tmp_path)
    assert res.passed, res.findings


def test_extract_strict_raises_on_malformed_json_and_idle_sweep_retries(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError):
        parse_extraction("not valid json", strict=True)
    with pytest.raises(ExtractionError):
        parse_extraction('{"not": "a list"}', strict=True)
    assert parse_extraction("[]", strict=True) == []

    bad_stub = StubExtractorConnect("broken json {")
    with pytest.raises(ExtractionError):
        asyncio.run(extract_from_transcript(bad_stub, "m", "hello", strict=True))

    ensure_runtime_store(tmp_path)
    cfg = _stub_config(tmp_path, context_tokens=32000)
    bots = BotRegistry(tmp_path)
    bots.ensure_thyca()
    convs = ConversationManager(tmp_path / "sessions", timezone_name=TZ_NAME)
    dm = convs.ensure_dm(SYSTEM_BOT_ID)
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    runtime = BotRuntime(tmp_path, bots, Bus(tmp_path))
    window = WindowRef(
        window_id=dm.conversation_id,
        channel_id="webui",
        kind="dm",
        participants=(SYSTEM_BOT_ID,),
    )
    internal_id = "2026-11-25T09-00-00_beef"
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
        canonical.append(Message(role="user", content=f"U{idx} " + ("x " * 1100), ts=old_ts))
        canonical.append(Message(role="assistant", content=f"A{idx} " + ("y " * 1100), ts=old_ts))

    class _NoTurns:
        @staticmethod
        def snapshot() -> set[str]:
            return set()

    loop = asyncio.new_event_loop()
    try:
        deps = BackgroundJobsDeps(
            root=tmp_path,
            conversations=convs,
            bots=bots,
            runtime=runtime,
            turns=_NoTurns(),
            window_for=lambda _conv: window,
            current_cfg=lambda: cfg,
            construction_cfg=cfg,
            submit=loop.run_until_complete,
            scheduled_turn=lambda _bot, _prompt: {"reply": "ok"},
            injected_connect=StubExtractorConnect("not json at all"),
            facade=facade,
        )
        svc = BackgroundJobsService(deps)
        svc._profile_last_run = 10**12
        svc._profile_signatures = {}
        r1 = svc.sweep_idle_conversations(idle_s=900)
        entry1 = next(iter(r1["bots"].values()))
        assert entry1.get("error") == "ExtractionError"
        assert not entry1.get("compacted")

        deps.injected_connect = StubExtractorConnect([])
        r2 = svc.sweep_idle_conversations(idle_s=900)
        entry2 = next(iter(r2["bots"].values()))
        assert "error" not in entry2
        assert entry2.get("compacted") is True
    finally:
        loop.close()
