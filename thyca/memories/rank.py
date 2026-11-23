"""Pure ranking policy for memory search hits.

Extracted from ``memory/facade.py`` (SRP): the facade owns remember/forget /
reinforce / get / search orchestration; scoring an in-order phrase span over
normalized text is a pure policy with no I/O.
"""
from __future__ import annotations

import math
import re

from thyca.memories.archived import Hit

_MIN_PHRASE_TOKENS = 3
_AVG_IDF_FLOOR = 0.55
_SPAN_WEIGHT = 0.30
_MAX_SPAN_IDF = 3.0


def _promote_in_order_span(
    query: str, hits: list[Hit], chunker, hays: dict[str, str] | None = None
) -> list[Hit]:
    """Boost hits matching a high-IDF contiguous sub-phrase of 3+ query tokens."""
    tokens = [t for t in _phrase_key(query, chunker).split() if t]
    if len(tokens) < _MIN_PHRASE_TOKENS or len(hits) < 2:
        return hits
    texts = hays or {}
    tokenized_hays = [
        _phrase_key(texts.get(hit.chunk_id) or f"{hit.heading} {hit.snippet}", chunker).split()
        for hit in hits
    ]
    n_docs = len(tokenized_hays)
    denom = math.log(n_docs + 1.0)
    unique_q = set(tokens)
    doc_freq = dict.fromkeys(unique_q, 0)
    for hay in tokenized_hays:
        hay_set = set(hay)
        for tok in unique_q & hay_set:
            doc_freq[tok] += 1
    idf = {tok: math.log((n_docs + 1.0) / (doc_freq[tok] + 0.5)) / denom for tok in unique_q}

    q_positions: dict[str, list[int]] = {}
    for qi, q_tok in enumerate(tokens):
        q_positions.setdefault(q_tok, []).append(qi)

    max_score = max((hit.score for hit in hits), default=0.0)
    scale = max_score if max_score > 0.0 else 1.0
    q_len = len(tokens)

    rescored: list[tuple[float, int, Hit]] = []
    for idx, (hit, hay) in enumerate(zip(hits, tokenized_hays, strict=True)):
        best_w = 0.0
        hay_len = len(hay)
        for hi in range(hay_len):
            starts = q_positions.get(hay[hi])
            if not starts:
                continue
            for qi in starts:
                span_w = 0.0
                matched = 0
                while qi + matched < q_len and hi + matched < hay_len:
                    q_tok = tokens[qi + matched]
                    h_tok = hay[hi + matched]
                    if q_tok == h_tok or (len(q_tok) >= 4 and h_tok.startswith(q_tok)):
                        span_w += idf[q_tok]
                        matched += 1
                    else:
                        break
                if matched >= _MIN_PHRASE_TOKENS and (span_w / matched) >= _AVG_IDF_FLOOR:
                    best_w = max(best_w, span_w)
        eff = hit.score + _SPAN_WEIGHT * scale * min(best_w, _MAX_SPAN_IDF)
        rescored.append((eff, idx, hit))

    rescored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in rescored]


def _phrase_key(text: str, chunker) -> str:
    """Normalize + collapse non-word runs to single spaces (FuzzyWuzzy-style)."""
    normalized = text if (text.isascii() and text.islower()) else chunker.normalize(text)
    return re.sub(r"[\W_]+", " ", normalized).strip()
