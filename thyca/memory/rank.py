"""Pure ranking policy for memory search hits.

Extracted from ``memory/facade.py`` (SRP): the facade owns remember/forget /
reinforce / get / search orchestration; scoring an in-order phrase span over
normalized text is a pure policy with no I/O.
"""
from __future__ import annotations

import re

from thyca.memory.archived import Hit


def _promote_in_order_span(
    query: str, hits: list[Hit], chunker, hays: dict[str, str] | None = None
) -> list[Hit]:
    """Lift hits whose heading+body contain a longer prefix of the query tokens.

    Score = longest run of consecutive query tokens (from the start of the
    query) appearing as consecutive tokens in the leaf. Gaps in the leaf break
    the run. Ties keep input order.
    """
    tokens = [t for t in _phrase_key(query, chunker).split() if t]
    if len(tokens) < 2:
        return hits
    texts = hays or {}

    def span(hit: Hit) -> int:
        raw = texts.get(hit.chunk_id) or f"{hit.heading} {hit.snippet}"
        hay = _phrase_key(raw, chunker).split()
        best = 0
        for i in range(len(hay)):
            n = 0
            for q_tok, h_tok in zip(tokens, hay[i:]):
                if q_tok == h_tok or (len(q_tok) >= 4 and h_tok.startswith(q_tok)):
                    n += 1
                else:
                    break
            best = max(best, n)
        return best

    return sorted(hits, key=lambda hit: -span(hit))


def _phrase_key(text: str, chunker) -> str:
    """Normalize + collapse non-word runs to single spaces (FuzzyWuzzy-style)."""
    return re.sub(r"[\W_]+", " ", chunker.normalize(text)).strip()
