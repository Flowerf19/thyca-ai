"""Local ONNX embedder provider — no weights, no ONNX load."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from thyca.config import EmbeddingCfg, save
from thyca.memory.embed_manifest import (
    FILES_SHA256,
    MODEL_MANIFEST,
    QUERY_PROMPT,
    QUERY_PROMPT_UTF8_SHA256,
    manifest_digest,
)
from thyca.memory.embed_onnx import MARKER_NAME, OnnxEmbedder, resolve_embedder
from thyca.memory.embed_payload import EmbeddingInputError, document_payload, query_payload
from thyca.tools.memory import MemoryFacade


def test_manifest_pins_five_files_and_prompt_hash() -> None:
    assert len(FILES_SHA256) == 5
    assert len(MODEL_MANIFEST.files) == 5
    assert (
        hashlib.sha256(QUERY_PROMPT.encode("utf-8")).hexdigest()
        == QUERY_PROMPT_UTF8_SHA256
    )
    assert MODEL_MANIFEST.profile_id == manifest_digest()
    assert MODEL_MANIFEST.profile_id == manifest_digest(MODEL_MANIFEST)


def test_profile_id_is_stable() -> None:
    assert MODEL_MANIFEST.profile_id == MODEL_MANIFEST.profile_id
    assert len(MODEL_MANIFEST.profile_id) == 64


def test_manifest_import_does_not_need_onnx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "tokenizers", None)
    sys.modules.pop("thyca.memory.embed_manifest", None)
    import thyca.memory.embed_manifest as manifest

    assert manifest.MODEL_MANIFEST.dimensions == 640


def test_query_payload_prepends_prompt() -> None:
    assert query_payload("  cà phê  ") == QUERY_PROMPT + "cà phê"
    with pytest.raises(EmbeddingInputError):
        query_payload("   ")
    raw = "bún bò\nleaf"
    assert document_payload(raw) is raw
    assert QUERY_PROMPT not in document_payload(raw)


def test_resolve_missing_dir_is_none(tmp_path: Path) -> None:
    assert resolve_embedder(EmbeddingCfg(), thyca_dir=tmp_path) is None
    assert resolve_embedder(EmbeddingCfg(provider="openai", model="text-embedding-3-small",
                                        baseUrl="https://api.openai.com/v1",
                                        apiKeyEnv="OPENAI_API_KEY"),
                           thyca_dir=tmp_path) is None


def test_resolve_marker_does_not_load_onnx(tmp_path: Path) -> None:
    model_dir = tmp_path / "models" / MODEL_MANIFEST.model / MODEL_MANIFEST.revision
    model_dir.mkdir(parents=True)
    (model_dir / MARKER_NAME).write_text(
        json.dumps({"manifest_digest": manifest_digest()}),
        encoding="utf-8",
    )
    embedder = resolve_embedder(EmbeddingCfg(), thyca_dir=tmp_path)
    assert isinstance(embedder, OnnxEmbedder)
    assert embedder.profile_id == MODEL_MANIFEST.profile_id
    assert embedder._session is None


def test_facade_reads_thyca_dir_config(tmp_path: Path) -> None:
    from thyca.config import Config, ProviderCfg

    save(
        Config(
            provider=ProviderCfg(),
            embedding=EmbeddingCfg(provider="openai", model="text-embedding-3-small",
                                  baseUrl="https://api.openai.com/v1",
                                  apiKeyEnv="OPENAI_API_KEY"),
        ),
        tmp_path / "config.json",
    )
    model_dir = tmp_path / "models" / MODEL_MANIFEST.model / MODEL_MANIFEST.revision
    model_dir.mkdir(parents=True)
    (model_dir / MARKER_NAME).write_text(
        json.dumps({"manifest_digest": manifest_digest()}),
        encoding="utf-8",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    assert facade.archive.embedder is None
