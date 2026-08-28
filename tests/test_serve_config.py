"""Config schema + settings HTTP endpoints — settings-webui."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from thyca.config import default_config, load, save
from thyca.config_schema import config_schema
from thyca.onboarding import ProviderProbeError
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
    assert provider["provider.reasoningEffort"]["choices"] == ["low", "medium", "high"]
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
        assert status == 200 and body == {"ready": False}
    finally:
        _stop(httpd, thread)


def test_config_get_masks_api_key(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        status, body = _call(httpd, "/api/config")
        assert status == 200
        assert body["values"]["provider"]["apiKey"] == ""
        assert body["schema"]["sections"][0]["key"] == "provider"
    finally:
        _stop(httpd, thread)


def test_config_post_saves_and_keeps_key(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["provider"]["apiKey"] = "sk-live-123"
        values["provider"]["model"] = "gpt-5.6-luna"
        status, body = _call(httpd, "/api/config", method="POST", data=values)
        assert status == 200 and body == {"ok": True, "ready": True}
        saved = load(tmp_path / "config.json")
        assert saved.provider.apiKey == "sk-live-123"
        assert saved.provider.model == "gpt-5.6-luna"
    finally:
        _stop(httpd, thread)


def test_config_post_empty_key_keeps_old(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        _, got = _call(httpd, "/api/config")
        values = got["values"]
        values["provider"]["apiKey"] = "sk-first"
        _, _ = _call(httpd, "/api/config", method="POST", data=values)
        _, got2 = _call(httpd, "/api/config")
        values2 = got2["values"]
        values2["provider"]["model"] = "m-2"
        values2["provider"]["apiKey"] = ""
        status, body = _call(httpd, "/api/config", method="POST", data=values2)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert saved.provider.apiKey == "sk-first"
        assert saved.provider.model == "m-2"
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
        values["provider"]["model"] = "only-model"
        _, _ = _call(httpd, "/api/config", method="POST", data=values)
        # UI keeps the deleted default as fallback — server must accept it.
        values2 = got["values"]
        values2["models"] = {}
        values2["provider"]["model"] = "only-model"
        status, body = _call(httpd, "/api/config", method="POST", data=values2)
        assert status == 200 and body["ok"] is True
        saved = load(tmp_path / "config.json")
        assert saved.provider.model == "only-model"
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
            data={"baseUrl": "http://127.0.0.1:1", "apiKey": "sk-x"},
        )
        assert status == 422
        assert "sk-x" not in body["error"]
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
