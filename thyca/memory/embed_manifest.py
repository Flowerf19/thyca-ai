"""Locked identity for the local ONNX embedding model.

Stdlib only: no inference runtime or tokenizer import. Swap the pinned
constants here when the default local model changes; the provider class stays.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

PROVIDER = "local"
MODEL = "harrier-q4"
REPOSITORY = "onnx-community/harrier-oss-v1-270m-ONNX"
REVISION = "d59c919d0159aea2c19ed7d04288fcdd048d0f9c"

FILES_SHA256: dict[str, str] = {
    "onnx/model_q4.onnx": "228dca2603b907d673dd99cf89c309c0ca68baeed127416a5e027a48e62b0f49",
    "onnx/model_q4.onnx_data": "b5a15487360f5341659480ae4b5ad60028d5f865bd329196ec8d5708bbed3118",
    "config.json": "5366f9919a82aaeceb6707bf218c5769f414d60f5dbaf781fa07e5465487fd7c",
    "tokenizer.json": "ec95be298bea26f90370854faa650744c9fb0a04ca5e5ff95dd3913393ac5e45",
    "tokenizer_config.json": "135405f3479eaebc473e2e78593f2195c7598948a215ee748758def426b30f59",
}

# Keep this string byte-exact.  Queries get it; document payloads do not.
QUERY_PROMPT = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query"
    "\nQuery: "
)
QUERY_PROMPT_UTF8_SHA256 = "df4b2898bf22e00bacddddd489243a3f8793730e38b842ec10161cebd94d36d6"

# This is identity metadata, not an instruction to the provider.  Chunker is
# the owner of the actual normalization and already stores the resulting text.
DOCUMENT_TEMPLATE = 'normalize(session_title) + "\\n" + normalize(leaf)'
INPUT_VERSION = 2
DIMENSIONS = 640
DTYPE = "float32"
NORMALIZATION = "unit_l2"


@dataclass(frozen=True)
class ModelManifest:
    """Immutable model and payload identity for one Thyca embedder profile."""

    provider: str
    model: str
    repo: str
    revision: str
    files: tuple[tuple[str, str], ...]
    query_prompt: str
    query_prompt_utf8_sha256: str
    document_template: str
    input_version: int
    dimensions: int
    dtype: str
    normalization: str

    @property
    def dimension(self) -> int:
        """Protocol spelling for the manifest's embedding width."""
        return self.dimensions

    @property
    def query_prompt_sha256(self) -> str:
        """Short alias for callers that do not use the UTF-8-qualified name."""
        return self.query_prompt_utf8_sha256

    @property
    def identity_fields(self) -> dict[str, Any]:
        """All fields that contribute to :attr:`profile_id`.

        The files are an ordered list so the canonical JSON is independent of
        dictionary insertion order while retaining each pinned path/hash pair.
        """
        return {
            "provider": self.provider,
            "model": self.model,
            "repo": self.repo,
            "revision": self.revision,
            "files": list(self.files),
            "query_prompt": self.query_prompt,
            "query_prompt_utf8_sha256": self.query_prompt_utf8_sha256,
            "document_template": self.document_template,
            "input_version": self.input_version,
            "dimensions": self.dimensions,
            "dtype": self.dtype,
            "normalization": self.normalization,
        }

    @property
    def profile_id(self) -> str:
        """SHA-256 of canonical JSON for the complete immutable identity."""
        return hashlib.sha256(canonical_json(self.identity_fields).encode("utf-8")).hexdigest()



def canonical_json(value: object) -> str:
    """Serialize identity data deterministically for hashing and markers."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _ordered_files() -> tuple[tuple[str, str], ...]:
    return tuple(sorted(FILES_SHA256.items()))


def _build_manifest() -> ModelManifest:
    manifest = ModelManifest(
        provider=PROVIDER,
        model=MODEL,
        repo=REPOSITORY,
        revision=REVISION,
        files=_ordered_files(),
        query_prompt=QUERY_PROMPT,
        query_prompt_utf8_sha256=QUERY_PROMPT_UTF8_SHA256,
        document_template=DOCUMENT_TEMPLATE,
        input_version=INPUT_VERSION,
        dimensions=DIMENSIONS,
        dtype=DTYPE,
        normalization=NORMALIZATION,
    )
    # Explicit runtime checks are intentional: these must also run under
    # ``python -O`` because a changed prompt or file set changes model identity.
    if len(manifest.files) != 5:
        raise RuntimeError(f"embedding manifest must pin five files, got {len(manifest.files)}")
    actual_prompt_hash = hashlib.sha256(manifest.query_prompt.encode("utf-8")).hexdigest()
    if manifest.query_prompt_utf8_sha256 != actual_prompt_hash:
        raise RuntimeError(
            "query prompt hash drift: "
            f"{manifest.query_prompt_utf8_sha256} != {actual_prompt_hash}"
        )
    return manifest


MODEL_MANIFEST = _build_manifest()
PROFILE_ID = MODEL_MANIFEST.profile_id


def manifest_json(manifest: ModelManifest = MODEL_MANIFEST) -> str:
    """Return canonical JSON for the identity represented by ``manifest``."""
    return canonical_json(manifest.identity_fields)


def manifest_digest(manifest: ModelManifest = MODEL_MANIFEST) -> str:
    """Return the full profile digest used by the install marker."""
    return manifest.profile_id
