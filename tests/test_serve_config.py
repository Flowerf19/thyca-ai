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
from thyca.memory.facade import MemoryFacade

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
    assert provider["provider.reasoningEffort"]["choices"] == ["low", "medium", "high", "xhigh", "max"]
    assert provider["provider.reasoningEffort"]["default"] == "high"
    assert provider["provider.apiKey"]["secret"] is True
    assert "secret" not in provider["provider.apiKeyEnv"]
    limits = {f["key"]: f for f in schema["sections"][1]["fields"]}
    assert limits["limits.loopMax"]["min"] == 1
    assert limits["limits.loopMax"]["max"] == 200


def test_schema_includes_new_fields_without_labels() -> None:
    # Any new dataclass field must surface even without a label entry.
    schema = config_schema()
    limits = {f["key"]: f for f in schema["sections"][1]["fields"]}
    assert set(limits) == {
        "limits.loopMax",
        "limits.hotTailKB",
        "limits.contextTokens",
        "limits.softTimeoutS",
    }
    assert limits["limits.softTimeoutS"]["min"] == 1
    assert limits["limits.softTimeoutS"]["max"] == 300
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
        # fresh config is truly empty: no provider, no OpenAI seed
        assert body["values"]["providers"] == {}
        assert body["values"]["defaultModel"] == ""
        assert body["values"]["defaultProvider"] == ""
        assert body["meta"] == {"hasApiKey": False, "providers": {}}
        assert body["schema"]["sections"][0]["key"] == "provider"
    finally:
        _stop(httpd, thread)


def test_config_post_saves_and_keeps_key(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"] = {
            "default": {
                "baseUrl": "https://a.example/v1",
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-live-123",
                "reasoningEffort": "high",
            }
        }
        values["defaultProvider"] = "default"
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
        values["providers"] = {
            "default": {
                "baseUrl": "https://a.example/v1",
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-first",
                "reasoningEffort": "high",
            }
        }
        values["defaultProvider"] = "default"
        values["defaultModel"] = "m-1"
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


def test_config_post_omitted_key_keeps_old(tmp_path: Path) -> None:
    """A payload with no apiKey field must not wipe the stored secret."""
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"] = {
            "default": {
                "baseUrl": "https://a.example/v1",
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-first",
                "reasoningEffort": "high",
            }
        }
        values["defaultProvider"] = "default"
        values["defaultModel"] = "m-1"
        _, _ = _call(httpd, "/api/config", method="POST", data=values)
        _, got2 = _call(httpd, "/api/config")
        values2 = got2["values"]
        values2["defaultModel"] = "m-2"
        del values2["providers"]["default"]["apiKey"]
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


def test_config_post_removing_registered_default_is_rejected(tmp_path: Path) -> None:
    """The registered default cannot be removed via any config replacement.

    Justification: deleting the current default in the same transaction
    that keeps its name (legacy fallback) or switches away would leave
    no safe single-step recovery — switch the default in a separate
    operation first, then delete. An unregistered legacy default is not
    a registry entry, so keeping it never triggers this rule.
    """
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["models"] = {"only-model": {"input": 0, "cache": 0, "output": 0}}
        values["defaultModel"] = "only-model"
        _, _ = _call(httpd, "/api/config", method="POST", data=values)
        # Keeping the name as an unregistered fallback does not excuse
        # removing the registered entry.
        values2 = got["values"]
        values2["models"] = {}
        values2["defaultModel"] = "only-model"
        status, body = _call(httpd, "/api/config", method="POST", data=values2)
        assert status == 422
        assert "model mặc định" in body["error"]
        saved = load(tmp_path / "config.json")
        assert saved.defaultModel == "only-model"
        assert "only-model" in saved.models
        # Keeping an unregistered legacy default that is NOT being
        # removed stays legal: no registry entry is touched, so neither
        # the reference guard nor the default rule fires.
        values2["models"] = {"only-model": {"input": 0, "cache": 0, "output": 0}}
        values2["defaultModel"] = "legacy-name"
        status, body = _call(httpd, "/api/config", method="POST", data=values2)
        assert status == 200 and body["ok"] is True
        _, got3 = _call(httpd, "/api/config")
        values3 = got3["values"]
        values3["models"] = {}
        status, body = _call(httpd, "/api/config", method="POST", data=values3)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert saved.defaultModel == "legacy-name"
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
        values["defaultProvider"] = "default"
        values["defaultModel"] = "model-b"
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
        values["providers"]["default"] = {
            "baseUrl": "https://a.example/v1",
            "apiKeyEnv": "THYCA_TOKEN",
            "reasoningEffort": "high",
        }
        values["defaultProvider"] = "default"
        values["defaultModel"] = "m-9"
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
        values["providers"] = {
            "default": {
                "baseUrl": base,
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-live",
                "reasoningEffort": "high",
            }
        }
        values["defaultProvider"] = "default"
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
        # fresh configs ship with no provider: register a keyless one first
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"] = {
            "default": {
                "baseUrl": "https://a.example/v1",
                "apiKeyEnv": "THYCA_TOKEN",
                "reasoningEffort": "high",
            }
        }
        values["defaultProvider"] = "default"
        values["defaultModel"] = "m"
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
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
        values["providers"] = {
            "default": {
                "baseUrl": "http://127.0.0.1:1",
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-live",
                "reasoningEffort": "high",
            }
        }
        values["defaultProvider"] = "default"
        values["defaultModel"] = "m-odd"
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
        values["providers"] = {
            "default": {
                "baseUrl": base,
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-live",
                "api": "openai_responses",
                "reasoningEffort": "high",
            }
        }
        values["defaultProvider"] = "default"
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


