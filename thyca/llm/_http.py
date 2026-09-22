"""Shared HTTP retry/redaction helpers for the provider connects."""

from __future__ import annotations

import httpx

_RETRY_STATUS = {429, 500, 502, 503, 504}
_BODY_CAP = 500
_RETRY_AFTER_CAP_S = 5.0


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


def _cap(text: str) -> str:
    if len(text) <= _BODY_CAP:
        return text
    return text[:_BODY_CAP] + "…"


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
