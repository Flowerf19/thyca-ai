from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from thyca.core.protocol import Message, ToolCall


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _extract_openai(raw: dict) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    prompt = _coerce_int(raw.get("prompt_tokens"))
    completion = _coerce_int(raw.get("completion_tokens"))
    total = _coerce_int(raw.get("total_tokens"))
    cached = None
    details = raw.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _coerce_int(details.get("cached_tokens"))
    reasoning = None
    c_details = raw.get("completion_tokens_details")
    if isinstance(c_details, dict):
        reasoning = _coerce_int(c_details.get("reasoning_tokens"))
    return prompt, cached, completion, total, reasoning


def _extract_responses(raw: dict) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    # Responses API shape (verified live): input/output counters.
    prompt = _coerce_int(raw.get("input_tokens"))
    completion = _coerce_int(raw.get("output_tokens"))
    total = _coerce_int(raw.get("total_tokens"))
    cached = None
    details = raw.get("input_tokens_details")
    if isinstance(details, dict):
        cached = _coerce_int(details.get("cached_tokens"))
    reasoning = None
    c_details = raw.get("output_tokens_details")
    if isinstance(c_details, dict):
        reasoning = _coerce_int(c_details.get("reasoning_tokens"))
    return prompt, cached, completion, total, reasoning


def _extract_generic(raw: dict) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    return (
        _coerce_int(raw.get("prompt_tokens")),
        _coerce_int(raw.get("cached_tokens")),
        _coerce_int(raw.get("completion_tokens")),
        _coerce_int(raw.get("total_tokens")),
        _coerce_int(raw.get("reasoning_tokens")),
    )


def normalize_usage(raw: dict | None, provider: str) -> dict | None:
    """Normalize provider usage to ``{prompt_tokens, cached_tokens, completion_tokens, total_tokens, reasoning_tokens?}``.

    Returns ``None`` when *raw* is missing or has no usable counters. ``cached_tokens``
    is always a subset of ``prompt_tokens`` (0 when the provider does not report it).
    """
    if not isinstance(raw, dict) or not raw:
        return None
    provider = provider.strip().lower()
    if provider in ("openai", "openai_chat", "openai_compat"):
        prompt, cached, completion, total, reasoning = _extract_openai(raw)
    elif provider == "openai_responses":
        prompt, cached, completion, total, reasoning = _extract_responses(raw)
    else:
        # OpenAI-only is deliberate: unknown providers fall through to the
        # generic snake_case counters, never to vendor-specific shapes.
        prompt, cached, completion, total, reasoning = _extract_generic(raw)
    if prompt is None and completion is None and total is None:
        return None
    if prompt is None and total is not None and completion is not None:
        prompt = total - completion
        if prompt < 0:
            prompt = None
    if completion is None and total is not None and prompt is not None:
        completion = total - prompt
        if completion < 0:
            completion = None
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    if cached is None and prompt is not None:
        cached = 0
    if cached is not None and prompt is not None and cached > prompt:
        cached = prompt
    out: dict = {}
    if prompt is not None:
        out["prompt_tokens"] = prompt
    if cached is not None and prompt is not None:
        out["cached_tokens"] = cached
    if completion is not None:
        out["completion_tokens"] = completion
    if total is not None:
        out["total_tokens"] = total
    if reasoning is not None:
        out["reasoning_tokens"] = reasoning
    return out or None


class LLMError(RuntimeError):
    """Provider HTTP or payload error. Must not contain API keys."""


@dataclass(frozen=True)
class ChatReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str = ""
    model: str | None = None
    reasoning: str | None = None
    reasoning_details: list[dict] | None = None


class Connect(ABC):
    """Product: one chat turn against a provider."""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> ChatReply:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