def test_merge_saved_key_keeps_omitted_and_null() -> None:
    from dataclasses import replace

    from thyca.config import ProviderEntry, merge_saved_keys

    cfg = replace(
        default_config(),
        providers={"default": ProviderEntry(baseUrl="https://a.example/v1", apiKey="sk-live")},
        defaultProvider="default",
        defaultModel="m",
    )
    merged = merge_saved_keys(
        {"providers": {"default": {"apiKey": "sk-new"}}}, cfg
    )
    assert merged["providers"]["default"]["apiKey"] == "sk-new"
    for missing in ({}, {"apiKey": None}, {"apiKey": ""}):
        merged = merge_saved_keys({"providers": {"default": dict(missing)}}, cfg)
        assert merged["providers"]["default"]["apiKey"] == "sk-live"
    # unknown ids still merge to None (env fallback), never another key
    merged = merge_saved_keys({"providers": {"ghost": {}}}, cfg)
    assert merged["providers"]["ghost"]["apiKey"] is None


def test_config_post_explicit_null_key_keeps_old(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"] = {
            "default": {
                "baseUrl": "https://a.example/v1",
                "apiKeyEnv": "THYCA_TOKEN",
                "apiKey": "sk-first",
                "reasoningEffort": "high",
            }
        }
        values["defaultProvider"] = "default"
        values["defaultModel"] = "m-1"
        _, _ = _call(httpd, "/api/config", method="POST", data=values)
        _, got2 = _call(httpd, "/api/config")
        values2 = got2["values"]
        values2["providers"]["default"]["apiKey"] = None
        status, body = _call(httpd, "/api/config", method="POST", data=values2)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert saved.providers["default"].apiKey == "sk-first"
    finally:
        _stop(httpd, thread)


def test_config_post_save_failure_is_json_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("thyca.serve.config_api.save", boom)
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        status, body = _call(httpd, "/api/config", method="POST", data=got["values"])
        assert status == 503
        assert body == {"error": "config unavailable"}
    finally:
        _stop(httpd, thread)


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

def test_x4_merge_saved_keys_single_home() -> None:
    from dataclasses import replace

    import thyca.serve.config_api as config_api
    from thyca.config import default_config, merge_saved_keys

    assert config_api.merge_saved_keys is merge_saved_keys
    from thyca.config import ProviderEntry

    cfg = replace(
        default_config(),
        providers={"default": ProviderEntry(baseUrl="https://a.example/v1", apiKey="sk")},
        defaultProvider="default",
        defaultModel="m",
    )
    assert merge_saved_keys({"providers": {"default": {}}}, cfg)["providers"][
        "default"
    ]["apiKey"] == "sk"
    assert not hasattr(config_api, "_merge_saved_key")


