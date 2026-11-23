"""Core helper pins (truncate_to_cap, protocol shapes)."""
from __future__ import annotations

import pytest

from thyca.core.protocol import META_CAP_BYTES, RESULT_CAP_BYTES, Message, truncate_to_cap
from thyca.llms.streaming import ReasoningOut


def test_x6_truncate_to_cap_kernel() -> None:

    assert truncate_to_cap(b"abc", 5) == (b"abc", False)
    assert truncate_to_cap(b"abc", 3) == (b"abc", False)
    kept, clipped = truncate_to_cap("é".encode() * 10, 3)
    assert clipped and kept == "é".encode()
    kept.decode("utf-8")
    assert truncate_to_cap(b"", 0) == (b"", False)


def test_x6_streaming_reasoning_budget_uses_cap() -> None:

    out = ReasoningOut("secret", None)
    out.add("x" * (RESULT_CAP_BYTES + 100))
    text = out.text()
    assert text is not None and len(text.encode("utf-8")) <= RESULT_CAP_BYTES
    out.add("late")
    assert out.text() == text


def test_m1_content_key_always_present() -> None:

    assert Message(role="assistant").to_canonical_dict()["content"] is None
    assert "content" in Message(role="user", content="x").to_canonical_dict()


def test_meta_cap_checked_at_construction_and_serialization() -> None:


    big = {"blob": "x" * (META_CAP_BYTES + 1)}
    with pytest.raises(ValueError, match="meta exceeds 4096 bytes"):
        Message(role="user", content="x", meta=big)
    msg = Message(role="user", content="x", meta={"ok": True})
    object.__setattr__(msg, "meta", big)
    with pytest.raises(ValueError, match="meta exceeds 4096 bytes"):
        msg.to_canonical_dict()
