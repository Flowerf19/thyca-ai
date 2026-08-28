"""Onboarding probe — provider /models validation, no key leakage."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from thyca.config import Config, ConfigError, ProviderCfg
from thyca.onboarding import (
    ProviderProbeError,
    apply_provider,
    provider_ready,
    validate_provider,
)


def _models_server(payload: bytes, status: int = 200):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _probe(httpd, path: str = "/models") -> list[str]:
    url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
    return validate_provider(url, "sk-secret")


def _stop(httpd, thread) -> None:
    httpd.shutdown()
    thread.join(timeout=2)
    httpd.server_close()


def test_validate_provider_returns_sorted_ids() -> None:
    body = json.dumps({"data": [{"id": "b-model"}, {"id": "a-model"}, {"id": "a-model"}]})
    httpd, thread = _models_server(body.encode())
    try:
        assert _probe(httpd) == ["a-model", "b-model"]
    finally:
        _stop(httpd, thread)


def test_validate_provider_rejects_bad_schema() -> None:
    httpd, thread = _models_server(b'{"oops": []}')
    try:
        with pytest.raises(ProviderProbeError):
            _probe(httpd)
    finally:
        _stop(httpd, thread)


def test_validate_provider_rejects_non_json() -> None:
    httpd, thread = _models_server(b"<html>nope</html>")
    try:
        with pytest.raises(ProviderProbeError):
            _probe(httpd)
    finally:
        _stop(httpd, thread)


def test_validate_provider_auth_error_has_no_key() -> None:
    httpd, thread = _models_server(b'{"error": "sk-secret bad"}', status=401)
    try:
        with pytest.raises(ProviderProbeError) as excinfo:
            _probe(httpd)
        assert "sk-secret" not in str(excinfo.value)
        assert "401" in str(excinfo.value)
    finally:
        _stop(httpd, thread)


def test_validate_provider_connection_refused() -> None:
    with pytest.raises(ProviderProbeError):
        validate_provider("http://127.0.0.1:1", "sk-secret", timeout=1.0)


def test_validate_provider_bad_url() -> None:
    with pytest.raises(ProviderProbeError):
        validate_provider("not-a-url", "sk-secret", timeout=1.0)


def test_provider_ready_true_with_json_key(tmp_path) -> None:
    cfg = Config(provider=ProviderCfg(apiKey="sk-1"))
    assert provider_ready(cfg) is True


def test_provider_ready_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THYCA_TOKEN", raising=False)
    cfg = Config()
    assert provider_ready(cfg) is False


def test_apply_provider_sets_values_and_preserves_rest(tmp_path) -> None:
    cfg = Config()
    updated = apply_provider(cfg, "https://openrouter.ai/api/v1", "sk-new", "x/m")
    assert updated.provider.baseUrl == "https://openrouter.ai/api/v1"
    assert updated.provider.apiKey == "sk-new"
    assert updated.provider.model == "x/m"
    assert updated.provider.reasoningEffort == "high"
    assert updated.timeline == cfg.timeline
    assert updated.limits == cfg.limits
    # frozen input untouched
    assert cfg.provider.apiKey is None


def test_apply_provider_rejects_empty_model() -> None:
    with pytest.raises(ProviderProbeError):
        apply_provider(Config(), "https://x/v1", "sk", "  ")
