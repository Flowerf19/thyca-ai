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
from dataclasses import dataclass

#: Technical defaults (retention only, never content rules): enough room
#: for a busy room's recent delegations without unbounded process growth.
MAX_ARTIFACTS = 500
MAX_TOTAL_CHARS = 4 * 1024 * 1024


@dataclass(frozen=True)
class _StoredArtifact:
    body: str
    sender: str | None = None
    recipient: str | None = None


class BusArtifactStore:
    """Thread-safe span_id -> full-body store with bounded retention.

    Each artifact also records the envelope's sender and recipient
    (recorded by ``Bus.send``): ``bot_fetch`` may only return a body to
    one of those two parties. ``get`` stays the internal/raw lookup;
    caller-facing reads go through :meth:`get_for` / :meth:`parties`."""

    def __init__(
        self,
        max_artifacts: int = MAX_ARTIFACTS,
        max_total_chars: int = MAX_TOTAL_CHARS,
    ) -> None:
        self._max_artifacts = max_artifacts
        self._max_total_chars = max_total_chars
        self._lock = threading.Lock()
        self._items: OrderedDict[str, _StoredArtifact] = OrderedDict()
        self._total_chars = 0

    def put(
        self, span_id: str, body: str, *, sender: str | None = None,
        recipient: str | None = None,
    ) -> None:
        if not span_id or not isinstance(body, str):
            raise ValueError("artifact needs a span_id and a text body")
        with self._lock:
            previous = self._items.pop(span_id, None)
            if previous is not None:
                self._total_chars -= len(previous.body)
            self._items[span_id] = _StoredArtifact(
                body=body, sender=sender, recipient=recipient
            )
            self._total_chars += len(body)
            while self._items and (
                len(self._items) > self._max_artifacts
                or self._total_chars > self._max_total_chars
            ):
                _key, evicted = self._items.popitem(last=False)
                self._total_chars -= len(evicted.body)

    def get(self, span_id: str) -> str | None:
        """Full body for ``span_id``, or None when unknown/evicted.

        Raw internal lookup (no authorization): tool handlers must use
        :meth:`get_for` so a third bot never receives the body."""
        if not isinstance(span_id, str) or not span_id:
            return None
        with self._lock:
            item = self._items.get(span_id)
            return item.body if item is not None else None

    def parties(self, span_id: str) -> tuple[str | None, str | None] | None:
        """``(sender, recipient)`` for ``span_id``, or None if unknown."""
        if not isinstance(span_id, str) or not span_id:
            return None
        with self._lock:
            item = self._items.get(span_id)
            if item is None:
                return None
            return item.sender, item.recipient

    def get_for(self, span_id: str, caller: str | None) -> str | None:
        """Body only when ``caller`` is the sender or the recipient."""
        if caller is None:
            return None
        parties = self.parties(span_id)
        if parties is None or caller not in parties:
            return None
        return self.get(span_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def total_chars(self) -> int:
        with self._lock:
            return self._total_chars
