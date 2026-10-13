"""Compaction summarizer prompt policy (sessions/summary.py)."""
from __future__ import annotations

import asyncio

from thyca.core.protocol import Message, ToolCall
from thyca.llm.llm_base import ChatReply
from thyca.sessions.summary import (
    SUMMARY_INPUT_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    accept_summary,
    omitted_text,
    propose_summary,
    summary_messages,
)

_TS = "2026-10-13T00:00:00Z"


def _msg(role: str, content: str | None) -> Message:
    return Message(role=role, content=content, ts=_TS)  # type: ignore[arg-type]


def test_omitted_text_skips_tool_bodies_keeps_names_and_paths() -> None:
    fresh = [
        _msg("user", "fix the parser"),
        Message(
            role="assistant",
            content="looking now",
            ts=_TS,
            tool_calls=[
                ToolCall(id="c1", name="read", arguments={"path": "/tmp/a.py"}),
                ToolCall(id="c2", name="bash", arguments={"command": "secret cmd"}),
            ],
        ),
        Message(role="tool", content="HUGE TOOL BODY", ts=_TS, tool_call_id="c1"),
        _msg("assistant", "done"),
    ]
    text = omitted_text(fresh)
    assert "User: fix the parser" in text
    assert "read(/tmp/a.py)" in text
    assert "bash" in text and "secret cmd" not in text
    assert "HUGE TOOL BODY" not in text
    assert "Assistant: done" in text


def test_omitted_text_merges_prior_and_caps_input_tail_biased() -> None:
    fresh = [_msg("user", "x" * (SUMMARY_INPUT_MAX_CHARS + 5000))]
    text = omitted_text(fresh, prior_text="earlier decision")
    assert text.startswith("Prior summary to merge:\nearlier decision")
    assert "truncated for summarizer input" in text
    assert len(text) <= SUMMARY_INPUT_MAX_CHARS
    assert text.endswith("x")


def test_accept_summary_caps_and_rejects_empty() -> None:
    assert accept_summary("") is None
    assert accept_summary("   ") is None
    assert accept_summary(None) is None
    assert accept_summary("  keep this  ") == "keep this"
    long = accept_summary("y" * (SUMMARY_MAX_CHARS + 100))
    assert long is not None and len(long) == SUMMARY_MAX_CHARS


def test_propose_summary_uses_instruction_and_focus() -> None:
    seen: list[list[Message]] = []

    async def chat(messages, tools=None):
        seen.append(list(messages))
        assert tools is None
        return ChatReply(content="  merged summary  ")

    out = asyncio.run(
        propose_summary(
            chat,
            [_msg("user", "hello")],
            prior_text="prior bit",
            instructions="focus on auth next",
        )
    )
    assert out == "merged summary"
    assert seen[0][0].role == "system" and "continuation summary" in (seen[0][0].content or "")
    body = seen[0][1].content or ""
    assert "prior bit" in body and "focus on auth next" in body


def test_compaction_instruction_is_packaged_seed() -> None:
    from pathlib import Path

    from thyca.sessions.summary import summary_instruction

    prompts_dir = Path(__file__).resolve().parents[1] / "thyca" / "seeds" / "prompts"
    text = (prompts_dir / "compaction.md").read_text(encoding="utf-8").strip()
    assert summary_instruction() == text
    for slot in ("Goal", "Done", "Current state", "Files", "Errors & fixes", "Constraints", "Next"):
        assert slot in text
    assert "merge" in text and "3500" in text
    pyproject = (prompts_dir.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"thyca/seeds/prompts/compaction.md" = "thyca/seeds/prompts/compaction.md"' in pyproject


def test_summary_messages_none_without_text() -> None:
    assert summary_messages([], prior_text="") is None
    assert summary_messages([_msg("tool", "body only")]) is None
