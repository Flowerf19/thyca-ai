"""Phase 2A: public room context (filter matrix + cursor delta + wiring)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from thyca.bots.room_context import (
    fingerprint,
    public_room_messages,
    room_delta,
)
from thyca.core.protocol import Message, ToolCall


def _msg(role, content, *, ts="2026-10-16T10:00:00Z", meta=None,
         tool_calls=None, reasoning=None):
    return Message(role=role, content=content, ts=ts, meta=meta,
                   tool_calls=tool_calls, reasoning=reasoning)


def _final(bot_id, content, *, kind="message", name=None, parent=None,
           ts="2026-10-16T10:01:00Z"):
    meta = {"bot_id": bot_id, "kind": kind, "parent_span_id": parent}
    if name is not None:
        meta["bot_name"] = name
    return _msg("assistant", content, ts=ts, meta=meta)


def test_filter_matrix() -> None:
    own = _final("coder", "my own words")
    bus_msg = _final("designer", "bus copy", name="Designer")
    bus_msg.meta["bus_event"] = True
    delegate = _final("designer", "do this", kind="delegate", name="Designer")
    rows = [
        _msg("user", "hello room"),
        _msg("user", "   "),
        _final("designer", "seen design", name="Designer"),
        own,
        bus_msg,
        delegate,
        _msg("assistant", "step update", meta={"bot_id": "designer", "kind": "step"}),
        _msg("tool", "tool output here"),
        _msg("assistant", None, meta={"bot_id": "designer", "kind": "message"}),
        _msg("system", "marker"),
        _msg("assistant", "thinking out", meta={"bot_id": "designer", "kind": "llm"},
             tool_calls=[ToolCall(id="c1", name="bash", arguments={})]),
        _msg("assistant", "reasoned", meta={"bot_id": "designer", "kind": "llm"},
             reasoning="secret chain"),
        _msg("assistant", "naming pick", meta={"kind": "naming"}),
        _msg("assistant", "compacted", meta={"kind": "compaction"}),
        _msg("assistant", "thyca root final", meta={"bot_id": "thyca", "kind": "llm"}),
        _msg("assistant", "legacy final"),
        _msg("assistant", "old bus message",
             meta={"bot_id": "designer", "kind": "message", "parent_span_id": "sp_1"}),
        _msg("assistant", "card body here",
             meta={"bot_id": "designer", "kind": "card"}),
        _msg("assistant", "",
             meta={"bot_id": "designer", "kind": "card",
                   "card": {"type": "question", "title": "Pick one"}}),
        _msg("assistant", "delegated result",
             meta={"bot_id": "designer", "kind": "result", "parent_span_id": "sp_9"}),
    ]
    items = public_room_messages(rows, "coder")
    by_content = {item["content"]: item["speaker"] for item in items}
    assert by_content["hello room"] == "user"
    assert by_content["seen design"] == "Designer"
    assert by_content["thyca root final"] == "thyca"
    assert by_content["legacy final"] == "thyca"
    assert by_content["card body here"] == "designer"
    assert by_content["[thẻ question: Pick one]"] == "designer"
    assert by_content["delegated result"] == "designer"
    assert "reasoned" in by_content  # content only; reasoning never leaks
    for dropped in ("my own words", "bus copy", "do this", "step update",
                    "tool output here", "naming pick", "compacted",
                    "old bus message"):
        assert dropped not in by_content
    assert all("fingerprint" in item and len(item["fingerprint"]) == 16
               for item in items)


def test_delta_first_match_mismatch_and_empty() -> None:
    rows = [_msg("user", "one"), _msg("user", "two")]
    text, cursor = room_delta(rows, "coder", None)
    assert "lịch sử phòng chưa đọc" in text
    assert "trước khi bạn tham gia" in text
    assert "one" in text and "two" in text
    count, last_fp = cursor
    assert count == 2
    # Matching cursor + one new message: only the new one.
    rows.append(_msg("user", "three"))
    text2, cursor2 = room_delta(rows, "coder", cursor)
    assert "Tin mới trong phòng" in text2
    assert "three" in text2 and "one" not in text2
    # Nothing new: empty text, same cursor.
    text3, cursor3 = room_delta(rows, "coder", cursor2)
    assert text3 == "" and cursor3 == cursor2
    # Edited history: fingerprint mismatch redelivers everything.
    edited = [_msg("user", "one EDITED"), _msg("user", "two")]
    text4, _cursor4 = room_delta(edited, "coder", cursor2)
    assert "đã thay đổi" in text4
    assert "one EDITED" in text4
    # Preamble always present on delivery.
    assert "KHÔNG phải lời của bạn" in text2


def test_fingerprint_stable_shape() -> None:
    item = {"ts": "t", "speaker": "user", "content": "hi"}
    assert fingerprint(item) == fingerprint(dict(item))
    assert len(fingerprint(item)) == 16
