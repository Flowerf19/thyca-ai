"""TASK-007: shared public turn-error mapping (no leak of stack/path/secret)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from thyca.chat_app import ChatApp
from thyca.config import ConfigError, default_config, load, save
from thyca.llm.llm_base import LLMError
from thyca.serve import default_webui, make_server, public_turn_error
from thyca.sessions import SessionCorrupt, SessionError, SessionNotFound
from thyca.tools.memory import MemoryFacade

WEBUI = default_webui()

SECRET = "sk-super-secret-12345"
SECRET_CONFIG_ERROR = ConfigError(
    f"/home/user/.thyca/config.json: cannot write {SECRET}"
)
LLM_TEXT = "provider HTTP 401: denied [redacted]"


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(default_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


def _start(tmp_path: Path, chat: ChatApp):
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(host="127.0.0.1", port=0, webui=WEBUI, facade=facade, chat=chat)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd, thread: threading.Thread) -> None:
    httpd.shutdown()
    thread.join(timeout=2)
    httpd.server_close()


def test_maps_each_exception_to_exact_public_triple() -> None:
    assert public_turn_error(ValueError("text must be a string")) == (
        400,
        "invalid_text",
        "invalid text",
    )
    assert public_turn_error(SessionNotFound(Path("/tmp/sessions"))) == (
        404,
        "session_not_found",
        "session not found",
    )
    assert public_turn_error(SessionCorrupt(Path("/tmp/x.jsonl"), 3, "bad line")) == (
        503,
        "session_unreadable",
        "session unreadable",
    )
    assert public_turn_error(SessionError("cannot secure /tmp/sessions")) == (
        503,
        "session_unavailable",
        "session unavailable",
    )
    assert public_turn_error(LLMError(LLM_TEXT)) == (503, "llm_error", LLM_TEXT)
    assert public_turn_error(SECRET_CONFIG_ERROR) == (
        503,
        "chat_unavailable",
        "chat unavailable",
    )
    assert public_turn_error(RuntimeError("boom")) == (
        503,
        "chat_unavailable",
        "chat unavailable",
    )


def test_config_error_message_never_contains_path_or_secret() -> None:
    status, code, message = public_turn_error(SECRET_CONFIG_ERROR)
    assert status == 503
    assert code == "chat_unavailable"
    assert message == "chat unavailable"
    assert SECRET not in message
    assert ".thyca" not in message
    assert "config.json" not in message
    assert "Path(" not in message


def test_llm_error_keeps_redacted_capped_text() -> None:
    assert public_turn_error(LLMError(LLM_TEXT))[2] == LLM_TEXT
    long = "x" * 10_000
    assert public_turn_error(LLMError(long))[2] == long


def test_turn_config_error_returns_constant_json_message(tmp_path: Path) -> None:
    class ConfigBoom:
        async def chat(self, messages, tools=None):
            raise SECRET_CONFIG_ERROR

    httpd, thread = _start(tmp_path, _chat(tmp_path, ConfigBoom()))
    try:
        created = _json_create(httpd)
        try:
            urlopen(
                Request(
                    _url(httpd, f"/api/sessions/{created['id']}/turn"),
                    data=b'{"text":"hi"}',
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=5,
            )
        except HTTPError as exc:
            assert exc.code == 503
            body = exc.read().decode("utf-8")
            assert json.loads(body) == {"error": "chat unavailable"}
            assert SECRET not in body
            assert ".thyca" not in body
            assert "config.json" not in body
        else:
            raise AssertionError("expected 503")
    finally:
        _stop(httpd, thread)


def _url(httpd, path: str) -> str:
    return f"http://127.0.0.1:{httpd.server_address[1]}{path}"


def _json_create(httpd) -> dict:
    with urlopen(Request(_url(httpd, "/api/sessions"), data=b"", method="POST"), timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
