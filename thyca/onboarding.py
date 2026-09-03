"""Provider readiness + OpenAI-compatible /models probe for WebUI onboarding.

Network logic lives here so ``serve.py`` stays a thin route layer. Error
messages never contain the API key.
"""
from __future__ import annotations

import json
import socket
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from thyca.config import Config, ConfigError, ProviderCfg
from thyca import __version__

_PROBE_TIMEOUT_S = 10.0


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


def validate_provider(
    base_url: str, api_key: str, *, timeout: float = _PROBE_TIMEOUT_S
) -> list[str]:
    """GET ``{base_url}/models`` with a bearer token; return sorted model ids."""
    if not base_url.startswith(("http://", "https://")):
        raise ProviderProbeError(f"baseUrl phải bắt đầu bằng http:// hoặc https://: {base_url!r}")
    url = base_url.rstrip("/") + "/models"
    # Some gateways (e.g. commandcode) 403 requests without a User-Agent.
    request = Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": f"thyca/{__version__}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ProviderProbeError("API key bị từ chối (HTTP %d)" % exc.code) from exc
        raise ProviderProbeError(f"provider trả HTTP {exc.code}") from exc
    except URLError as exc:
        if _is_timeout_reason(exc.reason):
            raise ProviderProbeError(
                f"provider quá thời gian phản hồi ({timeout:g}s)"
            ) from exc
        raise ProviderProbeError(f"không kết nối được {base_url}: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ProviderProbeError(
            f"provider quá thời gian phản hồi ({timeout:g}s)"
        ) from exc
    except OSError as exc:
        raise ProviderProbeError(f"không kết nối được {base_url}") from exc
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


def apply_provider(
    cfg: Config, base_url: str, api_key: str, model: str
) -> Config:
    """Return a new Config with the onboarding provider values applied."""
    from dataclasses import replace as _replace

    try:
        provider = _replace(
            cfg.provider,
            baseUrl=base_url.strip(),
            model=model.strip(),
            apiKey=api_key if api_key else None,
        )
        return _replace(cfg, provider=provider)
    except ConfigError as exc:
        # Empty model / bad URL surface as probe errors, not raw ConfigError.
        raise ProviderProbeError(str(exc)) from exc