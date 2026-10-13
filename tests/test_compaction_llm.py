"""LLM compaction orchestration: summary landing, fallback, auto path."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from thyca.agent.act import Act
from thyca.agent.assemble import Assemble
from thyca.agent.compacting import SummaryOutcome, compact_with_llm
from thyca.agent.loop import AgentLoop
from thyca.agent.observe import Observe
from thyca.agent.think import Think
from thyca.config import LimitsCfg
from thyca.core.protocol import Message, ToolResult
from thyca.llm.llm_base import ChatReply
from thyca.sessions import SessionManager


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


def _seeded(tmp_path: Path, turns: int = 4) -> SessionManager:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    manager.create()
    for i in range(turns):
        manager.append(Message(role="user", content=f"old-{i}-" + "u" * 300))
        manager.append(Message(role="assistant", content="a" * 300))
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
    session_id = manager.current.id

    def append_meanwhile() -> None:
        other = SessionManager(tmp_path)
        other.load(session_id)
        other.append(Message(role="user", content="meanwhile-question"))

    stub = StubSummarizer(_outcome(), during=append_meanwhile)
    result = asyncio.run(
        compact_with_llm(stub, manager, model="m", pricing=None, force=True)
    )
    assert result["summary"] == "excerpt"
    contents = [m.content for m in manager.current.messages]
    assert "meanwhile-question" in contents


def test_no_plan_is_honest_noop(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=100_000))
    manager.create()
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
    session_id = manager.current.id
    stub = StubSummarizer(_outcome("AUTO SUMMARY"))
    llm = FakeLLM([ChatReply(content="next answer", usage={"prompt_tokens": 5}, model="m")])
    loop = AgentLoop(
        sessions=manager,
        assemble=Assemble(),
        think=Think(llm),
        act=Act(NoTools()),
        observe=Observe(manager),
        loop_max=3,
        model="m",
        context_tokens=1000,
        summarizer=stub,
    )
    assert asyncio.run(loop.run("new question")) == "next answer"
    assert stub.calls, "auto path must consult the summarizer"
    sent = "\n".join(m.content or "" for m in llm.requests[0])
    assert "AUTO SUMMARY" in sent
    assert "old-0-" not in sent
    stored = SessionManager(tmp_path).load(session_id).messages
    assert stored[0].role == "system" and "AUTO SUMMARY" in (stored[0].content or "")
    assert any((m.meta or {}).get("kind") == "compaction" for m in stored)
