"""Provider readiness + OpenAI-compatible /models probe for WebUI onboarding.

Network logic lives here so ``serve.py`` stays a thin route layer. Error
messages never contain the API key.
"""
from __future__ import annotations

import json
import socket
import time
from http.client import InvalidURL
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from thyca import __version__
from thyca.config import Config, ConfigError
from thyca.llm._http import redact

_PROBE_TIMEOUT_S = 10.0
_TEST_TIMEOUT_S = 20.0
_TEST_MESSAGE = "ping"


def _is_timeout_reason(reason: object) -> bool:
    """True khi URLError bọc socket timeout (urlopen bọc timeout thành
    URLError(reason=TimeoutError) thay vì ném TimeoutError trần)."""
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return "timed out" in str(reason or "").lower()


class ProviderProbeError(RuntimeError):
    """Provider /models probe failed (network, auth, or bad response)."""


def provider_ready(cfg: Config) -> bool:
    try:
        cfg.provider.api_key()
    except ConfigError:
        return False
    return True


def _map_network_error(exc: Exception, timeout: float, *, model: str | None = None) -> ProviderProbeError:
    """One mapping for every probe/test transport failure (key-free messages).

    ``model`` enables the 404 branch the config tests use; the /models probe
    passes none and keeps the generic HTTP message. HTTPError precedes
    URLError (it subclasses it), TimeoutError precedes the bare fallback."""
    if isinstance(exc, ProviderProbeError):
        return exc
    if isinstance(exc, (InvalidURL, TypeError, UnicodeError, ValueError)):
        return ProviderProbeError("baseUrl không hợp lệ")
    if isinstance(exc, HTTPError):
        if exc.code in (401, 403):
            return ProviderProbeError(f"API key bị từ chối (HTTP {exc.code})")
        if exc.code == 404 and model is not None:
            return ProviderProbeError(
                f"model {model!r} không có trên provider (HTTP 404)"
            )
        return ProviderProbeError(f"provider trả HTTP {exc.code}")
    if isinstance(exc, URLError):
        if _is_timeout_reason(exc.reason):
            return ProviderProbeError(
                f"provider quá thời gian phản hồi ({timeout:g}s)"
            )
        return ProviderProbeError("không kết nối được provider")
    if isinstance(exc, TimeoutError):
        return ProviderProbeError(
            f"provider quá thời gian phản hồi ({timeout:g}s)"
        )
    return ProviderProbeError("không kết nối được provider")


