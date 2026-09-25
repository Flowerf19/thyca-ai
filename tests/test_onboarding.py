"""Onboarding probe — provider /models validation, no key leakage."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from thyca.config import Config, ProviderEntry
from thyca.app.onboarding import (
    ProviderProbeError,
    apply_provider,
    provider_ready,
    validate_provider,
)
from thyca.app.onboarding import test_chat as probe_test_chat
from thyca.app.onboarding import test_provider_api as probe_dispatch
from thyca.app.onboarding import test_responses_chat as probe_test_responses


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


def test_validate_provider_oserror_does_not_echo_query_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        raise OSError("connection failed")

    monkeypatch.setattr("thyca.app.onboarding.urlopen", fail)
    base_url = "https://provider.example/v1?token=query-secret"
    with pytest.raises(ProviderProbeError) as excinfo:
        validate_provider(base_url, "sk-secret")
    assert str(excinfo.value) == "không kết nối được provider"
    assert "query-secret" not in str(excinfo.value)
    assert base_url not in str(excinfo.value)


def test_validate_provider_bad_url() -> None:
    with pytest.raises(ProviderProbeError):
        validate_provider("not-a-url", "sk-secret", timeout=1.0)


def test_provider_ready_true_with_json_key(tmp_path) -> None:
    cfg = Config(providers={"default": ProviderEntry(apiKey="sk-1")})
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


def _chat_server(payload: bytes, status: int = 200, seen: list | None = None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            if seen is not None:
                seen.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": json.loads(raw.decode("utf-8")),
                    }
                )
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


def _chat_payload(model: str = "probe-model") -> bytes:
    return json.dumps(
        {
            "model": model,
            "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
        }
    ).encode("utf-8")


def test_chat_ok_returns_model_and_latency() -> None:
    seen: list = []
    httpd, thread = _chat_server(_chat_payload(), seen=seen)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        result = probe_test_chat(base, "sk-secret", "probe-model")
    finally:
        _stop(httpd, thread)
    assert result["model"] == "probe-model"
    assert result["latency_ms"] >= 0
    assert seen[0]["path"] == "/chat/completions"
    assert seen[0]["auth"] == "Bearer sk-secret"
    assert seen[0]["body"]["stream"] is False
    assert seen[0]["body"]["messages"] == [{"role": "user", "content": "ping"}]


def test_chat_404_names_missing_model() -> None:
    httpd, thread = _chat_server(b'{"error": "nope"}', status=404)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(ProviderProbeError, match=r"ghost.*HTTP 404"):
            probe_test_chat(base, "sk-secret", "ghost")
    finally:
        _stop(httpd, thread)


def test_chat_auth_error_has_no_key() -> None:
    httpd, thread = _chat_server(b'{"error": "denied"}', status=401)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(ProviderProbeError) as excinfo:
            probe_test_chat(base, "sk-secret", "probe-model")
    finally:
        _stop(httpd, thread)
    assert "sk-secret" not in str(excinfo.value)
    assert "401" in str(excinfo.value)


def test_chat_rejects_bad_schema() -> None:
    httpd, thread = _chat_server(b'{"choices": []}')
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(ProviderProbeError, match="schema"):
            probe_test_chat(base, "sk-secret", "probe-model")
    finally:
        _stop(httpd, thread)


def _responses_payload(model: str = "probe-model") -> bytes:
    return json.dumps(
        {
            "model": model,
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "pong"}],
                }
            ],
        }
    ).encode("utf-8")


def test_responses_ok_returns_model_and_latency() -> None:
    seen: list = []
    httpd, thread = _chat_server(_responses_payload(), seen=seen)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        result = probe_test_responses(base, "sk-secret", "probe-model")
    finally:
        _stop(httpd, thread)
    assert result["model"] == "probe-model"
    assert result["latency_ms"] >= 0
    assert seen[0]["path"] == "/responses"
    assert seen[0]["auth"] == "Bearer sk-secret"
    assert seen[0]["body"]["stream"] is False
    assert seen[0]["body"]["input"] == [{"role": "user", "content": "ping"}]


def test_responses_404_names_missing_model() -> None:
    httpd, thread = _chat_server(b'{"error": "nope"}', status=404)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(ProviderProbeError, match=r"ghost.*HTTP 404"):
            probe_test_responses(base, "sk-secret", "ghost")
    finally:
        _stop(httpd, thread)


def test_responses_auth_error_has_no_key() -> None:
    httpd, thread = _chat_server(b'{"error": "denied"}', status=401)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(ProviderProbeError) as excinfo:
            probe_test_responses(base, "sk-secret", "probe-model")
    finally:
        _stop(httpd, thread)
    assert "sk-secret" not in str(excinfo.value)
    assert "401" in str(excinfo.value)


def test_responses_rejects_bad_schema() -> None:
    httpd, thread = _chat_server(b'{"output": []}')
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(ProviderProbeError, match="schema"):
            probe_test_responses(base, "sk-secret", "probe-model")
    finally:
        _stop(httpd, thread)


def test_responses_rejects_non_dict_json() -> None:
    # Regression: the non-dict branch once chained `from exc` out of scope
    # (NameError) instead of raising a clean probe error.
    httpd, thread = _chat_server(b'["not", "a", "dict"]')
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(ProviderProbeError, match="JSON không hợp lệ"):
            probe_test_responses(base, "sk-secret", "probe-model")
    finally:
        _stop(httpd, thread)


def test_responses_error_message_redacts_key() -> None:
    httpd, thread = _chat_server(
        b'{"error": {"message": "bad key sk-secret rejected"}}'
    )
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(ProviderProbeError) as excinfo:
            probe_test_responses(base, "sk-secret", "probe-model")
    finally:
        _stop(httpd, thread)
    assert "sk-secret" not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)


def test_provider_api_dispatches_to_wire_api() -> None:
    seen: list = []
    httpd, thread = _chat_server(_chat_payload("chat-model"), seen=seen)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        result = probe_dispatch("openai_chat", base, "sk", "chat-model")
        assert result["model"] == "chat-model"
        assert seen[-1]["path"] == "/chat/completions"
        # Unknown kinds fall back to chat completions.
        probe_dispatch("bogus", base, "sk", "chat-model")
        assert seen[-1]["path"] == "/chat/completions"
    finally:
        _stop(httpd, thread)
    seen_resp: list = []
    rhttpd, rthread = _chat_server(_responses_payload("resp-model"), seen=seen_resp)
    try:
        rbase = f"http://127.0.0.1:{rhttpd.server_address[1]}"
        result = probe_dispatch("openai_responses", rbase, "sk", "resp-model")
        assert result["model"] == "resp-model"
        assert seen_resp[-1]["path"] == "/responses"
    finally:
        _stop(rhttpd, rthread)


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import pytest

def test_x22_network_error_mapping_matrix() -> None:
    import socket
    from http.client import HTTPException
    from urllib.error import HTTPError, URLError

    from thyca.app.onboarding import ProviderProbeError, _map_network_error

    def msg(exc: Exception, **kwargs) -> str:
        mapped = _map_network_error(exc, 7.0, **kwargs)
        assert isinstance(mapped, ProviderProbeError)
        return str(mapped)

    assert "bd" in msg(ProviderProbeError("bd"))
    assert msg(ValueError("x")) == "baseUrl không hợp lệ"
    http401 = HTTPError("u", 401, "x", {}, None)
    assert msg(http401) == "API key bị từ chối (HTTP 401)"
    http404 = HTTPError("u", 404, "x", {}, None)
    assert msg(http404) == "provider trả HTTP 404"
    assert msg(http404, model="m") == "model 'm' không có trên provider (HTTP 404)"
    assert msg(HTTPError("u", 500, "x", {}, None)) == "provider trả HTTP 500"
    assert msg(URLError(TimeoutError())) == "provider quá thời gian phản hồi (7s)"
    assert msg(URLError(socket.timeout())) == "provider quá thời gian phản hồi (7s)"
    assert msg(URLError("conn refused")) == "không kết nối được provider"
    assert msg(TimeoutError()) == "provider quá thời gian phản hồi (7s)"
    assert msg(OSError("down")) == "không kết nối được provider"
    assert msg(HTTPException()) == "không kết nối được provider"
    assert msg(RuntimeError("weird")) == "không kết nối được provider"


def test_x22_scheme_check_stays_first() -> None:
    from thyca.app.onboarding import ProviderProbeError, _request_json

    with pytest.raises(ProviderProbeError, match="baseUrl phải bắt đầu"):
        _request_json("ftp://x", "/models", "k", body=None, timeout=1.0, model="  ")
    with pytest.raises(ProviderProbeError, match="cần model để test"):
        _request_json("https://x", "/models", "k", body=None, timeout=1.0, model="  ")
