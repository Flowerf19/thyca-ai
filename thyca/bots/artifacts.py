"""Bounded in-process store for full A2A envelope bodies (CBE-3).

Bus envelopes are prompt-facing: they carry only an excerpt plus ``refs``.
The full body lives here, keyed by the envelope's ``span_id``, so a
recipient that actually needs it can fetch it with ``bot_fetch`` instead
of every prompt paying for every payload. The bus is in-process for v1,
so this store is too — nothing here is a durable database.

Retention is bounded like the tool gateway's: at most
``MAX_ARTIFACTS`` entries and ``MAX_TOTAL_CHARS`` characters in total,
oldest-first eviction. An evicted artifact is simply gone; ``bot_fetch``
reports that honestly instead of inventing content.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

#: Technical defaults (retention only, never content rules): enough room
#: for a busy room's recent delegations without unbounded process growth.
MAX_ARTIFACTS = 500
MAX_TOTAL_CHARS = 4 * 1024 * 1024


class BusArtifactStore:
    """Thread-safe span_id -> full-body store with bounded retention."""

    def __init__(
        self,
        max_artifacts: int = MAX_ARTIFACTS,
        max_total_chars: int = MAX_TOTAL_CHARS,
    ) -> None:
        self._max_artifacts = max_artifacts
        self._max_total_chars = max_total_chars
        self._lock = threading.Lock()
        self._items: OrderedDict[str, str] = OrderedDict()
        self._total_chars = 0

    def put(self, span_id: str, body: str) -> None:
        if not span_id or not isinstance(body, str):
            raise ValueError("artifact needs a span_id and a text body")
        with self._lock:
            previous = self._items.pop(span_id, None)
            if previous is not None:
                self._total_chars -= len(previous)
            self._items[span_id] = body
            self._total_chars += len(body)
            while self._items and (
                len(self._items) > self._max_artifacts
                or self._total_chars > self._max_total_chars
            ):
                _key, evicted = self._items.popitem(last=False)
                self._total_chars -= len(evicted)

    def get(self, span_id: str) -> str | None:
        """Full body for ``span_id``, or None when unknown/evicted."""
        if not isinstance(span_id, str) or not span_id:
            return None
        with self._lock:
            return self._items.get(span_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def total_chars(self) -> int:
        with self._lock:
            return self._total_chars
