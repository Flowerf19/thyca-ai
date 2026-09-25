"""Shared HTTP retry/redaction/parse helpers for the provider connects."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

import httpx

from .llm_base import ChatReply, Connect, LLMError

if TYPE_CHECKING:
    from thyca.config import ProviderCfg

_RETRY_STATUS = {429, 500, 502, 503, 504}
_BODY_CAP = 500
_RETRY_AFTER_CAP_S = 5.0


def redact(text: str, secret: str) -> str:
    """Replace one secret with ``[redacted]``; the single redact for llm + onboarding."""
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


def cap(text: str, limit: int = _BODY_CAP) -> str:
    """Cap error text at ``limit`` chars; the single cap for llm errors."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def parse_json_bytes(raw: bytes, key: str) -> dict:
    """Decode one non-streaming provider body; the shared bytes error shape."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMError(redact(cap(raw.decode("utf-8", errors="replace")), key)) from exc
    if not isinstance(payload, dict):
        raise LLMError("provider response must be an object")
    return payload


async def iter_sse_data(
    response: httpx.Response, key: str, *, done: list[bool] | None = None
) -> AsyncIterator[dict]:
    """Yield parsed SSE data dicts; the shared skip/[DONE]/decode preamble.

    When ``done`` is given, a True is appended iff the stream ended with
    ``[DONE]`` (lets the chat path tell truncation from completion).
    """
    async for line in response.aiter_lines():
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].lstrip()
        if data == "[DONE]":
            if done is not None:
                done.append(True)
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMError(redact(cap(data), key)) from exc
        if not isinstance(chunk, dict):
            continue
        yield chunk


async def _sleep_retry_after(response: httpx.Response) -> None:
    raw = response.headers.get("Retry-After")
    delay = 0.0
    if raw:
        try:
            delay = min(max(float(raw), 0.0), _RETRY_AFTER_CAP_S)
        except ValueError:
            delay = 0.0
    if delay > 0:
        import asyncio

        await asyncio.sleep(delay)


class _DropParam(Exception):
    """Retry the request once without the dropped reasoning param."""


class BaseConnect(Connect):
    """Shared connect mechanics: client, retries, consume dispatch.

    Subclasses only build the payload (:meth:`chat`) and name the drop param
    plus the parse functions, so retry/timeout/error semantics cannot drift.
    """

    #: Payload key dropped + retried once on a 400 naming it.
    DROP_PARAM = ""
    #: Body substring that triggers the drop (empty = never drop).
    DROP_MARKER = ""

    def __init__(
        self,
        provider: ProviderCfg,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider = provider
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
        )
        self._retry_hook: Callable[[int, int], None] | None = None

    def set_retry_hook(self, hook: Callable[[int, int], None] | None) -> None:
        """Optional (attempt, max_attempts) callback for transient retries."""
        self._retry_hook = hook

    def _notify_retry(self, attempt: int, max_attempts: int) -> None:
        if self._retry_hook is None:
            return
        try:
            self._retry_hook(attempt, max_attempts)
        except Exception:
            pass

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None = None,
    ) -> ChatReply:
        # Exactly 3 transient attempts; each failure emits retry status
        # (1/3, 2/3, 3/3) before the final provider error.
        max_attempts = 3
        last_error: LLMError | None = None
        transient = 0
        dropped = False
        while True:
            try:
                async with self._client.stream(
                    "POST", url, json=payload, headers=headers
                ) as response:
                    reply = await self._consume(
                        response, payload, key, on_reasoning, on_content, dropped
                    )
            except _DropParam:
                payload = {k: v for k, v in payload.items() if k != self.DROP_PARAM}
                dropped = True
                continue
            except httpx.RequestError as exc:
                transient += 1
                if isinstance(exc, httpx.TimeoutException):
                    last_error = LLMError("provider timeout")
                else:
                    last_error = LLMError(redact(cap(str(exc)), key))
                self._notify_retry(transient, max_attempts)
                if transient >= max_attempts:
                    raise last_error from exc
                continue

            if isinstance(reply, LLMError):
                transient += 1
                last_error = reply
                self._notify_retry(transient, max_attempts)
                if transient >= max_attempts:
                    raise last_error
                continue
            return reply

    async def _consume(
        self,
        response: httpx.Response,
        payload: dict[str, Any],
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None,
        dropped: bool,
    ) -> ChatReply | LLMError:
        status = response.status_code
        if status == 400 and self.DROP_PARAM and self.DROP_PARAM in payload and not dropped:
            body = (await response.aread()).decode("utf-8", errors="replace")
            if self.DROP_MARKER and self.DROP_MARKER in body:
                raise _DropParam
            raise LLMError(f"provider HTTP 400: {redact(cap(body), key)}")
        if status in _RETRY_STATUS:
            await _sleep_retry_after(response)
            body = (await response.aread()).decode("utf-8", errors="replace")
            return LLMError(f"provider HTTP {status}: {redact(cap(body), key)}")
        if status >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")
            raise LLMError(f"provider HTTP {status}: {redact(cap(body), key)}")
        ctype = response.headers.get("content-type", "")
        if "event-stream" in ctype:
            return await self._read_sse(response, key, on_reasoning, on_content)
        raw = await response.aread()
        reply = self._parse_bytes(raw, key)
        self._emit_callbacks(reply, on_reasoning, on_content)
        return reply

    async def _read_sse(
        self,
        response: httpx.Response,
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None,
    ) -> ChatReply:
        raise NotImplementedError

    def _parse_bytes(self, raw: bytes, key: str) -> ChatReply:
        raise NotImplementedError

    @staticmethod
    def _emit_callbacks(
        reply: ChatReply,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None,
    ) -> None:
        """Non-streaming tail: replay the assembled text through the callbacks."""
        if on_reasoning and reply.reasoning:
            try:
                on_reasoning(reply.reasoning)
            except Exception:
                pass
        if on_content and reply.content:
            try:
                on_content(reply.content)
            except Exception:
                pass
