"""Loopback chat HTTP — webui-chat-backend."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from thyca.chat_app import ChatApp, session_title
from thyca.config import default_config, load, save
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.protocol import Message
from thyca.serve import ServeError, default_webui, make_server
from thyca.sessions import SessionManager
from thyca.tools.memory import MemoryFacade

WEBUI = default_webui()


@dataclass
class FakeLLM:
    reply: ChatReply
    requests: list[list[Message]] = field(default_factory=list)

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.requests.append(list(messages))
        return self.reply


def _url(httpd, path: str) -> str:
    port = httpd.server_address[1]
    return f"http://127.0.0.1:{port}{path}"


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(default_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


def _start(tmp_path: Path, chat: ChatApp | None = None):
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(host="127.0.0.1", port=0, webui=WEBUI, facade=facade, chat=chat)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd, thread: threading.Thread) -> None:
    httpd.shutdown()
    thread.join(timeout=2)
    httpd.server_close()


def _json(httpd, path: str, *, method: str = "GET", data: bytes | None = None) -> dict:
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(_url(httpd, path), data=data, method=method, headers=headers)
    with urlopen(request, timeout=5) as response:
        assert response.headers.get_content_type() == "application/json"
        return json.loads(response.read().decode("utf-8"))


def test_list_empty_without_and_with_chat(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        try:
            urlopen(_url(httpd, "/api/sessions"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404 without chat")
    finally:
        _stop(httpd, thread)

    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        payload = _json(httpd, "/api/sessions")
        assert payload["sessions"] == []
        assert payload["model"]
    finally:
        _stop(httpd, thread)


def test_create_list_get_and_turn(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    httpd, thread = _start(tmp_path, _chat(tmp_path, llm))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        assert created["title"] == "Phiên trống"
        assert created["messages"] == []
        listed = _json(httpd, "/api/sessions")
        assert [item["id"] for item in listed["sessions"]] == [created["id"]]
        body = json.dumps({"text": "ping"}).encode("utf-8")
        turned = _json(httpd, f"/api/sessions/{created['id']}/turn", method="POST", data=body)
        assert turned["reply"] == "pong"
        assert turned["title"] == "ping"
        roles = [(item["role"], item["content"]) for item in turned["messages"]]
        assert roles == [("user", "ping"), ("assistant", "pong")]
        loaded = _json(httpd, f"/api/sessions/{created['id']}")
        assert loaded["messages"] == turned["messages"]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert [(item.role, item.content) for item in session.messages] == [
            ("user", "ping"),
            ("assistant", "pong"),
        ]
        assert llm.requests[0][-1].content == "ping"
    finally:
        _stop(httpd, thread)


def test_turn_errors(tmp_path: Path) -> None:
    class Boom:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 401: denied [redacted]")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Boom()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        try:
            urlopen(
                Request(
                    _url(httpd, f"/api/sessions/{created['id']}/turn"),
                    data=b'{"text":""}',
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("expected 400")
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/sessions/2026-01-01T00-00-00_ffff/turn"),
                    data=b'{"text":"hi"}',
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 404
            assert "ffff" not in exc.read().decode("utf-8")
        else:
            raise AssertionError("expected 404")
        try:
            urlopen(
                Request(
                    _url(httpd, f"/api/sessions/{created['id']}/turn"),
                    data=b'{"text":"hi"}',
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 503
            body = json.loads(exc.read().decode("utf-8"))
            assert body == {"error": "provider HTTP 401: denied [redacted]"}
        else:
            raise AssertionError("expected 503")
        try:
            urlopen(
                Request(_url(httpd, "/api/memory/stats"), method="POST", data=b"{}"),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 405
        else:
            raise AssertionError("expected 405")
        try:
            urlopen(_url(httpd, "/api/sessions/../pyproject.toml"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")
        broken = SessionManager(tmp_path / "sessions").create()
        broken.path.write_text("{bad\n", encoding="utf-8")
        try:
            urlopen(_url(httpd, f"/api/sessions/{broken.id}"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 503
            body = json.loads(exc.read().decode("utf-8"))
            assert body == {"error": "session unreadable"}
            assert "bad" not in str(body)
            assert str(broken.path) not in str(body)
        else:
            raise AssertionError("expected 503")
    finally:
        _stop(httpd, thread)


def test_rejects_non_loopback(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    try:
        make_server(host="0.0.0.0", port=0, webui=WEBUI, facade=facade, chat=_chat(tmp_path))
    except ServeError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("expected refuse")


def test_create_waits_for_in_flight_turn(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    app = _chat(tmp_path, Slow())
    first = app.create()["id"]
    errors: list[BaseException] = []
    result: dict = {}
    created: dict = {}

    def run_turn() -> None:
        try:
            result.update(app.turn(first, "hi"))
        except BaseException as exc:
            errors.append(exc)

    def run_create() -> None:
        try:
            created.update(app.create())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert started.wait(2)
    maker = threading.Thread(target=run_create)
    maker.start()
    maker.join(0.2)
    assert maker.is_alive()
    assert not created
    release.set()
    worker.join(timeout=5)
    maker.join(timeout=5)
    assert not errors
    assert result["reply"] == "late"
    assert result["id"] == first
    assert created["id"] != first
    assert created["messages"] == []
    stored = SessionManager(tmp_path / "sessions").load(first)
    assert [(item.role, item.content) for item in stored.messages] == [
        ("user", "hi"),
        ("assistant", "late"),
    ]


def test_chat_app_shutdown_idempotent(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    created = app.create()
    payload = app.turn(created["id"], "hi")
    assert payload["reply"] == "x"
    app.shutdown()
    app.shutdown()


def test_session_title_truncates() -> None:
    long = "x" * 60
    assert session_title([Message(role="user", content=long, ts="2026-01-01T00:00:00Z")]) == "x" * 47 + "…"
    assert session_title([]) == "Phiên trống"


def test_chat_js_shipped() -> None:
    assert (WEBUI / "js" / "chat.js").is_file()
