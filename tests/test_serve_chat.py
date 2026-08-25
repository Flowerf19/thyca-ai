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
from thyca.sessions import Session, SessionManager
from thyca.sessions.title import fallback_title
from thyca.tools.memory import MemoryFacade

WEBUI = default_webui()


@dataclass
class FakeLLM:
    reply: ChatReply
    requests: list[list[Message]] = field(default_factory=list)
    tools: list = field(default_factory=list)

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.requests.append(list(messages))
        self.tools.append(tools)
        return self.reply


@dataclass
class ScriptedLLM:
    replies: list[ChatReply]
    requests: list[list[Message]] = field(default_factory=list)
    tools: list = field(default_factory=list)

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.requests.append(list(messages))
        self.tools.append(tools)
        if not self.replies:
            raise LLMError("no scripted reply")
        return self.replies.pop(0)


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
        assert listed["sessions"] == []
        body = json.dumps({"text": "ping"}).encode("utf-8")
        turned = _json(httpd, f"/api/sessions/{created['id']}/turn", method="POST", data=body)
        assert turned["reply"] == "pong"
        assert turned["title"] == fallback_title(created["id"])
        assert turned["title"] != "ping"
        roles = [(item["role"], item["content"]) for item in turned["messages"]]
        assert roles == [("user", "ping"), ("assistant", "pong")]
        loaded = _json(httpd, f"/api/sessions/{created['id']}")
        assert loaded["messages"] == turned["messages"]
        assert loaded["title"] == fallback_title(created["id"])
        spoken = _json(httpd, "/api/sessions")
        assert [item["id"] for item in spoken["sessions"]] == [created["id"]]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title is None
        assert [(item.role, item.content) for item in session.messages] == [
            ("user", "ping"),
            ("assistant", "pong"),
        ]
        assert llm.requests[0][-1].content == "ping"
        assert len(llm.requests) == 2
        assert llm.tools[1] is None
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


def test_session_title_display_not_utterance(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-24T10-56-24_abcd.jsonl"
    path.write_text("", encoding="utf-8")
    empty = Session("2026-08-24T10-56-24_abcd", path, [])
    assert session_title(empty) == "Phiên trống"
    spoken = Session(
        "2026-08-24T10-56-24_abcd",
        path,
        [Message(role="user", content="alo", ts="2026-08-24T03:56:24Z")],
    )
    assert session_title(spoken) == "Sáng 24 thg 8"
    named = Session(
        "2026-08-24T10-56-24_abcd",
        path,
        [Message(role="user", content="alo", ts="2026-08-24T03:56:24Z")],
        title="Cà phê với Hòa",
    )
    assert session_title(named) == "Cà phê với Hòa"


def test_notebook_title_persists_and_skips_second_turn(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content='"Cà phê với Hòa."'),
            ChatReply(content="again"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    first = app.turn(created["id"], "alo")
    assert first["title"] == "Cà phê với Hòa"
    assert first["reply"] == "pong"
    second = app.turn(created["id"], "thêm")
    assert second["title"] == "Cà phê với Hòa"
    assert second["reply"] == "again"
    assert len(llm.requests) == 3
    listed = app.list_payload()
    assert listed["sessions"][0]["title"] == "Cà phê với Hòa"
    app.shutdown()


def test_title_failure_keeps_turn_and_fallback(tmp_path: Path) -> None:
    class TitleBoom(FakeLLM):
        async def chat(self, messages, tools=None):
            if len(self.requests) >= 1:
                self.requests.append(list(messages))
                raise LLMError("title failed")
            return await super().chat(messages, tools)

    llm = TitleBoom(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    created = app.create()
    turned = app.turn(created["id"], "alo")
    assert turned["reply"] == "pong"
    assert turned["title"] == fallback_title(created["id"])
    assert turned["title"] != "alo"
    app.shutdown()


def test_chat_js_shipped() -> None:
    assert (WEBUI / "js" / "chat.js").is_file()
    chat = (WEBUI / "js" / "chat.js").read_text(encoding="utf-8")
    assert "flushTools" in chat
    start = chat.index("export async function createChatSession")
    end = chat.index("export function", start + 1)
    body = chat[start:end]
    assert "postJson" not in body
    assert "hydrateChat" not in body
    assert "refreshChatList" in body
    assert "state.activeSessionId = null;" in body
    send = chat.index("export async function sendChatTurn")
    send_end = chat.index("export async function fillChatAt", send)
    assert "page.sessionId" in chat[send:send_end]
    assert "function bindSession" in chat
    css = (WEBUI / "css" / "workspace.css").read_text(encoding="utf-8")
    assert "flex-flow: row wrap" in css
    script = WEBUI.parent / "scripts" / "retitle_sessions.py"
    assert script.is_file()
    assert "retitle_missing" in script.read_text(encoding="utf-8")


def test_create_prunes_previous_blank(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    first = app.create()
    assert app.list_payload()["sessions"] == []
    second = app.create()
    assert not (tmp_path / "sessions" / f"{first['id']}.jsonl").exists()
    assert (tmp_path / "sessions" / f"{second['id']}.jsonl").exists()
    app.shutdown()


def test_session_payload_includes_ask_remember(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    created = app.create()
    assert created["ask_remember"] is False
    turned = app.turn(created["id"], "hi")
    assert turned["ask_remember"] is False
    loaded = app.get_payload(created["id"])
    assert loaded["ask_remember"] is False
    app.shutdown()


def test_idle_remember_nudge_in_webui() -> None:
    html = (WEBUI / "index.html").read_text(encoding="utf-8")
    app = (WEBUI / "js" / "app.js").read_text(encoding="utf-8")
    assert 'id="idle-nudge"' in html
    assert "Phiên im 15 phút" in html
    assert "IDLE_MS = 15 * 60 * 1000" in app
    assert "ask_remember" in app
    assert "idleArmed" in app
    assert "idleFromNudge" in app
    assert not app.rstrip().endswith("armIdle();")
