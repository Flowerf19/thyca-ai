"""Provider readiness + OpenAI-compatible /models probe for WebUI onboarding.

Network logic lives here so ``serve.py`` stays a thin route layer. Error
messages never contain the API key.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import time
from http.client import InvalidURL
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx

from thyca import __version__
from thyca.config import Config, ConfigError, ProviderCfg
from thyca.core.protocol import Message
from thyca.llm.llm_base import ChatReply, Connect, LLMError
from thyca.llm.llm_factory import ConnectFactory

_PROBE_TIMEOUT_S = 10.0
_TEST_TIMEOUT_S = 20.0
_TEST_MESSAGE = "ping"
_HTTP_STATUS_RE = re.compile(r"provider HTTP (\d+)")


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


async def _probe_turn(connect: Connect, timeout: float) -> ChatReply:
    """One ping turn through the real wire; the client always closes here."""
    try:
        messages = [Message(role="user", content=_TEST_MESSAGE)]
        return await asyncio.wait_for(connect.chat(messages), timeout)
    finally:
        await connect.aclose()


def _map_connect_error(
    exc: Exception,
    *,
    model: str,
    timeout: float,
    schema_error: str,
    non_dict_error: str,
) -> ProviderProbeError:
    """Connect failures to the frozen probe messages (key-free, no new strings).

    Transport causes stay visible via ``__cause__`` (``_request`` chains the
    httpx error), so connection failures and undecodable bodies map exactly
    instead of falling through to the schema message. ``non_dict_error`` keeps
    the legacy split: responses treated a non-dict body as bad JSON while
    chat treated it as a bad schema.
    """
    if isinstance(exc, ProviderProbeError):
        return exc
    if isinstance(exc, (InvalidURL, TypeError, UnicodeError, ValueError)):
        return ProviderProbeError("baseUrl không hợp lệ")
    if isinstance(exc, TimeoutError):
        return ProviderProbeError(
            f"provider quá thời gian phản hồi ({timeout:g}s)"
        )
    if isinstance(exc, LLMError):
        message = str(exc)
        status = _HTTP_STATUS_RE.match(message)
        if status:
            code = status.group(1)
            if code in ("401", "403"):
                return ProviderProbeError(f"API key bị từ chối (HTTP {code})")
            if code == "404":
                return ProviderProbeError(
                    f"model {model!r} không có trên provider (HTTP 404)"
                )
            return ProviderProbeError(f"provider trả HTTP {code}")
        if message == "provider timeout":
            return ProviderProbeError(
                f"provider quá thời gian phản hồi ({timeout:g}s)"
            )
        cause = exc.__cause__
        if isinstance(cause, httpx.TimeoutException):
            return ProviderProbeError(
                f"provider quá thời gian phản hồi ({timeout:g}s)"
            )
        if isinstance(cause, httpx.RequestError):
            return ProviderProbeError("không kết nối được provider")
        if isinstance(cause, (json.JSONDecodeError, UnicodeDecodeError)):
            return ProviderProbeError("provider trả JSON không hợp lệ")
        if message.startswith("provider error: "):
            return ProviderProbeError(
                f"provider trả lỗi: {message[len('provider error: '):]}"
            )
        if message == "provider response must be an object":
            return ProviderProbeError(non_dict_error)
        return ProviderProbeError(schema_error)
    return ProviderProbeError("không kết nối được provider")


def _test_via_connect(
    kind: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    *,
    schema_error: str,
    non_dict_error: str,
) -> dict:
    """One ping turn through the real ``Connect.chat()`` wire.

    Same ``{"model", "latency_ms"}`` contract and guard messages as the old
    urllib probe, but the request is byte-identical to a real turn (stream +
    reasoning effort), so "test OK but chat fails" can no longer hide.
    """
    if not base_url.startswith(("http://", "https://")):
        raise ProviderProbeError("baseUrl phải bắt đầu bằng http:// hoặc https://")
    if not model.strip():
        raise ProviderProbeError("cần model để test")
    try:
        provider = ProviderCfg(baseUrl=base_url, apiKey=api_key, model=model)
    except ConfigError as exc:
        raise ProviderProbeError("không kết nối được provider") from exc
    connect = ConnectFactory.create(kind, provider=provider)
    started = time.perf_counter()
    try:
        reply = asyncio.run(_probe_turn(connect, timeout))
    except Exception as exc:
        raise _map_connect_error(
            exc,
            model=model,
            timeout=timeout,
            schema_error=schema_error,
            non_dict_error=non_dict_error,
        ) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {"model": reply.model or model, "latency_ms": max(0, latency_ms)}


def test_chat(
    base_url: str, api_key: str, model: str, *, timeout: float = _TEST_TIMEOUT_S
) -> dict:
    """POST one tiny ``/chat/completions`` turn to prove a saved config works.

    Returns ``{"model": <echo or requested>, "latency_ms": <int>}``.
    Raises :class:`ProviderProbeError` with a key-free message on any failure.
    """
    return _test_via_connect(
        "openai_chat",
        base_url,
        api_key,
        model,
        timeout,
        schema_error="provider trả schema /chat/completions không đúng",
        non_dict_error="provider trả schema /chat/completions không đúng",
    )


def test_responses_chat(
    base_url: str, api_key: str, model: str, *, timeout: float = _TEST_TIMEOUT_S
) -> dict:
    """POST one tiny ``/v1/responses`` turn to prove a responses provider works.

    Same contract as :func:`test_chat`; verifies the Responses wire shape
    (``input`` in, ``output_text`` out).
    """
    return _test_via_connect(
        "openai_responses",
        base_url,
        api_key,
        model,
        timeout,
        schema_error="provider trả schema /responses không đúng",
        non_dict_error="provider trả JSON không hợp lệ",
    )


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