def _start_config_server(tmp_path: Path):
    from thyca.config import default_config, save
    from thyca.memory.facade import MemoryFacade
    from thyca.serve import default_webui, make_server

    save(default_config(), tmp_path / "config.json")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(
        host="127.0.0.1",
        port=0,
        webui=default_webui(),
        facade=facade,
        config_file=tmp_path / "config.json",
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _post(httpd, path: str, data: dict) -> tuple[int, dict]:
    body = json.dumps(data).encode()
    request = Request(
        f"http://127.0.0.1:{httpd.server_address[1]}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _start_guarded(tmp_path: Path, *, models: dict, bots: dict | None = None,
                    paused: tuple = (), default_model: str = "m-free"):
    """Isolated server whose config registers ``models`` and whose bots
    explicitly reference them (``bots`` maps bot id to model id; None
    means inherit the global default). ``config_file`` is always explicit
    so the guard scans the fixture inventory, never the live home dir."""
    from dataclasses import replace

    from thyca.bots.card import BotCard, BotModel
    from thyca.bots.registry import BotRegistry
    from thyca.config import ModelCfg, ProviderEntry

    cfg = replace(
        default_config(),
        providers={"alpha": ProviderEntry(baseUrl="https://alpha.invalid/v1", apiKey="k")},
        defaultProvider="alpha",
        defaultModel=default_model,
        models={name: ModelCfg(**spec) for name, spec in models.items()},
    )
    save(cfg, tmp_path / "config.json")
    # bots=None leaves the inventory genuinely absent (no bots/ dir at
    # all); an empty dict still seeds the registry (bots/ exists, empty).
    if bots is not None:
        reg = BotRegistry(tmp_path)
        reg.ensure_thyca()
        for bot_id, model_id in bots.items():
            reg.create(BotCard(
                bot_id=bot_id, name=bot_id, role="R", description="d", persona="p",
                model=BotModel(id=model_id) if model_id else None,
            ))
        for bot_id in paused:
            reg.set_paused(bot_id, True)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(
        host="127.0.0.1", port=0, webui=WEBUI, facade=facade, chat=None,
        config_file=tmp_path / "config.json",
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop_guarded(httpd, thread) -> None:
    httpd.shutdown()
    thread.join(timeout=2)
    httpd.server_close()


def test_config_post_removing_referenced_model_conflicts(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}},
        bots={"coder": "m-used", "helper": None},
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m-used"]
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 409
        assert body["referencedBy"] == {"m-used": ["coder"]}
        assert "coder" in body["error"] and "m-used" in body["error"]
        assert "m-used" in load(tmp_path / "config.json").models
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_removing_unreferenced_model_succeeds(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}},
        bots={"coder": "m-used"}, default_model="m-used",
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m-free"]
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200 and body["ok"] is True
        assert "m-free" not in load(tmp_path / "config.json").models
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_removal_with_unreadable_inventory_fails_closed(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}},
        bots={"coder": "m-used"},
    )
    try:
        (tmp_path / "bots" / "coder" / "settings.json").write_text("{bad", encoding="utf-8")
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m-used"]
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 503
        assert "kiểm tra lại cấu hình bot" in body["error"]
        assert "m-used" in load(tmp_path / "config.json").models
        # A replacement that removes nothing skips the scan and still works.
        values["models"]["m-used"] = {"baseUrl": "", "input": 0, "cache": 0, "output": 0}
        values["limits"] = {"loopMax": 81, "hotTailKB": 4, "contextTokens": 128000}
        status, _ = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200
    finally:
        _stop_guarded(httpd, thread)


