"""Lazy local ONNX embedding provider. Model identity lives in the manifest."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import numpy as np

from thyca.memory.embed import Embedder
from thyca.memory.embed_manifest import MODEL_MANIFEST, ModelManifest, manifest_digest
from thyca.memory.embed_payload import document_payload, query_payload

MARKER_NAME = ".installed.json"
NORM_TOLERANCE = 1e-3


class OnnxEmbeddingError(RuntimeError):
    """The local ONNX provider could not load or produce a valid vector."""


class ModelNotInstalledError(OnnxEmbeddingError):
    """The requested model directory is absent or not an installed profile."""


class EmbeddingOutputError(OnnxEmbeddingError):
    """The tokenizer or graph returned an invalid embedding output."""


class OnnxEmbedder:
    """One lazy, process-local, thread-safe ONNX embedding session."""

    def __init__(self, model_dir: Path, *, manifest: ModelManifest = MODEL_MANIFEST) -> None:
        self.model_dir = Path(model_dir)
        self.manifest = manifest
        self.profile_id = manifest.profile_id
        self.dimension = manifest.dimensions
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._failure: OnnxEmbeddingError | None = None
        self._load_lock = threading.Lock()

    def health(self) -> bool:
        """Return install health without importing or loading ONNX."""
        return _marker_ok(self.model_dir, self.manifest)

    def load_error(self) -> str | None:
        """Return the sticky load error, if one has occurred."""
        return None if self._failure is None else str(self._failure)

    @classmethod
    def is_installed(
        cls, model_dir: Path, *, manifest: ModelManifest = MODEL_MANIFEST
    ) -> bool:
        """Check the install marker without loading tokenizer or ONNX."""
        return _marker_ok(Path(model_dir), manifest)

    def embed_query(self, text: str) -> list[float]:
        """Embed one prompted query."""
        return self._embed([query_payload(text)])[0]

    def embed_docs(self, texts: list[str]) -> list[list[float] | None]:
        """Embed documents, preserving a ``None`` slot for a bad item."""
        payloads: list[str] = []
        slots: list[int] = []
        results: list[list[float] | None] = [None] * len(texts)
        for index, text in enumerate(texts):
            try:
                payloads.append(document_payload(text))
            except Exception:
                continue
            slots.append(index)

        if not payloads:
            return results
        outputs = self._run(payloads)
        if len(outputs) != len(slots):
            raise EmbeddingOutputError(
                f"expected {len(slots)} embedding rows, got {len(outputs)}"
            )
        for index, output in zip(slots, outputs, strict=True):
            try:
                results[index] = _validate_vector(output, self.dimension)
            except Exception:
                results[index] = None
        return results

    def _ensure_loaded(self) -> tuple[Any, Any]:
        if self._session is not None and self._tokenizer is not None:
            return self._tokenizer, self._session
        if self._failure is not None:
            raise self._failure
        with self._load_lock:
            if self._session is None and self._failure is None:
                self._load()
            if self._failure is not None:
                raise self._failure
            return self._tokenizer, self._session

    def _load(self) -> None:
        if not _marker_ok(self.model_dir, self.manifest):
            self._record_failure(
                ModelNotInstalledError(
                    "embedding model profile is not installed or has an invalid marker"
                )
            )
        try:
            from tokenizers import Tokenizer
            import onnxruntime as ort

            tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
            graph = next(
                name
                for name, _ in self.manifest.files
                if name.endswith(".onnx") and not name.endswith(".onnx_data")
            )
            session = ort.InferenceSession(
                str(self.model_dir / graph), providers=["CPUExecutionProvider"]
            )
            self._assert_graph_dimension(session)
        except OnnxEmbeddingError as exc:
            self._record_failure(exc)
        except Exception as exc:
            self._record_failure(OnnxEmbeddingError(f"embedding model load failed: {exc}"))
        self._tokenizer = tokenizer
        self._session = session

    def _record_failure(self, error: OnnxEmbeddingError) -> None:
        self._failure = error
        raise error

    def _assert_graph_dimension(self, session: Any) -> None:
        outputs = session.get_outputs()
        output = next((item for item in outputs if item.name == "sentence_embedding"), None)
        if output is None:
            names = [item.name for item in outputs]
            raise OnnxEmbeddingError(
                f"sentence_embedding output missing from graph; got {names}"
            )
        shape = list(output.shape)
        dimension = shape[1] if len(shape) == 2 else None
        if dimension != self.dimension:
            raise OnnxEmbeddingError(
                f"graph sentence_embedding dim {dimension} != manifest {self.dimension}"
            )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        outputs = self._run(texts)
        if not bool(np.all(np.isfinite(outputs))):
            raise EmbeddingOutputError("embedding output contains non-finite values")
        return [_validate_vector(output, self.dimension) for output in outputs]

    def _run(self, texts: list[str]) -> np.ndarray:
        tokenizer, session = self._ensure_loaded()
        try:
            encodings = tokenizer.encode_batch(texts)
            max_len = max((len(item.ids) for item in encodings), default=0)
            feed: dict[str, np.ndarray] = {
                "input_ids": _pad([item.ids for item in encodings], max_len)
            }
            input_names = {item.name for item in session.get_inputs()}
            if "attention_mask" in input_names:
                feed["attention_mask"] = _pad(
                    [item.attention_mask for item in encodings], max_len
                )
            if "token_type_ids" in input_names:
                feed["token_type_ids"] = _pad(
                    [item.type_ids for item in encodings], max_len
                )
            outputs = session.run(["sentence_embedding"], feed)[0]
        except OnnxEmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingOutputError(f"embedding run failed: {exc}") from exc
        return _validate_batch_shape(outputs, self.dimension)


def _marker_ok(model_dir: Path, manifest: ModelManifest) -> bool:
    try:
        marker = json.loads((model_dir / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return marker.get("manifest_digest") == manifest_digest(manifest)


def _pad(values: list[list[int]], length: int) -> np.ndarray:
    return np.asarray([value + [0] * (length - len(value)) for value in values], dtype=np.int64)


def _validate_batch_shape(outputs: object, dimension: int) -> np.ndarray:
    if not isinstance(outputs, np.ndarray):
        raise EmbeddingOutputError(
            f"expected numpy output, got {type(outputs).__name__}"
        )
    if outputs.ndim != 2 or outputs.shape[1] != dimension:
        raise EmbeddingOutputError(f"expected [batch, {dimension}], got {outputs.shape}")
    return outputs


def _validate_vector(values: object, dimension: int) -> list[float]:
    if not isinstance(values, np.ndarray):
        values = np.asarray(values)
    if values.dtype != np.float32 or values.shape != (dimension,):
        raise EmbeddingOutputError(f"expected FLOAT32 vector [{dimension}], got {values.shape}")
    if not bool(np.all(np.isfinite(values))):
        raise EmbeddingOutputError("embedding vector contains non-finite values")
    norm = float(np.linalg.norm(values))
    if not np.isfinite(norm) or abs(norm - 1.0) > NORM_TOLERANCE:
        raise EmbeddingOutputError(f"embedding vector is not unit norm: {norm}")
    return values.tolist()


def default_model_dir() -> Path:
    """Return the revision-scoped default directory for the locked local model."""
    return Path.home() / ".thyca" / "models" / MODEL_MANIFEST.model / MODEL_MANIFEST.revision


def resolve_embedder(
    embedding: object | None = None,
    *,
    thyca_dir: Path | None = None,
    model_dir: Path | None = None,
) -> Embedder | None:
    """Resolve the configured local provider without loading its model."""
    provider = getattr(embedding, "provider", "local") if embedding is not None else "local"
    model = getattr(embedding, "model", MODEL_MANIFEST.model) if embedding is not None else MODEL_MANIFEST.model
    if provider != "local" or model != MODEL_MANIFEST.model:
        return None
    directory = Path(model_dir) if model_dir is not None else (
        Path(thyca_dir) / "models" / model / MODEL_MANIFEST.revision
        if thyca_dir
        else default_model_dir()
    )
    if not OnnxEmbedder.is_installed(directory):
        return None
    return OnnxEmbedder(directory)
