"""Embedding contracts and local providers."""

from .embed import (
    COSINE_FLOOR_MICRO,
    RRF_K,
    Embedder,
    FakeEmbedder,
    cosine,
    embedding_hash,
    fold_text,
    pack_unit,
    profile_id_for,
    rrf_ranks,
    unpack,
)
from .manifest import MODEL_MANIFEST, ModelManifest
from .onnx import OnnxEmbedder, resolve_embedder
from .payload import EmbeddingInputError, document_payload, query_payload

__all__ = [
    "COSINE_FLOOR_MICRO",
    "RRF_K",
    "Embedder",
    "FakeEmbedder",
    "cosine",
    "embedding_hash",
    "fold_text",
    "pack_unit",
    "profile_id_for",
    "rrf_ranks",
    "unpack",
    "MODEL_MANIFEST",
    "ModelManifest",
    "OnnxEmbedder",
    "resolve_embedder",
    "EmbeddingInputError",
    "document_payload",
    "query_payload",
]
