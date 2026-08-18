"""Embedding contract, cosine, and RRF. No model download."""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Protocol

import numpy as np

COSINE_FLOOR_MICRO = 300_000
RRF_K = 60


class Embedder(Protocol):
    profile_id: str
    dimension: int

    def embed_query(self, text: str) -> list[float]: ...

    def embed_docs(self, texts: list[str]) -> list[list[float] | None]: ...


def profile_id_for(fields: dict[str, object]) -> str:
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def embedding_hash(profile_id: str, embed_text: str) -> str:
    return hashlib.sha256(profile_id.encode("utf-8") + b"\0" + embed_text.encode("utf-8")).hexdigest()


def pack_unit(values: list[float]) -> bytes:
    arr = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if not math.isfinite(norm) or norm == 0.0:
        raise ValueError("embedding must be finite and non-zero")
    return (arr / norm).tobytes()


def unpack(blob: bytes, dimension: int) -> np.ndarray | None:
    if len(blob) != dimension * 4:
        return None
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.shape != (dimension,) or not bool(np.all(np.isfinite(arr))):
        return None
    return arr


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


def rrf_ranks(lists: list[list[str]], *, k: int = RRF_K) -> list[tuple[str, float, int, int]]:
    ranks: dict[str, list[int]] = {}
    for items in lists:
        for index, item in enumerate(items, start=1):
            ranks.setdefault(item, []).append(index)
    scored = [
        (item, sum(1.0 / (k + rank) for rank in item_ranks), len(item_ranks), min(item_ranks))
        for item, item_ranks in ranks.items()
    ]
    scored.sort(key=lambda row: (-row[1], -row[2], row[3], row[0]))
    return scored


def fold_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").lower()


class FakeEmbedder:
    """Deterministic stand-in so hybrid tests do not need the local ONNX model."""

    dimension = 8

    def __init__(self, aliases: dict[str, list[float]] | None = None) -> None:
        self.aliases = aliases or {
            "thit quay": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "mon nuong": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        }
        self.profile_id = profile_id_for({"provider": "fake", "dimension": self.dimension})
        self.query_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._embed(text)

    def embed_docs(self, texts: list[str]) -> list[list[float] | None]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        folded = fold_text(text)
        for needle, vector in self.aliases.items():
            if needle in folded:
                return list(vector)
        digest = hashlib.sha256(folded.encode("utf-8")).digest()
        return [byte / 255.0 for byte in digest[: self.dimension]]
