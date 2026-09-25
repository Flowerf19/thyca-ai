"""Streaming delta forwarders shared by the OpenAI connects.

Both Chat Completions and Responses stream text/thinking deltas the UI
renders live; the batching/redaction rules live here so the two cannot drift.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from thyca.core.protocol import RESULT_CAP_BYTES, truncate_to_cap

from ._http import redact

_FLUSH_CHARS = 64
_FLUSH_S = 0.08


class ContentOut:
    """Forward reply-text deltas while streaming. Never accumulates or caps:
    the authoritative content is assembled separately and uncapped."""

    _FLUSH_CHARS = _FLUSH_CHARS
    _FLUSH_S = _FLUSH_S

    def __init__(
        self, on_content: Callable[[str], None] | None, key: str = ""
    ) -> None:
        self._on = on_content
        self._key = key
        self._buf = ""
        self._last = time.monotonic()

    def add(self, piece: str) -> None:
        if not piece:
            return
        self._buf += piece
        if len(self._buf) >= self._FLUSH_CHARS or (time.monotonic() - self._last) >= self._FLUSH_S:
            self.flush()

    def _split(self) -> tuple[str, str]:
        # Hold back a tail shorter than the key so a secret split across
        # flushes is only emitted (redacted) once its remainder arrives.
        tail = len(self._key) - 1 if self._key else 0
        if tail and len(self._buf) <= tail:
            return "", self._buf
        if tail:
            return self._buf[:-tail], self._buf[-tail:]
        return self._buf, ""

    def _emit(self, chunk: str) -> None:
        self._last = time.monotonic()
        if self._on is None:
            return
        try:
            self._on(redact(chunk, self._key))
        except Exception:
            pass

    def flush(self) -> None:
        if not self._buf:
            return
        chunk, self._buf = self._split()
        if not chunk:
            return
        self._emit(chunk)

    def finish(self) -> None:
        """Emit the held-back tail (redacted). Call once at end of stream."""
        if not self._buf:
            return
        chunk, self._buf = self._buf, ""
        self._emit(chunk)


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
        piece = redact(piece, self._key)
        room = RESULT_CAP_BYTES - self._bytes
        if room <= 0:
            self._capped = True
            return
        kept, clipped = truncate_to_cap(piece.encode("utf-8"), room)
        if clipped:
            piece = kept.decode("utf-8")
            self._capped = True
            if not piece:
                return
        encoded = piece.encode("utf-8")
        self._parts.append(piece)
        self._bytes += len(encoded)
        self._buf += piece
        if len(self._buf) >= _FLUSH_CHARS or (time.monotonic() - self._last) >= _FLUSH_S:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        # Same hold-back as ContentOut: a key split across flushes must
        # not reach the live callback unredacted.
        tail = len(self._key) - 1 if self._key else 0
        if tail and len(self._buf) <= tail:
            return
        if tail:
            chunk, self._buf = self._buf[:-tail], self._buf[-tail:]
        else:
            chunk, self._buf = self._buf, ""
        self._last = time.monotonic()
        if self._on is None:
            return
        try:
            self._on(redact(chunk, self._key))
        except Exception:
            pass

    def text(self) -> str | None:
        if self._buf:
            chunk, self._buf = self._buf, ""
            self._last = time.monotonic()
            if self._on is not None:
                try:
                    self._on(redact(chunk, self._key))
                except Exception:
                    pass
        # Redact the join, not just each piece: a key split across two
        # SSE chunks is whole only here.
        return redact("".join(self._parts), self._key) or None
