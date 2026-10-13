"""BE-4: Thyca memory sidecar job (extract → apply, idle decision)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thyca.core.protocol import Message
from thyca.memory import jobs
from thyca.memory.facade import MemoryFacade


def test_should_idle_sweep() -> None:
    assert jobs.should_idle_sweep("2026-08-01T10:00:00Z", "2026-08-01T10:16:00Z") is True
    assert jobs.should_idle_sweep("2026-08-01T10:00:00Z", "2026-08-01T10:14:59Z") is False
    assert jobs.should_idle_sweep("bogus", "2026-08-01T10:16:00Z") is False
    assert jobs.should_idle_sweep("2026-08-01T10:00:00Z", "2026-08-01T10:15:00Z",
                                  idle_s=900) is True


def test_parse_extraction_drops_invalid_items() -> None:
    items = jobs.parse_extraction(
        '[{"topic": "t", "summary": "s", "importance": 9, "bots": ["coder"], '
        '"team_proposal": true}, {"topic": "", "summary": "x"}, "nope"]'
    )
    assert len(items) == 1
    assert items[0].importance == 3
    assert items[0].bots == ("coder",)
    assert items[0].team_proposal is True
    assert jobs.parse_extraction("not json") == []
    assert jobs.parse_extraction('{"topic": "t"}') == []


def test_apply_extracted_writes_private_and_proposes(tmp_path: Path) -> None:
    import json

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    out = jobs.apply_extracted(
        facade,
        [jobs.ExtractedMemory(topic="gift", summary="egg coffee",
                              bots=("coder",), team_proposal=True)],
    )
    assert len(out["written"]) == 1
    assert len(out["proposed"]) == 1
    stored = json.loads((tmp_path / "memory_candidates.json").read_text(encoding="utf-8"))
    assert stored[0]["bot_id"] == "coder"
    assert stored[0]["status"] == "pending"
    # Written private, never team-direct (read the heading: today is unindexed).
    sid = out["written"][0]
    day = sid.split("#")[0]
    owners = None
    for line in (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8").splitlines():
        from thyca.memory.heading import parse_heading as _parse

        meta = _parse(line)
        if meta is not None and meta.entry_id == sid.split("#")[1]:
            owners = meta.bots
    assert owners == ("coder",)


def test_extract_from_transcript_uses_model_json(tmp_path: Path) -> None:
    import asyncio
    import json

    class Reply:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeConnect:
        def __init__(self) -> None:
            self.seen: list = []

        async def chat(self, messages, tools=None):
            self.seen.append(messages)
            return Reply(json.dumps([{"topic": "t", "summary": "s"}]))

    connect = FakeConnect()
    items = asyncio.run(jobs.extract_from_transcript(connect, "m", "hello"))
    assert len(items) == 1
    assert connect.seen and isinstance(connect.seen[0][0], Message)


def test_extract_failure_is_empty_not_loud() -> None:
    import asyncio

    class BadConnect:
        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    assert asyncio.run(jobs.extract_from_transcript(BadConnect(), "m", "hi")) == []


def test_transcript_tail_skips_sidecars() -> None:
    messages = [
        Message(role="assistant", content="x", ts="2026-08-01T10:00:00Z",
                meta={"kind": "naming"}),
        Message(role="user", content="remember this", ts="2026-08-01T10:01:00Z"),
    ]
    tail = jobs.transcript_tail(messages)
    assert "remember this" in tail
    assert tail.count("[assistant") == 0
