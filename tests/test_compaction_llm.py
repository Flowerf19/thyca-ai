"""LLM compaction orchestration: summary landing, fallback, auto path."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from thyca.agents.act import Act
from thyca.agents.assemble import Assemble
from thyca.agents.compacting import SummaryOutcome, compact_with_llm
from thyca.agents.loop import AgentLoop
from thyca.agents.observe import Observe
from thyca.agents.think import Think
from thyca.bots.timeline import CanonicalConversationStore, WorkspaceTimeline
from thyca.configs import LimitsCfg
from thyca.conversations.runtime import ConversationManager
from thyca.conversations.runtime.compaction import PRIOR_CLEARED_MARKER
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message, ToolResult
from thyca.llms.llm_base import ChatReply


def _canonical_manager(root: Path, limits=None) -> ConversationManager:

    timeline = WorkspaceTimeline(root, "thyca")
    window = WindowRef(
        window_id="thyca", channel_id="webui", kind="dm", participants=("thyca",))
    conversation_id = "2026-01-01T00-00-00_ab12"
    store = CanonicalConversationStore(
        root / "bots" / "thyca" / "sessions",
        timeline=timeline, window=window, conversation_id=conversation_id)
    manager = ConversationManager(store=store, limits=limits)
    manager.load(conversation_id)
    return manager


@dataclass
class StubSummarizer:
    outcome: SummaryOutcome | None
    calls: list = field(default_factory=list)
    during: object = None

    async def summarize(self, fresh, prior_text, instructions):
        self.calls.append((list(fresh), prior_text, instructions))
        if callable(self.during):
            self.during()
        return self.outcome


@dataclass
class FakeLLM:
    replies: list[ChatReply]
    requests: list = field(default_factory=list)

    async def chat(self, messages, tools=None):
        self.requests.append(list(messages))
        return self.replies.pop(0)


@dataclass
class NoTools:
    async def submit(self, call) -> ToolResult:
        raise AssertionError("no tools expected")


def _seeded(tmp_path: Path, turns: int = 4) -> ConversationManager:
    manager = _canonical_manager(tmp_path, LimitsCfg(contextTokens=32000))
    for i in range(turns):
        manager.append(Message(role="user", content=f"old-{i}-" + "u" * 16000))
        manager.append(Message(role="assistant", content="a" * 16000))
    return manager


def _outcome(text: str = "SUMMARY BODY") -> SummaryOutcome:
    return SummaryOutcome(
        text=text,
        reply=ChatReply(
            content=text, usage={"prompt_tokens": 10, "completion_tokens": 2}, model="m"
        ),
        latency_ms=7,
    )


def test_llm_summary_lands_with_sidecar_cost_record(tmp_path: Path) -> None:
    manager = _seeded(tmp_path)
    stub = StubSummarizer(_outcome())
    result = asyncio.run(
        compact_with_llm(
            stub, manager, model="m", pricing=None, force=True, instructions="keep auth"
        )
    )
    assert result["compacted"] is True and result["summary"] == "llm"
    assert stub.calls and stub.calls[0][2] == "keep auth"
    messages = manager.current.messages
    assert messages[0].role == "system"
    assert "SUMMARY BODY" in (messages[0].content or "")
    sidecar = messages[-1]
    assert (sidecar.meta or {}).get("kind") == "compaction"
    assert sidecar.meta["usage"]["prompt_tokens"] == 10
    assert result["messages_after"] == len(messages)
    assert result["messages_after"] < result["messages_before"]


def test_summarizer_none_falls_back_to_excerpt(tmp_path: Path) -> None:
    manager = _seeded(tmp_path)
    result = asyncio.run(
        compact_with_llm(StubSummarizer(None), manager, model="m", pricing=None, force=True)
    )
    assert result["compacted"] is True and result["summary"] == "excerpt"
    assert all(
        (m.meta or {}).get("kind") != "compaction" for m in manager.current.messages
    )


def test_transcript_change_during_summary_falls_back_without_loss(
    tmp_path: Path,
) -> None:
    manager = _seeded(tmp_path)
    conversation_id = manager.current.conversation_id

    def append_meanwhile() -> None:
        other = _canonical_manager(tmp_path)
        other.load(conversation_id)
        other.append(Message(role="user", content="meanwhile-question"))

    stub = StubSummarizer(_outcome(), during=append_meanwhile)
    result = asyncio.run(
        compact_with_llm(stub, manager, model="m", pricing=None, force=True)
    )
    assert result["summary"] == "excerpt"
    contents = [m.content for m in manager.current.messages]
    assert "meanwhile-question" in contents


def test_no_plan_is_honest_noop(tmp_path: Path) -> None:
    manager = _canonical_manager(tmp_path, LimitsCfg(contextTokens=100_000))
    manager.append(Message(role="user", content="hi"))
    manager.append(Message(role="assistant", content="hello"))
    stub = StubSummarizer(_outcome())
    result = asyncio.run(
        compact_with_llm(stub, manager, model="m", pricing=None, force=True)
    )
    assert result == {
        "compacted": False,
        "messages_before": 2,
        "messages_after": 2,
        "summary": None,
    }
    assert stub.calls == []


def test_auto_compact_in_loop_uses_summarizer(tmp_path: Path) -> None:
    manager = _seeded(tmp_path, turns=8)
    conversation_id = manager.current.conversation_id
    stub = StubSummarizer(_outcome("AUTO SUMMARY"))
    llm = FakeLLM([ChatReply(content="next answer", usage={"prompt_tokens": 5}, model="m")])
    loop = AgentLoop(
        conversations=manager,
        assemble=Assemble(),
        think=Think(llm),
        act=Act(NoTools()),
        observe=Observe(manager),
        loop_max=3,
        model="m",
        context_tokens=32000,
        summarizer=stub,
    )
    assert asyncio.run(loop.run("new question")) == "next answer"
    assert stub.calls, "auto path must consult the summarizer"
    sent = "\n".join(m.content or "" for m in llm.requests[0])
    assert "AUTO SUMMARY" in sent
    assert "old-0-" not in sent
    stored = _canonical_manager(tmp_path).load(conversation_id).messages
    assert stored[0].role == "system" and "AUTO SUMMARY" in (stored[0].content or "")
    assert any((m.meta or {}).get("kind") == "compaction" for m in stored)


def test_drop_prior_clears_stale_marker_without_llm_and_is_idempotent(
    tmp_path: Path,
) -> None:
    manager = _seeded(tmp_path)
    first = asyncio.run(
        compact_with_llm(
            StubSummarizer(_outcome("OLD_STALE_PROJECT_SUMMARY")),
            manager,
            model="m",
            pricing=None,
            force=True,
        )
    )
    assert first["compacted"] is True
    assert "OLD_STALE_PROJECT_SUMMARY" in (manager.current.messages[0].content or "")

    manager.append(Message(role="user", content="brand new short topic"))
    manager.append(Message(role="assistant", content="fresh answer"))

    stub = StubSummarizer(_outcome("SHOULD_NOT_BE_CALLED"))
    cleared = asyncio.run(
        compact_with_llm(
            stub,
            manager,
            model="m",
            pricing=None,
            force=True,
            drop_prior=True,
        )
    )
    assert cleared["compacted"] is True
    assert cleared["summary"] == "cleared"
    assert stub.calls == []
    msgs = manager.current.messages
    assert msgs[0].content == PRIOR_CLEARED_MARKER
    assert "OLD_STALE_PROJECT_SUMMARY" not in "\n".join(m.content or "" for m in msgs)
    assert any(m.content == "brand new short topic" for m in msgs)

    again = asyncio.run(
        compact_with_llm(
            stub,
            manager,
            model="m",
            pricing=None,
            force=True,
            drop_prior=True,
        )
    )
    assert again["compacted"] is False
    assert again["summary"] is None


def test_drop_prior_summarizes_fresh_turns_without_carrying_old_summary(
    tmp_path: Path,
) -> None:
    manager = _seeded(tmp_path)
    asyncio.run(
        compact_with_llm(
            StubSummarizer(_outcome("ANCIENT_UNRELATED_TOPIC")),
            manager,
            model="m",
            pricing=None,
            force=True,
        )
    )
    for i in range(4):
        manager.append(Message(role="user", content=f"new-{i}-" + "n" * 16000))
        manager.append(Message(role="assistant", content="r" * 16000))

    stub = StubSummarizer(_outcome("FRESH_ONLY_SUMMARY"))
    result = asyncio.run(
        compact_with_llm(
            stub,
            manager,
            model="m",
            pricing=None,
            force=True,
            drop_prior=True,
        )
    )
    assert result["compacted"] is True
    assert result["summary"] == "llm"
    assert len(stub.calls) == 1
    fresh_sent, prior_sent, _ = stub.calls[0]
    assert fresh_sent
    assert prior_sent == ""
    marker = manager.current.messages[0].content or ""
    assert "FRESH_ONLY_SUMMARY" in marker
    assert "ANCIENT_UNRELATED_TOPIC" not in marker