def _start_guarded_chat(tmp_path: Path, *, models: dict, bots: dict,
                        default_model: str = "m1"):
    """Guarded fixture with a live ChatApp so bot assignment races the
    guarded config replacement over real HTTP."""
    from dataclasses import dataclass, replace

    from thyca.app.chat_app import ChatApp
    from thyca.bots.card import BotCard, BotModel
    from thyca.config import ModelCfg, ProviderEntry
    from thyca.llm.llm_base import ChatReply

    @dataclass
    class IdleLLM:
        async def chat(self, messages, tools=None):
            return ChatReply(content="unused")

    cfg = replace(
        default_config(),
        providers={"alpha": ProviderEntry(baseUrl="https://alpha.invalid/v1", apiKey="k")},
        defaultProvider="alpha",
        defaultModel=default_model,
        models={name: ModelCfg(**spec) for name, spec in models.items()},
    )
    save(cfg, tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"), connect=IdleLLM())
    for bot_id, model_id in bots.items():
        app._bots.create(BotCard(
            bot_id=bot_id, name=bot_id, role="R", description="d", persona="p",
            model=BotModel(id=model_id) if model_id else None,
        ))
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(
        host="127.0.0.1", port=0, webui=WEBUI, facade=facade, chat=app,
        config_file=tmp_path / "config.json",
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, app


def _stop_guarded_chat(httpd, thread, app) -> None:
    httpd.shutdown()
    thread.join(timeout=2)
    httpd.server_close()
    app.shutdown()


def test_config_post_concurrent_removal_and_assignment_keeps_invariant(tmp_path: Path) -> None:
    """A model deletion racing a bot assignment to that model never
    leaves a dangling explicit reference: either the deletion sees the
    new reference (409) or the assignment validates against the fresh
    config (400/503)."""
    from thyca.bots.registry import BotRegistry

    httpd, thread, app = _start_guarded_chat(
        tmp_path, models={"m1": {}, "m2": {}},
        bots={"coder": "m1", "designer": None}, default_model="m1",
    )
    try:
        barrier = threading.Barrier(2)
        outcomes: dict = {}

        def remover() -> None:
            barrier.wait(timeout=10)
            _, got = _call(httpd, "/api/config")
            values = got["values"]
            del values["models"]["m1"]
            values["defaultModel"] = "m2"
            outcomes["remove"] = _call(httpd, "/api/config", method="POST", data=values)

        def assigner() -> None:
            barrier.wait(timeout=10)
            outcomes["assign"] = _post(
                httpd, "/api/bots/designer/settings", {"model": {"id": "m1", "thinking": None}})

        first, second = threading.Thread(target=remover), threading.Thread(target=assigner)
        first.start()
        second.start()
        first.join(timeout=20)
        second.join(timeout=20)
        assert not first.is_alive() and not second.is_alive()
        assert outcomes["remove"][0] in (200, 409)
        assert outcomes["assign"][0] in (200, 400, 503)
        saved = load(tmp_path / "config.json")
        reg = BotRegistry(tmp_path)
        for bot_id in ("coder", "designer"):
            doc = reg.get(bot_id)
            assert doc.ok and doc.card is not None
            mid = doc.card.model.id if doc.card.model else "default"
            assert mid == "default" or mid == saved.defaultModel or mid in saved.models
    finally:
        _stop_guarded_chat(httpd, thread, app)


def test_model_coordination_hold_serializes_threads(tmp_path: Path) -> None:
    import time

    from thyca.config.coordination import hold

    barrier = threading.Barrier(2)
    order: list = []

    def worker(tag: str, delay: float) -> None:
        barrier.wait(timeout=10)
        with hold(tmp_path):
            order.append((tag, "in"))
            time.sleep(delay)
            order.append((tag, "out"))

    first = threading.Thread(target=worker, args=("a", 0.3))
    second = threading.Thread(target=worker, args=("b", 0.0))
    first.start()
    second.start()
    first.join(timeout=20)
    second.join(timeout=20)
    assert not first.is_alive() and not second.is_alive()
    # Whoever enters first leaves before the other enters: never interleaved.
    assert [tag for tag, _ in order] == [order[0][0], order[0][0], order[3][0], order[3][0]]
    assert [mark for _, mark in order] == ["in", "out", "in", "out"]


def test_model_coordination_hold_is_not_reentrant(tmp_path: Path) -> None:
    # FileLock cannot nest in one thread: the single-acquisition discipline
    # in coordination.py is load-bearing, not stylistic.
    from thyca.config.coordination import CoordinationError, hold

    with hold(tmp_path):
        try:
            with hold(tmp_path, timeout=0.2):
                raise AssertionError("nested hold must not succeed")
        except CoordinationError:
            pass


def test_bot_settings_assignment_to_deleted_model_fails(tmp_path: Path) -> None:
    httpd, thread, app = _start_guarded_chat(
        tmp_path, models={"m1": {}, "m2": {}},
        bots={"coder": "m1", "designer": None}, default_model="m1",
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m2"]
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        status, body = _post(
            httpd, "/api/bots/designer/settings", {"model": {"id": "m2", "thinking": None}})
        assert status == 400
        assert body == {"error": "unknown model"}
        # Re-registering makes the same assignment succeed again.
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["models"]["m2"] = {"baseUrl": "", "input": 0, "cache": 0, "output": 0}
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        status, body = _post(
            httpd, "/api/bots/designer/settings", {"model": {"id": "m2", "thinking": None}})
        assert status == 200
        assert body["model"] == {"id": "m2", "thinking": None}
    finally:
        _stop_guarded_chat(httpd, thread, app)


def test_config_post_removal_counts_paused_bots_but_not_default_inheritance(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}},
        bots={"follower": None, "sleeper": "m-used"}, paused=("sleeper",),
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m-used"]
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 409
        # The paused bot blocks; the default-inheriting bot is not listed.
        assert body["referencedBy"] == {"m-used": ["sleeper"]}
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_abandoning_default_is_rejected(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}, "m2": {}},
        bots={"coder": "m2"}, default_model="m1",
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m1"]
        values["defaultModel"] = ""
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 422
        assert "model mặc định" in body["error"]
        saved = load(tmp_path / "config.json")
        assert "m1" in saved.models and saved.defaultModel == "m1"
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_default_switch_then_delete_in_separate_steps(tmp_path: Path) -> None:
    # Even an atomic switch-and-delete is rejected: the default must be
    # switched in one operation, and only then may the old default be
    # removed in another.
    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}, "m2": {}},
        bots={"coder": "m2"}, default_model="m1",
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m1"]
        values["defaultModel"] = "m2"
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 422
        assert "model mặc định" in body["error"]
        _, got = _call(httpd, "/api/config")
        switch = got["values"]
        switch["defaultModel"] = "m2"
        assert _call(httpd, "/api/config", method="POST", data=switch)[0] == 200
        _, got = _call(httpd, "/api/config")
        removal = got["values"]
        del removal["models"]["m1"]
        status, body = _call(httpd, "/api/config", method="POST", data=removal)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert "m1" not in saved.models and saved.defaultModel == "m2"
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_switch_and_delete_with_reference_conflicts(tmp_path: Path) -> None:
    # The reference guard runs first: switching the default does not
    # excuse a model still explicitly assigned to a bot.
    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}, "m2": {}},
        bots={"coder": "m1"}, default_model="m1",
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m1"]
        values["defaultModel"] = "m2"
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 409
        assert body["referencedBy"] == {"m1": ["coder"]}
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_removal_with_file_not_dir_inventory_fails_closed(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}}, bots=None,
    )
    try:
        (tmp_path / "bots").write_text("not a directory", encoding="utf-8")
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m-used"]
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 503
        assert "kiểm tra lại cấu hình bot" in body["error"]
        assert "m-used" in load(tmp_path / "config.json").models
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_removal_with_absent_inventory_succeeds(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}}, bots=None,
    )
    try:
        assert not (tmp_path / "bots").exists()
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m-used"]
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200 and body["ok"] is True
        assert "m-used" not in load(tmp_path / "config.json").models
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_deletion_can_win_before_assignment(tmp_path: Path) -> None:
    # Deterministic deletion-wins ordering: with no references the removal
    # succeeds, and the later assignment to the gone model must fail.
    from thyca.bots.registry import BotRegistry

    httpd, thread, app = _start_guarded_chat(
        tmp_path, models={"m1": {}, "m2": {}},
        bots={"designer": None}, default_model="m2",
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        del values["models"]["m1"]
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        status, body = _post(
            httpd, "/api/bots/designer/settings", {"model": {"id": "m1", "thinking": None}})
        assert status == 400
        assert body == {"error": "unknown model"}
        reg = BotRegistry(tmp_path)
        doc = reg.get("designer")
        assert doc.ok and doc.card is not None
        assert doc.card.model is None or doc.card.model.id == "default"
    finally:
        _stop_guarded_chat(httpd, thread, app)


def test_config_post_unreferenced_race_keeps_invariant(tmp_path: Path) -> None:
    # Barrier-started race with no initial references: either the deletion
    # wins (assignment then fails) or the assignment wins (deletion then
    # conflicts). The invariant holds in every outcome; responses stay in
    # the fail-closed set and the server never wedges.
    from thyca.bots.registry import BotRegistry

    httpd, thread, app = _start_guarded_chat(
        tmp_path, models={"m1": {}, "m2": {}},
        bots={"designer": None}, default_model="m2",
    )
    try:
        barrier = threading.Barrier(2)
        outcomes: dict = {}

        def remover() -> None:
            barrier.wait(timeout=10)
            _, got = _call(httpd, "/api/config")
            values = got["values"]
            values["models"].pop("m1", None)
            outcomes["remove"] = _call(httpd, "/api/config", method="POST", data=values)

        def assigner() -> None:
            barrier.wait(timeout=10)
            outcomes["assign"] = _post(
                httpd, "/api/bots/designer/settings", {"model": {"id": "m1", "thinking": None}})

        first, second = threading.Thread(target=remover), threading.Thread(target=assigner)
        first.start()
        second.start()
        first.join(timeout=20)
        second.join(timeout=20)
        assert not first.is_alive() and not second.is_alive()
        assert outcomes["remove"][0] in (200, 409)
        assert outcomes["assign"][0] in (200, 400, 503)
        saved = load(tmp_path / "config.json")
        doc = BotRegistry(tmp_path).get("designer")
        assert doc.ok and doc.card is not None
        mid = doc.card.model.id if doc.card.model else "default"
        assert mid == "default" or mid == saved.defaultModel or mid in saved.models
    finally:
        _stop_guarded_chat(httpd, thread, app)


def test_model_coordination_hold_blocks_across_processes(tmp_path: Path) -> None:
    import subprocess
    import sys
    import time

    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}}, bots=None,
    )
    try:
        root = Path(__file__).resolve().parents[1]
        code = (
            "import sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from pathlib import Path\n"
            "from thyca.config.coordination import hold\n"
            "with hold(Path(sys.argv[2])):\n"
            "    print('ready', flush=True)\n"
            "    time.sleep(1.5)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code, str(root), str(tmp_path)],
            cwd=str(root), stdout=subprocess.PIPE, text=True,
        )
        try:
            _, got = _call(httpd, "/api/config")
            values = got["values"]
            del values["models"]["m-used"]
            values["baseRevision"] = got["revision"]
            assert proc.stdout.readline().strip() == "ready"
            start = time.monotonic()
            status, body = _call(httpd, "/api/config", method="POST", data=values)
            elapsed = time.monotonic() - start
            assert status == 200 and body["ok"] is True
            # The file lock is truly cross-process: the POST waited for
            # the holder instead of sailing through (or deadlocking).
            assert elapsed >= 0.8
        finally:
            proc.wait(timeout=10)
    finally:
        _stop_guarded(httpd, thread)


