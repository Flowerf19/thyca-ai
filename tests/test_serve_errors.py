"""TASK-007: shared public turn-error mapping (no leak of stack/path/secret)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from thyca.serve.bridge import public_turn_error
from thyca.app.chat_app import ChatApp, InvalidTurnOption, InvalidTurnText, TurnCancelled
from thyca.config import ConfigError, default_config, load, save
from thyca.llm.llm_base import LLMError
from thyca.serve import default_webui, make_server
from thyca.sessions import SessionCorrupt, SessionError, SessionNotFound
from thyca.memory.facade import MemoryFacade

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
    assert public_turn_error(InvalidTurnText("empty")) == (
        400,
        "invalid_text",
        "invalid text: empty",
    )
    assert public_turn_error(InvalidTurnText("too long")) == (
        400,
        "invalid_text",
        "invalid text: too long",
    )
    assert public_turn_error(InvalidTurnText("invalid")) == (
        400,
        "invalid_text",
        "invalid text: invalid",
    )
    # Unexpected ValueError is a bug: 500, constant message (B2/F10).
    assert public_turn_error(ValueError("boom")) == (
        500,
        "internal_error",
        "chat unavailable",
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
    assert public_turn_error(TurnCancelled()) == (200, "cancelled", "cancelled")
    assert public_turn_error(InvalidTurnOption("invalid model")) == (
        400,
        "invalid_model",
        "invalid model",
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


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import pytest

def test_x2_serve_parses_via_turn_options() -> None:
    from thyca.app.chat_app import InvalidTurnOption
    from thyca.serve.errors import parse_turn_body

    assert parse_turn_body({"text": "hi"}) == ("hi", None, None, False)
    assert parse_turn_body({"text": "  hi  "}) == ("hi", None, None, False)
    assert parse_turn_body({"text": "hi", "model": "m", "effort": "low"}) == (
        "hi",
        "m",
        "low",
        False,
    )
    assert parse_turn_body({"retry": True}) == ("", None, None, True)
    assert parse_turn_body({"retry": True, "text": 123}) == ("", None, None, True)
    assert parse_turn_body({"retry": True, "text": "kept"}) == ("kept", None, None, True)
    with pytest.raises(InvalidTurnOption):
        parse_turn_body({"text": "hi", "model": ""})
    with pytest.raises(InvalidTurnOption):
        parse_turn_body({"text": "hi", "model": "x" * 201})
    with pytest.raises(InvalidTurnOption):
        parse_turn_body({"text": "hi", "effort": "  "})
    with pytest.raises(ValueError):
        parse_turn_body({"text": ""})
    with pytest.raises(ValueError):
        parse_turn_body({"text": "x" * 4001})
    with pytest.raises(ValueError):
        parse_turn_body({"text": 5})


def test_x26_with_json_preamble() -> None:
    from thyca.serve.errors import _with_json

    class Handler:
        def __init__(self, payload=None, explode=False):
            self.payload = payload
            self.explode = explode
            self.sent: list = []

        def _read_json(self):
            if self.explode:
                raise ValueError("bad")
            return self.payload

        def _json(self, status, body):
            self.sent.append((status, body))

    handler = Handler({"a": 1})
    assert _with_json(handler, lambda payload: ("ran", payload)) == ("ran", {"a": 1})
    assert handler.sent == []
    bad = Handler(explode=True)
    assert _with_json(bad, lambda payload: "never") is None
    assert bad.sent == [(400, {"error": "invalid body"})]
    stream = Handler(explode=True)
    _with_json(stream, lambda payload: "never", error="invalid text")
    assert stream.sent == [(400, {"error": "invalid text"})]


# Moved from tests/test_b2_contracts.py (B2 batch).
import pytest
from thyca.serve.sessions_api import session_turn

def test_f10_known_failures_400_with_reason_unexpected_500() -> None:
    """Fails pre-fix: every ValueError mapped to 400 'invalid text'."""
    for reason in ("empty", "too long", "invalid"):
        assert public_turn_error(InvalidTurnText(reason)) == (
            400,
            "invalid_text",
            f"invalid text: {reason}",
        )
    status, code, message = public_turn_error(ValueError("boom"))
    assert (status, code, message) == (500, "internal_error", "chat unavailable")
    assert "boom" not in message


def test_f10_unexpected_turn_value_error_is_500_and_logged(capsys) -> None:
    """Fails pre-fix: an in-turn bug ValueError returned 400, masking it."""

    class Handler:
        def __init__(self) -> None:
            self.sent: list = []

        def _read_json(self):
            return {"text": "hi"}

        def _json(self, status, body):
            self.sent.append((status, body))

    class BuggyApp:
        def default_model(self):
            return "m"

        def provider_id_for(self, model):
            return "default"

        def turn(self, *args, **kwargs):
            raise ValueError("boom")

    handler = Handler()
    session_turn(handler, BuggyApp(), "2026-09-25T00-00-00_ab12")
    assert handler.sent == [(500, {"error": "chat unavailable"})]
    logged = capsys.readouterr().err
    assert "code=internal_error" in logged
    assert "boom" not in logged
