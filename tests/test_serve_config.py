"""Config schema + settings HTTP endpoints — settings-webui."""
from __future__ import annotations

import json
import threading
from http.client import BadStatusLine
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from thyca import __version__
from thyca.config import default_config, load, save
from thyca.config import config_schema
from thyca.serve import default_webui, make_server
from thyca.tools.memory import MemoryFacade

WEBUI = default_webui()


def _start(tmp_path: Path):
    save(default_config(), tmp_path / "config.json")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(
        host="127.0.0.1",
        port=0,
        webui=WEBUI,
        facade=facade,
        config_file=tmp_path / "config.json",
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd, thread) -> None:
    httpd.shutdown()
    thread.join(timeout=2)
    httpd.server_close()


def _url(httpd, path: str) -> str:
    port = httpd.server_address[1]
    return f"http://127.0.0.1:{port}{path}"


def _call(
    httpd, path: str, *, method: str = "GET", data: dict | None = None
) -> tuple[int, dict]:
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = Request(_url(httpd, path), data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_schema_covers_all_scalar_sections() -> None:
    schema = config_schema()
    keys = [section["key"] for section in schema["sections"]]
    # Pricing renders per-model in the UI, not as a schema section.
    assert keys == ["provider", "limits", "timeline"]
    provider = {f["key"]: f for f in schema["sections"][0]["fields"]}
    assert provider["provider.reasoningEffort"]["choices"] == ["low", "high", "max"]
    assert provider["provider.reasoningEffort"]["default"] == "high"
    assert provider["provider.apiKey"]["secret"] is True
    assert "secret" not in provider["provider.apiKeyEnv"]
    limits = {f["key"]: f for f in schema["sections"][1]["fields"]}
    assert limits["limits.loopMax"]["min"] == 1
    assert limits["limits.loopMax"]["max"] == 200


def test_schema_includes_new_fields_without_labels() -> None:
    # Any new dataclass field must surface even without a label entry.
    schema = config_schema()
    limits = {f["key"] for f in schema["sections"][1]["fields"]}
    assert limits == {"limits.loopMax", "limits.hotTailKB", "limits.contextTokens"}
    # timezone follows the host system; apiKeyEnv is plumbing, not user-facing.
    # Both stay in the config file, the panel just skips them.
    timeline = {f["key"]: f.get("hidden") for f in schema["sections"][2]["fields"]}
    assert timeline == {"timeline.timezone": True}
    provider = {f["key"]: f for f in schema["sections"][0]["fields"]}
    assert provider["provider.apiKeyEnv"].get("hidden") is True


def test_status_reflects_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("THYCA_TOKEN", raising=False)
    httpd, thread = _start(tmp_path)
    try:
        status, body = _call(httpd, "/api/config/status")
        assert status == 200 and body == {"ready": False, "version": __version__}
    finally:
        _stop(httpd, thread)


def test_config_get_masks_api_key(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        status, body = _call(httpd, "/api/config")
        assert status == 200
        assert body["values"]["providers"]["default"]["apiKey"] == ""
        assert body["values"]["defaultModel"] == "gpt-4o-mini"
        assert body["meta"] == {"hasApiKey": False, "providers": {"default": False}}
        assert body["schema"]["sections"][0]["key"] == "provider"
    finally:
        _stop(httpd, thread)


def test_config_post_saves_and_keeps_key(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"]["default"]["apiKey"] = "sk-live-123"
        values["defaultModel"] = "gpt-5.6-luna"
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200 and body == {"ok": True, "ready": True}
        saved = load(tmp_path / "config.json")
        assert saved.providers["default"].apiKey == "sk-live-123"
        assert saved.defaultModel == "gpt-5.6-luna"
    finally:
        _stop(httpd, thread)


def test_config_post_empty_key_keeps_old(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"]["default"]["apiKey"] = "sk-first"
        _, _ = _call(httpd, "/api/config", method="POST", data=values)
        _, got2 = _call(httpd, "/api/config")
        values2 = got2["values"]
        values2["defaultModel"] = "m-2"
        values2["providers"]["default"]["apiKey"] = ""
        status, body = _call(httpd, "/api/config", method="POST", data=values2)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert saved.providers["default"].apiKey == "sk-first"
        assert saved.defaultModel == "m-2"
    finally:
        _stop(httpd, thread)


def test_config_post_invalid_limit_422(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["limits"]["loopMax"] = 9999
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 422
        assert "loopMax" in body["error"]
    finally:
        _stop(httpd, thread)


def test_config_post_models_roundtrip(tmp_path: Path) -> None:
    """Models with per-model baseUrl and prices persist and come back."""
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["models"] = {
            "Qwen/Qwen3.8-Flash": {"baseUrl": "", "input": 0.1, "cache": 0.01, "output": 0.4},
            "gpt-x@other": {"baseUrl": "https://other.api/v1", "input": 1.0, "cache": 0.1, "output": 2.0},
        }
        # UI keeps legacy pricing in sync with models; server stores both.
        values["pricing"] = {
            "Qwen/Qwen3.8-Flash": {"input": 0.1, "cache": 0.01, "output": 0.4},
            "gpt-x@other": {"input": 1.0, "cache": 0.1, "output": 2.0},
        }
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert saved.models["gpt-x@other"].baseUrl == "https://other.api/v1"
        assert saved.models["Qwen/Qwen3.8-Flash"].output == 0.4
        # Legacy pricing stays in sync so older consumers keep working.
        assert saved.pricing["Qwen/Qwen3.8-Flash"].output == 0.4
        _, got2 = _call(httpd, "/api/config")
        assert got2["values"]["models"]["gpt-x@other"]["baseUrl"] == "https://other.api/v1"
    finally:
        _stop(httpd, thread)


def test_config_post_last_model_keep_default(tmp_path: Path) -> None:
    """Deleting the default model keeps provider.model valid (never empty)."""
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["models"] = {"only-model": {"input": 0, "cache": 0, "output": 0}}
        values["defaultModel"] = "only-model"
        _, _ = _call(httpd, "/api/config", method="POST", data=values)
        # UI keeps the deleted default as fallback — server must accept it.
        values2 = got["values"]
        values2["models"] = {}
        values2["defaultModel"] = "only-model"
        status, body = _call(httpd, "/api/config", method="POST", data=values2)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert saved.defaultModel == "only-model"
        assert saved.models == {}
    finally:
        _stop(httpd, thread)


def test_verify_with_saved_key_no_leak(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        status, body = _call(
            httpd,
            "/api/onboarding/verify",
            method="POST",
            data={"baseUrl": "http://127.0.0.1:1/?apiKey=url-secret", "apiKey": "sk-x"},
        )
        assert status == 422
        assert "sk-x" not in body["error"]
        assert "url-secret" not in body["error"]
        assert "127.0.0.1" not in body["error"]
        # no apiKey → uses stored (empty) → 422 missing key
        status2, body2 = _call(
            httpd,
            "/api/onboarding/verify",
            method="POST",
            data={"baseUrl": "http://127.0.0.1:1"},
        )
        assert status2 == 422
        assert "key" in body2["error"].lower()
    finally:
        _stop(httpd, thread)


@pytest.mark.parametrize("bad_suffix", ["\x00", "\udcff"])
def test_verify_malformed_url_does_not_echo_url_secret(
    tmp_path: Path, bad_suffix: str
) -> None:
    httpd, thread = _start(tmp_path)
    try:
        base_url = "https://provider.example/v1?token=url-secret" + bad_suffix
        status, body = _call(
            httpd,
            "/api/onboarding/verify",
            method="POST",
            data={"baseUrl": base_url, "apiKey": "sk-x"},
        )
        assert status == 422
        assert body["error"] == "baseUrl không hợp lệ"
        assert "url-secret" not in body["error"]
        assert "provider.example" not in body["error"]
    finally:
        _stop(httpd, thread)


def test_verify_request_exception_does_not_echo_url_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url = "https://provider.example/v1?token=url-secret"

    def fail(*args, **kwargs):
        raise BadStatusLine(f"{base_url} sk-secret")

    monkeypatch.setattr("thyca.app.onboarding.urlopen", fail)
    httpd, thread = _start(tmp_path)
    try:
        status, body = _call(
            httpd,
            "/api/onboarding/verify",
            method="POST",
            data={"baseUrl": base_url, "apiKey": "sk-secret"},
        )
        assert status == 422
        assert body["error"] == "không kết nối được provider"
        assert base_url not in body["error"]
        assert "url-secret" not in body["error"]
        assert "sk-secret" not in body["error"]
    finally:
        _stop(httpd, thread)


def test_verify_success_returns_models(tmp_path: Path) -> None:
    calls: list[str] = []

    class Handler:
        pass

    # local stub server for /models
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    payload = json.dumps({"data": [{"id": "m-1"}]}).encode()

    class ModelsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            calls.append(self.headers.get("Authorization", ""))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    models = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
    mthread = threading.Thread(target=models.serve_forever, daemon=True)
    mthread.start()
    httpd, thread = _start(tmp_path)
    try:
        base = f"http://127.0.0.1:{models.server_address[1]}"
        status, body = _call(
            httpd,
            "/api/onboarding/verify",
            method="POST",
            data={"baseUrl": base, "apiKey": "sk-live"},
        )
        assert status == 200 and body == {"models": ["m-1"], "apiKeyOk": True}
        assert calls == ["Bearer sk-live"]
    finally:
        _stop(httpd, thread)
        models.shutdown()
        mthread.join(timeout=2)
        models.server_close()


def _chat_stub(
    payload: bytes,
    status: int = 200,
    calls: list | None = None,
    paths: list | None = None,
):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            if calls is not None:
                calls.append(self.headers.get("Authorization"))
            if paths is not None:
                paths.append(self.path)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_config_post_two_providers_keys_stay_isolated(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"] = {
            "default": {
                "baseUrl": "https://a.example/v1",
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-a",
                "reasoningEffort": "high",
            },
            "second": {
                "baseUrl": "https://b.example/v1",
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-b",
                "reasoningEffort": "low",
            },
        }
        values["models"] = {
            "model-b": {"provider": "second", "input": 0, "cache": 0, "output": 0},
        }
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert saved.providers["second"].api_key() == "sk-b"
        assert saved.effective_provider_for("model-b").baseUrl == "https://b.example/v1"
        # secrets split: keys land in auth.json, never config.json
        auth_raw = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
        assert auth_raw["providers"]["second"]["apiKey"] == "sk-b"
        conf_text = (tmp_path / "config.json").read_text(encoding="utf-8")
        assert "sk-a" not in conf_text and "sk-b" not in conf_text
        # masked re-post keeps each provider's own key (never crosses)
        _, got2 = _call(httpd, "/api/config")
        assert got2["values"]["providers"]["second"]["apiKey"] == ""
        assert got2["meta"]["providers"] == {"default": True, "second": True}
        status2, _ = _call(httpd, "/api/config", method="POST", data=got2["values"])
        assert status2 == 200
        saved2 = load(tmp_path / "config.json")
        assert saved2.providers["default"].api_key() == "sk-a"
        assert saved2.providers["second"].api_key() == "sk-b"
    finally:
        _stop(httpd, thread)


def test_config_post_rejects_unknown_model_provider(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["models"] = {
            "orphan": {"provider": "ghost", "input": 0, "cache": 0, "output": 0},
        }
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 422
        assert "ghost" in body["error"]
    finally:
        _stop(httpd, thread)


def test_verify_uses_named_provider_saved_key(tmp_path: Path) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls: list = []

    class ModelsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            calls.append(self.headers.get("Authorization"))
            payload = b'{"data": [{"id": "m-9"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    models = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
    mthread = threading.Thread(target=models.serve_forever, daemon=True)
    mthread.start()
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"]["second"] = {
            "baseUrl": "https://b.example/v1",
            "apiKeyEnv": "THYCA_TOKEN",
            "apiKey": "sk-second",
            "reasoningEffort": "high",
        }
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        base = f"http://127.0.0.1:{models.server_address[1]}"
        status, body = _call(
            httpd,
            "/api/onboarding/verify",
            method="POST",
            data={"baseUrl": base, "providerId": "second"},
        )
        assert status == 200 and body["models"] == ["m-9"]
        assert calls == ["Bearer sk-second"]
        status, body = _call(
            httpd,
            "/api/onboarding/verify",
            method="POST",
            data={"baseUrl": base, "providerId": "ghost"},
        )
        assert status == 404
    finally:
        _stop(httpd, thread)
        models.shutdown()
        mthread.join(timeout=2)
        models.server_close()


def test_providers_test_ok_and_failure(tmp_path: Path, capsys) -> None:
    ok_payload = json.dumps(
        {"model": "m-test", "choices": [{"message": {"content": "pong"}}]}
    ).encode()
    calls: list = []
    stub, sthread = _chat_stub(ok_payload, calls=calls)
    httpd, thread = _start(tmp_path)
    try:
        base = f"http://127.0.0.1:{stub.server_address[1]}"
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"]["default"]["baseUrl"] = base
        values["providers"]["default"]["apiKey"] = "sk-live"
        values["defaultModel"] = "m-test"
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        status, body = _call(
            httpd, "/api/providers/test", method="POST", data={"providerId": "default"}
        )
        assert status == 200 and body["ok"] is True
        assert body["model"] == "m-test"
        assert body["latencyMs"] >= 0
        assert calls == ["Bearer sk-live"]
        err = capsys.readouterr().err
        assert "provider test provider=default model=m-test ok=true" in err
        # unknown model on a strict stub → 422 with the provider message
        fail, fthread = _chat_stub(b'{"error": "nope"}', status=404)
        try:
            base2 = f"http://127.0.0.1:{fail.server_address[1]}"
            _, got2 = _call(httpd, "/api/config")
            values2 = got2["values"]
            values2["providers"]["default"]["baseUrl"] = base2
            values2["providers"]["default"]["apiKey"] = "sk-live"
            assert _call(httpd, "/api/config", method="POST", data=values2)[0] == 200
            status, body = _call(
                httpd,
                "/api/providers/test",
                method="POST",
                data={"providerId": "default", "model": "ghost"},
            )
            assert status == 422
            assert "ghost" in body["error"]
            assert "sk-live" not in body["error"]
            fail_err = capsys.readouterr().err
            assert "provider test provider=default model=ghost ok=false" in fail_err
            assert "sk-live" not in fail_err
        finally:
            fail.shutdown()
            fthread.join(timeout=2)
            fail.server_close()
    finally:
        _stop(httpd, thread)
        stub.shutdown()
        sthread.join(timeout=2)
        stub.server_close()


def test_providers_test_without_key_422(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("THYCA_TOKEN", raising=False)
    httpd, thread = _start(tmp_path)
    try:
        status, body = _call(
            httpd, "/api/providers/test", method="POST", data={"providerId": "default"}
        )
        assert status == 422
        assert "API key" in body["error"]
        status, body = _call(
            httpd, "/api/providers/test", method="POST", data={"providerId": "ghost"}
        )
        assert status == 404
    finally:
        _stop(httpd, thread)


def test_providers_test_honors_model_baseurl_override(tmp_path: Path) -> None:
    ok_payload = json.dumps(
        {"model": "m-odd", "choices": [{"message": {"content": "pong"}}]}
    ).encode()
    stub, sthread = _chat_stub(ok_payload)
    httpd, thread = _start(tmp_path)
    try:
        base = f"http://127.0.0.1:{stub.server_address[1]}"
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        # Provider points nowhere; the model's own endpoint must win.
        values["providers"]["default"]["baseUrl"] = "http://127.0.0.1:1"
        values["providers"]["default"]["apiKey"] = "sk-live"
        values["models"] = {
            "m-odd": {"baseUrl": base, "input": 0, "cache": 0, "output": 0},
        }
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        status, body = _call(
            httpd,
            "/api/providers/test",
            method="POST",
            data={"providerId": "default", "model": "m-odd"},
        )
        assert status == 200 and body["ok"] is True
        assert body["model"] == "m-odd"
    finally:
        _stop(httpd, thread)
        stub.shutdown()
        sthread.join(timeout=2)
        stub.server_close()


def test_providers_test_dispatches_responses_api(tmp_path: Path) -> None:
    ok_payload = json.dumps(
        {
            "model": "m-resp",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "pong"}],
                }
            ],
        }
    ).encode()
    paths: list = []
    stub, sthread = _chat_stub(ok_payload, paths=paths)
    httpd, thread = _start(tmp_path)
    try:
        base = f"http://127.0.0.1:{stub.server_address[1]}"
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"]["default"]["baseUrl"] = base
        values["providers"]["default"]["apiKey"] = "sk-live"
        values["providers"]["default"]["api"] = "openai_responses"
        values["defaultModel"] = "m-resp"
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        status, body = _call(
            httpd, "/api/providers/test", method="POST", data={"providerId": "default"}
        )
        assert status == 200 and body["ok"] is True
        assert body["model"] == "m-resp"
        assert paths == ["/responses"]
    finally:
        _stop(httpd, thread)
        stub.shutdown()
        sthread.join(timeout=2)
        stub.server_close()