def test_config_revision_present_and_changes_on_write(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}}, bots=None,
    )
    try:
        _, first = _call(httpd, "/api/config")
        assert isinstance(first.get("revision"), str) and first["revision"]
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["limits"] = {"loopMax": 81, "hotTailKB": 4, "contextTokens": 128000}
        status, _ = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200
        _, second = _call(httpd, "/api/config")
        assert second["revision"] != first["revision"]
    finally:
        _stop_guarded(httpd, thread)


def test_config_get_snapshot_cannot_be_split_by_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The GET race the revision scheme exists to close: pause GET after
    # it has read values but before it reads the revision, and let a
    # concurrent POST run. The coordination lock must serialize the POST
    # behind the whole snapshot, so replaying the returned snapshot can
    # never overwrite the intervening update. Without the lock the POST
    # would slip in between, GET would pair stale values with the new
    # revision, and the replay would silently revert the update.
    import threading
    import time

    import thyca.serve.config_api as config_api
    from thyca.config.coordination import CoordinationError, hold

    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        entered, release = threading.Event(), threading.Event()
        gate = threading.Lock()
        paused_once = []
        real_revision = config_api._config_revision

        def hooked(config_file, root):
            with gate:
                first = not paused_once
                if first:
                    paused_once.append(True)
            if first:
                entered.set()
                assert release.wait(timeout=10)
            return real_revision(config_file, root)

        outcomes: dict = {}

        def reader() -> None:
            try:
                outcomes["get"] = _call(httpd, "/api/config")
            except Exception as exc:  # noqa: BLE001 — recorded for assert
                outcomes["get_error"] = exc

        def writer() -> None:
            try:
                values = {
                    "providers": {"alpha": {"baseUrl": "https://alpha.invalid/v1"}},
                    "defaultProvider": "alpha", "defaultModel": "m1",
                    "models": {"m1": {"baseUrl": "", "input": 0, "cache": 0, "output": 0}},
                    "limits": {"loopMax": 91, "hotTailKB": 4, "contextTokens": 128000},
                }
                outcomes["post"] = _call(httpd, "/api/config", method="POST", data=values)
            except Exception as exc:  # noqa: BLE001 — recorded for assert
                outcomes["post_error"] = exc

        monkeypatch.setattr(config_api, "_config_revision", hooked)
        getter = threading.Thread(target=reader)
        getter.start()
        try:
            assert entered.wait(timeout=10)
            # The paused GET holds coordination across values→revision:
            # anything else serializes behind it instead of interleaving.
            with pytest.raises(CoordinationError):
                with hold(tmp_path, timeout=0.3):
                    pass
            poster = threading.Thread(target=writer)
            poster.start()
            time.sleep(0.3)
            assert poster.is_alive()  # blocked on the snapshot hold
        finally:
            release.set()
        getter.join(timeout=20)
        poster.join(timeout=20)
        assert not getter.is_alive() and not poster.is_alive()
        assert "get_error" not in outcomes, outcomes.get("get_error")
        assert "post_error" not in outcomes, outcomes.get("post_error")
        assert outcomes["post"][0] == 200
        status, got = outcomes["get"]
        assert status == 200
        # The snapshot predates the POST (values show the default
        # loopMax 200) yet carries the pre-POST revision — replaying
        # it must conflict instead of reverting the update.
        assert got["values"]["limits"]["loopMax"] == 200
        replay = dict(got["values"])
        replay["baseRevision"] = got["revision"]
        status, body = _call(httpd, "/api/config", method="POST", data=replay)
        assert status == 409
        assert body["code"] == "config_changed"
        assert load(tmp_path / "config.json").limits.loopMax == 91
        _, current = _call(httpd, "/api/config")
        assert current["revision"] != got["revision"]
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_replay_of_superseded_snapshot_conflicts(tmp_path: Path) -> None:
    # Deterministic proof that a returned snapshot cannot overwrite an
    # intervening update: replaying the superseded values verbatim with
    # their stale revision fails, while replaying the current snapshot
    # is a harmless no-op that leaves the revision untouched.
    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        _, got = _call(httpd, "/api/config")
        superseded = dict(got["values"])
        stale_base = got["revision"]
        update = dict(got["values"])
        update["baseRevision"] = stale_base
        update["limits"] = {"loopMax": 91, "hotTailKB": 4, "contextTokens": 128000}
        assert _call(httpd, "/api/config", method="POST", data=update)[0] == 200
        _, current = _call(httpd, "/api/config")
        assert current["revision"] != stale_base
        replay = dict(superseded)
        replay["baseRevision"] = stale_base
        status, body = _call(httpd, "/api/config", method="POST", data=replay)
        assert status == 409
        assert body["code"] == "config_changed"
        assert load(tmp_path / "config.json").limits.loopMax == 91
        assert _call(httpd, "/api/config")[1]["revision"] == current["revision"]
        fresh = dict(current["values"])
        fresh["baseRevision"] = current["revision"]
        status, _ = _call(httpd, "/api/config", method="POST", data=fresh)
        assert status == 200
        assert _call(httpd, "/api/config")[1]["revision"] == current["revision"]
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_matching_base_revision_succeeds(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}}, bots=None,
    )
    try:
        _, got = _call(httpd, "/api/config")
        values = dict(got["values"])
        values["baseRevision"] = got["revision"]
        values["limits"] = {"loopMax": 82, "hotTailKB": 4, "contextTokens": 128000}
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200 and body["ok"] is True
    finally:
        _stop_guarded(httpd, thread)


