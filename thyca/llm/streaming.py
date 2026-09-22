"""Streaming delta forwarders shared by the OpenAI connects.

Both Chat Completions and Responses stream text/thinking deltas the UI
renders live; the batching/redaction rules live here so the two cannot drift.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from thyca.core.protocol import RESULT_CAP_BYTES

_FLUSH_CHARS = 64
_FLUSH_S = 0.08


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


class ContentOut:
    """Forward reply-text deltas while streaming. Never accumulates or caps:
    the authoritative content is assembled separately and uncapped."""

    _FLUSH_CHARS = _FLUSH_CHARS
    _FLUSH_S = _FLUSH_S

    def __init__(self, on_content: Callable[[str], None] | None) -> None:
        self._on = on_content
        self._buf = ""
        self._last = time.monotonic()

    def add(self, piece: str) -> None:
        if not piece:
            return
        self._buf += piece
        if len(self._buf) >= self._FLUSH_CHARS or (time.monotonic() - self._last) >= self._FLUSH_S:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        chunk, self._buf = self._buf, ""
        self._last = time.monotonic()
        if self._on is None:
            return
        try:
            self._on(chunk)
        except Exception:
            pass


class ReasoningOut:
    def __init__(self, key: str, on_reasoning: Callable[[str], None] | None) -> None:
        self._key = key
        self._on = on_reasoning
        self._buf = ""
        self._last = time.monotonic()
        self._parts: list[str] = []
        self._bytes = 0
        self._capped = False

    def add(self, piece: str) -> None:
        if self._capped or not piece:
            return
        piece = _redact(piece, self._key)
        encoded = piece.encode("utf-8")
        room = RESULT_CAP_BYTES - self._bytes
        if room <= 0:
            self._capped = True
            return
        if len(encoded) > room:
            piece = encoded[:room].decode("utf-8", errors="ignore")
            encoded = piece.encode("utf-8")
            self._capped = True
            if not piece:
                return
        self._parts.append(piece)
        self._bytes += len(encoded)
        self._buf += piece
        if len(self._buf) >= _FLUSH_CHARS or (time.monotonic() - self._last) >= _FLUSH_S:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        chunk, self._buf = self._buf, ""
        self._last = time.monotonic()
        if self._on is None:
            return
        try:
            self._on(chunk)
        except Exception:
            pass

    def text(self) -> str | None:
        self.flush()
        return "".join(self._parts) or None
