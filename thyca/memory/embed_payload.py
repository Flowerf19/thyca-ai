"""Payload policy for the locked local embedding input version."""
from __future__ import annotations

from thyca.memory.embed_manifest import QUERY_PROMPT


class EmbeddingInputError(ValueError):
    """An embedding payload cannot satisfy the provider input contract."""


def document_payload(embed_text: str) -> str:
    """Return the already-built chunk payload without changing or prompting it."""
    if not isinstance(embed_text, str):
        raise EmbeddingInputError("document embed_text must be a string")
    return embed_text


def query_payload(query: str) -> str:
    """Build the prompted query payload; whitespace-only queries are invalid."""
    if not isinstance(query, str):
        raise EmbeddingInputError("query must be a string")
    stripped = query.strip()
    if not stripped:
        raise EmbeddingInputError("query must not be empty")
    return QUERY_PROMPT + stripped