def test_config_post_stale_base_revision_conflicts_without_applying(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m-used": {}, "m-free": {}}, bots=None,
    )
    try:
        _, got = _call(httpd, "/api/config")
        stale = dict(got["values"])
        stale["baseRevision"] = got["revision"]
        # A newer write lands first (another tab saving concurrently).
        stale["limits"] = {"loopMax": 83, "hotTailKB": 4, "contextTokens": 128000}
        assert _call(httpd, "/api/config", method="POST", data=dict(stale))[0] == 200
        # The stale write must not silently replace it: no values or keys
        # echoed back, and the stored config keeps the newer limits.
        stale["limits"] = {"loopMax": 84, "hotTailKB": 4, "contextTokens": 128000}
        status, body = _call(httpd, "/api/config", method="POST", data=stale)
        assert status == 409
        assert body["code"] == "config_changed"
        assert "tải lại" in body["error"]
        assert "values" not in body and "apiKey" not in body["error"]
        saved = load(tmp_path / "config.json")
        assert saved.limits.loopMax == 83
    finally:
        _stop_guarded(httpd, thread)


def test_config_key_only_change_rotates_revision(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        _, first = _call(httpd, "/api/config")
        values = dict(first["values"])
        values["baseRevision"] = first["revision"]
        values["providers"] = {"alpha": {"baseUrl": "https://alpha.invalid/v1", "apiKey": "k2"}}
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        _, second = _call(httpd, "/api/config")
        assert second["revision"] != first["revision"]
    finally:
        _stop_guarded(httpd, thread)


def test_config_concurrent_key_only_saves_conflict_safely(tmp_path: Path) -> None:
    from thyca.config.auth import auth_path_for, load_auth_keys

    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        barrier = threading.Barrier(2)
        fetched: dict = {}
        outcomes: dict = {}

        def saver(tag: str, key: str) -> None:
            barrier.wait(timeout=10)
            _, got = _call(httpd, "/api/config")
            fetched[tag] = got["revision"]
            barrier.wait(timeout=10)
            values = dict(got["values"])
            values["baseRevision"] = fetched[tag]
            values["providers"] = {"alpha": {"baseUrl": "https://alpha.invalid/v1", "apiKey": key}}
            outcomes[tag] = _call(httpd, "/api/config", method="POST", data=values)

        first = threading.Thread(target=saver, args=("a", "kA"))
        second = threading.Thread(target=saver, args=("b", "kB"))
        first.start()
        second.start()
        first.join(timeout=20)
        second.join(timeout=20)
        assert not first.is_alive() and not second.is_alive()
        assert fetched["a"] == fetched["b"]
        # Same base revision, same content shape, different keys: exactly
        # one wins; the loser retries against the rotated revision.
        assert sorted(code for code in (outcomes["a"][0], outcomes["b"][0])) == [200, 409]
        stored = load_auth_keys(auth_path_for(tmp_path / "config.json"))
        assert stored.get("alpha") in ("kA", "kB")
        loser = "b" if outcomes["a"][0] == 200 else "a"
        _, got = _call(httpd, "/api/config")
        values = dict(got["values"])
        values["baseRevision"] = got["revision"]
        values["providers"] = {"alpha": {"baseUrl": "https://alpha.invalid/v1", "apiKey": "k-retry"}}
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        assert load_auth_keys(auth_path_for(tmp_path / "config.json"))["alpha"] == "k-retry"
    finally:
        _stop_guarded(httpd, thread)


def test_config_get_snapshot_survives_concurrent_posts(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        barrier = threading.Barrier(5)
        gets: list = []
        posts: list = []

        def reader() -> None:
            barrier.wait(timeout=10)
            for _ in range(10):
                gets.append(_call(httpd, "/api/config"))

        def writer(n: int) -> None:
            barrier.wait(timeout=10)
            for i in range(5):
                _, got = _call(httpd, "/api/config")
                values = dict(got["values"])
                values["baseRevision"] = got.get("revision")
                values["limits"] = {"loopMax": 80 + n, "hotTailKB": 4,
                                       "contextTokens": 128000}
                try:
                    posts.append(_call(httpd, "/api/config", method="POST", data=values))
                except Exception as exc:  # noqa: BLE001 — recorded, never raised
                    posts.append(exc)

        workers = [threading.Thread(target=reader) for _ in range(3)]
        workers += [threading.Thread(target=writer, args=(n,)) for n in (1, 2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
            assert not worker.is_alive()
        assert gets and all(status == 200 and isinstance(body.get("revision"), str)
                            for status, body in gets)
        assert posts and all(isinstance(item, Exception) or item[0] in (200, 409, 422)
                            for item in posts)
        assert not any(isinstance(item, Exception) for item in posts)
        assert not any(status >= 500 for status, _ in gets + posts)
        assert load(tmp_path / "config.json").limits.loopMax in (80, 81, 82)
    finally:
        _stop_guarded(httpd, thread)


def test_config_malformed_or_unreadable_config_fails_closed(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        (tmp_path / "config.json").write_text("{broken", encoding="utf-8")
        status, body = _call(httpd, "/api/config")
        assert status == 503
        assert body == {"error": "config unavailable"}
        values = {"providers": {}, "defaultProvider": "", "defaultModel": "",
                  "models": {}, "pricing": {}}
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 503
        (tmp_path / "config.json").chmod(0o000)
        try:
            assert _call(httpd, "/api/config")[0] == 503
        finally:
            (tmp_path / "config.json").chmod(0o600)
    finally:
        _stop_guarded(httpd, thread)


def test_config_corrupt_revision_key_fails_closed_but_keeps_legacy_post(tmp_path: Path) -> None:
    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        # A torn write (empty, as an interrupted publish would leave under
        # the old in-place scheme) and a short file both fail closed.
        (tmp_path / "config_revision.key").write_bytes(b"")
        status, body = _call(httpd, "/api/config")
        assert status == 503
        assert "revision" not in body
        (tmp_path / "config_revision.key").write_bytes(b"short")
        assert _call(httpd, "/api/config")[0] == 503
        values = {"providers": {"alpha": {"baseUrl": "https://alpha.invalid/v1"}},
                  "defaultProvider": "alpha", "defaultModel": "m1",
                  "models": {"m1": {"baseUrl": "", "input": 0, "cache": 0, "output": 0}},
                  "limits": {"loopMax": 85, "hotTailKB": 4, "contextTokens": 128000}}
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        # Documented recovery: deleting the corrupt key lets the next
        # request publish a fresh one atomically.
        (tmp_path / "config_revision.key").unlink()
        status, body = _call(httpd, "/api/config")
        assert status == 200 and isinstance(body.get("revision"), str)
    finally:
        _stop_guarded(httpd, thread)


def test_config_revision_key_creation_failure_fails_closed_without_litter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulates a crash during first key creation: nothing may be
    # published at the final path, and GET must fail closed rather than
    # serve values callers would silently save back unconditionally.
    import thyca.serve.config_api as config_api

    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        assert not (tmp_path / "config_revision.key").exists()

        def fail_publish(*args, **kwargs):
            raise OSError("injected interruption")

        monkeypatch.setattr(config_api, "atomic_write_text", fail_publish)
        status, _ = _call(httpd, "/api/config")
        assert status == 503
        assert not (tmp_path / "config_revision.key").exists()
        assert not (tmp_path / "config_revision.key.tmp").exists()
        assert list(tmp_path.glob("config_revision.key*")) == []
    finally:
        _stop_guarded(httpd, thread)


def test_config_revision_key_concurrent_first_creation_converges(tmp_path: Path) -> None:
    import stat as _stat
    import threading

    httpd, thread = _start_guarded(
        tmp_path, models={"m1": {}}, bots=None, default_model="m1",
    )
    try:
        assert not (tmp_path / "config_revision.key").exists()
        barrier = threading.Barrier(4)
        outcomes: list = []

        def reader() -> None:
            barrier.wait(timeout=10)
            for _ in range(3):
                try:
                    outcomes.append(_call(httpd, "/api/config"))
                except Exception as exc:  # noqa: BLE001 — recorded, never raised
                    outcomes.append(exc)

        workers = [threading.Thread(target=reader) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
            assert not worker.is_alive()
        assert outcomes and not any(isinstance(item, Exception) for item in outcomes)
        assert not any(status >= 500 for status, _ in outcomes)
        assert any(status == 200 for status, _ in outcomes)
        _, first = _call(httpd, "/api/config")
        _, second = _call(httpd, "/api/config")
        assert first["revision"] == second["revision"]
        key_path = tmp_path / "config_revision.key"
        assert _stat.S_IMODE(key_path.stat().st_mode) == 0o600
        assert len(bytes.fromhex(key_path.read_text(encoding="utf-8"))) == 32
    finally:
        _stop_guarded(httpd, thread)


def test_m7_providers_test_explicit_empty_model_is_400(tmp_path: Path) -> None:
    httpd, thread = _start_config_server(tmp_path)
    try:
        # fresh configs ship with no provider: the empty-model check needs
        # an existing provider to reach the model validation.
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["providers"] = {"default": {"baseUrl": "https://a.example/v1"}}
        values["defaultProvider"] = "default"
        values["defaultModel"] = "m"
        assert _call(httpd, "/api/config", method="POST", data=values)[0] == 200
        status, body = _post(
            httpd, "/api/providers/test", {"providerId": "default", "model": ""}
        )
        assert status == 400
        assert body == {"error": "invalid model"}
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()