def _request_json(
    base_url: str,
    path: str,
    api_key: str,
    *,
    body: bytes | None,
    timeout: float,
    model: str | None = None,
) -> bytes:
    """One bearer-JSON request for the probe/tests: scheme check, URL join,
    transport, and the shared network-error mapping. ``body=None`` sends GET.
    The config tests pass ``model`` for the 404 branch and the blank-model
    guard; the scheme check stays first so both-bad inputs report the URL."""
    try:
        if not base_url.startswith(("http://", "https://")):
            raise ProviderProbeError("baseUrl phải bắt đầu bằng http:// hoặc https://")
        if model is not None and not model.strip():
            raise ProviderProbeError("cần model để test")
        url = base_url.rstrip("/") + path
        headers = {"Authorization": f"Bearer {api_key}", "User-Agent": f"thyca/{__version__}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        # Some gateways (e.g. commandcode) 403 requests without a User-Agent.
        request = Request(url, data=body, headers=headers)
    except ProviderProbeError:
        raise
    except Exception as exc:
        raise _map_network_error(exc, timeout, model=model) from exc
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise _map_network_error(exc, timeout, model=model) from exc


def validate_provider(
    base_url: str, api_key: str, *, timeout: float = _PROBE_TIMEOUT_S
) -> list[str]:
    """GET ``{base_url}/models`` with a bearer token; return sorted model ids."""
    body = _request_json(base_url, "/models", api_key, body=None, timeout=timeout)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProbeError("provider trả JSON không hợp lệ") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ProviderProbeError("provider trả schema /models không đúng")
    ids = {
        item.get("id")
        for item in payload["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }
    return sorted(ids)


def test_chat(
    base_url: str, api_key: str, model: str, *, timeout: float = _TEST_TIMEOUT_S
) -> dict:
    """POST one tiny ``/chat/completions`` turn to prove a saved config works.

    Returns ``{"model": <echo or requested>, "latency_ms": <int>}``.
    Raises :class:`ProviderProbeError` with a key-free message on any failure.
    """
    started = time.perf_counter()
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": _TEST_MESSAGE}],
            "stream": False,
        }
    ).encode("utf-8")
    raw = _request_json(
        base_url, "/chat/completions", api_key, body=body, timeout=timeout, model=model
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProbeError("provider trả JSON không hợp lệ") from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if (
        not isinstance(choices, list)
        or not choices
        or not isinstance(choices[0], dict)
        or not isinstance((choices[0].get("message") or {}).get("content"), str)
    ):
        raise ProviderProbeError("provider trả schema /chat/completions không đúng")
    echo = payload.get("model")
    return {
        "model": echo if isinstance(echo, str) and echo else model,
        "latency_ms": max(0, latency_ms),
    }


def test_responses_chat(
    base_url: str, api_key: str, model: str, *, timeout: float = _TEST_TIMEOUT_S
) -> dict:
    """POST one tiny ``/v1/responses`` turn to prove a responses provider works.

    Same contract as :func:`test_chat`; verifies the Responses wire shape
    (``input`` in, ``output_text`` out).
    """
    started = time.perf_counter()
    body = json.dumps(
        {
            "model": model,
            "input": [{"role": "user", "content": _TEST_MESSAGE}],
            "stream": False,
        }
    ).encode("utf-8")
    raw = _request_json(
        base_url, "/responses", api_key, body=body, timeout=timeout, model=model
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProbeError("provider trả JSON không hợp lệ") from exc
    if not isinstance(payload, dict):
        raise ProviderProbeError("provider trả JSON không hợp lệ")
    error = payload.get("error")
    if isinstance(error, dict) and error:
        detail = redact(str(error.get("message", "")), api_key)
        raise ProviderProbeError(f"provider trả lỗi: {detail}")
    output = payload.get("output")
    has_text = isinstance(output, list) and any(
        isinstance(item, dict)
        and item.get("type") == "message"
        and any(
            isinstance(part, dict) and part.get("type") == "output_text"
            for part in item.get("content") or []
        )
        for item in output
    )
    if not has_text:
        raise ProviderProbeError("provider trả schema /responses không đúng")
    echo = payload.get("model")
    return {
        "model": echo if isinstance(echo, str) and echo else model,
        "latency_ms": max(0, latency_ms),
    }


def test_provider_api(
    api: str, base_url: str, api_key: str, model: str, *, timeout: float = _TEST_TIMEOUT_S
) -> dict:
    """Dispatch the config test to the provider's wire API."""
    if api == "openai_responses":
        return test_responses_chat(base_url, api_key, model, timeout=timeout)
    return test_chat(base_url, api_key, model, timeout=timeout)


def apply_provider(
    cfg: Config, base_url: str, api_key: str, model: str
) -> Config:
    """Return a new Config with the onboarding provider values applied."""
    from dataclasses import replace as _replace

    if not model.strip():
        raise ProviderProbeError("provider.model must be non-empty")
    try:
        entry = _replace(
            cfg.providers[cfg.defaultProvider],
            baseUrl=base_url.strip(),
            apiKey=api_key or None,
        )
        providers = dict(cfg.providers)
        providers[cfg.defaultProvider] = entry
        return _replace(cfg, providers=providers, defaultModel=model.strip())
    except (ConfigError, KeyError) as exc:
        # Empty model / bad URL surface as probe errors, not raw ConfigError.
        raise ProviderProbeError(str(exc)) from exc