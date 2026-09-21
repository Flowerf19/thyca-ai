"""Loopback chat HTTP — webui-chat-backend."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from thyca.agent.events import TurnEvent
from thyca.bridge import SENTINEL
from thyca.chat_app import ChatApp, session_title
from thyca.config import ModelCfg, default_config, load, save
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.protocol import Message, ToolCall
from thyca.serve import ServeError, default_webui, make_server
from thyca.sessions import Session, SessionBusy, SessionManager
from thyca.sessions.title import fallback_title
from thyca.tools.mcp import StartupDiagnostic
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


def test_chat_app_mcp_diagnostic_includes_server_name(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeManager:
        async def spawn_all(self, servers):
            return [StartupDiagnostic("remote", False, "failed to start")]

        def tool_specs(self):
            return []

        async def shutdown(self) -> None:
            return

    stderr = StringIO()
    monkeypatch.setattr("thyca.chat_app.MCPManager", FakeManager)
    monkeypatch.setattr("thyca.chat_app.sys.stderr", stderr)
    app = _chat(tmp_path, FakeLLM(ChatReply(content="unused")))
    try:
        assert "remote: failed to start" in stderr.getvalue()
    finally:
        app.shutdown()


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


def test_create_during_in_flight_turn(tmp_path: Path) -> None:
    """create() must not wait on the LLM of an in-flight turn."""
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
    maker.join(2)
    assert not maker.is_alive(), "create blocked by in-flight turn lock"
    assert created.get("id") and created["id"] != first
    assert created["messages"] == []
    # Turn session must still receive the reply after create ran mid-flight.
    release.set()
    worker.join(timeout=5)
    assert not errors
    assert result["reply"] == "late"
    assert result["id"] == first
    stored = SessionManager(tmp_path / "sessions").load(first)
    assert [(item.role, item.content) for item in stored.messages] == [
        ("user", "hi"),
        ("assistant", "late"),
    ]
    app.shutdown()


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


def _stream(httpd, path: str, *, data: bytes, timeout: float = 10):
    request = Request(
        _url(httpd, path),
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    return urlopen(request, timeout=timeout)


def _stream_lines(response) -> list[dict]:
    return [json.loads(raw.decode("utf-8")) for raw in response if raw]


def test_stream_slow_turn_first_line_arrives_before_release(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        response = _stream(
            httpd,
            f"/api/sessions/{created['id']}/turn/stream",
            data=b'{"text":"hello stream"}',
        )
        first = json.loads(response.readline().decode("utf-8"))
        assert response.status == 200
        assert first == {"type": "turn.accepted"}
        # Headers + first line went out while the Slow LLM is still blocked.
        assert release.is_set() is False
        release.set()
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        assert types == [
            "llm.started",
            "llm.finished",
            "session.naming.started",
            "session.naming.finished",
            "turn.completed",
        ]
        assert lines[0]["round"] == 1
        assert lines[1] == {"type": "llm.finished", "round": 1, "tool_count": 0}
        assert lines[3]["updated"] is False
        completed = lines[-1]
        assert completed["type"] == "turn.completed"
        detail = completed["detail"]
        assert detail["id"] == created["id"]
        assert detail["reply"] == "late"
        assert detail["model"]
        assert detail["title"]
        assert [(item["role"], item["content"]) for item in detail["messages"]] == [
            ("user", "hello stream"),
            ("assistant", "late"),
        ]
    finally:
        _stop(httpd, thread)


def test_stream_disconnect_does_not_cancel_turn(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="kept")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        response = _stream(
            httpd,
            f"/api/sessions/{created['id']}/turn/stream",
            data=b'{"text":"stay"}',
        )
        first = json.loads(response.readline().decode("utf-8"))
        assert first == {"type": "turn.accepted"}
        response.close()
        release.set()
        stored = None
        deadline = time.time() + 5
        while time.time() < deadline:
            stored = SessionManager(tmp_path / "sessions").load(created["id"])
            if any(item.role == "assistant" and item.content == "kept" for item in stored.messages):
                break
            time.sleep(0.05)
        assert stored is not None
        assert [(item.role, item.content) for item in stored.messages[:2]] == [
            ("user", "stay"),
            ("assistant", "kept"),
        ]
    finally:
        _stop(httpd, thread)


def test_stream_pre_accept_errors_are_http_json(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        try:
            _stream(
                httpd,
                f"/api/sessions/{created['id']}/turn/stream",
                data=b'{"text":""}',
            )
        except HTTPError as exc:
            assert exc.code == 400
            assert exc.headers.get_content_type() == "application/json"
            body = exc.read().decode("utf-8")
            assert json.loads(body) == {"error": "invalid text"}
            assert "turn." not in body
        else:
            raise AssertionError("expected 400")
        try:
            _stream(
                httpd,
                f"/api/sessions/{created['id']}/turn/stream",
                data=b'{"text":5}',
            )
        except HTTPError as exc:
            assert exc.code == 400
            body = exc.read().decode("utf-8")
            assert json.loads(body) == {"error": "invalid text"}
        else:
            raise AssertionError("expected 400")
        try:
            _stream(
                httpd,
                "/api/sessions/2026-01-01T00-00-00_ffff/turn/stream",
                data=b'{"text":"hi"}',
            )
        except HTTPError as exc:
            assert exc.code == 404
            assert exc.headers.get_content_type() == "application/json"
            body = exc.read().decode("utf-8")
            assert json.loads(body) == {"error": "session not found"}
            assert "turn." not in body
        else:
            raise AssertionError("expected 404")
    finally:
        _stop(httpd, thread)


def test_stream_post_accept_llm_error_ends_with_single_failed(tmp_path: Path) -> None:
    class Boom:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 401: denied [redacted]")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Boom()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        response = _stream(
            httpd,
            f"/api/sessions/{created['id']}/turn/stream",
            data=b'{"text":"hi"}',
        )
        assert response.status == 200
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        assert types[0] == "turn.accepted"
        assert "turn.completed" not in types
        failed = [item for item in lines if item["type"] == "turn.failed"]
        assert failed == [
            {
                "type": "turn.failed",
                "code": "llm_error",
                "message": "provider HTTP 401: denied [redacted]",
            }
        ]
        stored = SessionManager(tmp_path / "sessions").load(created["id"])
        assert [(item.role, item.content) for item in stored.messages] == [
            ("user", "hi")
        ]
    finally:
        _stop(httpd, thread)


def test_stream_headers_and_no_content_length(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="pong"))))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        response = _stream(
            httpd,
            f"/api/sessions/{created['id']}/turn/stream",
            data=b'{"text":"ping"}',
        )
        assert response.status == 200
        assert response.headers.get_content_type() == "application/x-ndjson"
        cache = response.headers.get("Cache-Control", "")
        assert "no-store" in cache
        assert "no-transform" in cache
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("Content-Length") is None
        lines = _stream_lines(response)
        assert lines[-1]["type"] == "turn.completed"
    finally:
        _stop(httpd, thread)


def test_stream_tool_events_ordered_and_clean(tmp_path: Path) -> None:
    call = ToolCall(
        id="call-1",
        name="bash",
        arguments={"cmd": "echo hi"},
        parse_error="invalid arguments",
    )
    llm = ScriptedLLM(
        [
            ChatReply(content=None, tool_calls=[call]),
            ChatReply(content="final answer"),
        ]
    )
    httpd, thread = _start(tmp_path, _chat(tmp_path, llm))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        response = _stream(
            httpd,
            f"/api/sessions/{created['id']}/turn/stream",
            data=b'{"text":"run the bash tool now"}',
        )
        raws = list(response)
        lines = [json.loads(raw.decode("utf-8")) for raw in raws]
        assert [item["type"] for item in lines] == [
            "turn.accepted",
            "llm.started",
            "llm.finished",
            "tool.started",
            "tool.finished",
            "llm.started",
            "llm.finished",
            "session.naming.started",
            "session.naming.finished",
            "turn.completed",
        ]
        assert lines[2] == {"type": "llm.finished", "round": 1, "tool_count": 1}
        assert lines[3] == {
            "type": "tool.started",
            "round": 1,
            "call_id": "call-1",
            "name": "bash",
        }
        assert lines[4] == {
            "type": "tool.finished",
            "round": 1,
            "call_id": "call-1",
            "name": "bash",
            "ok": False,
        }
        # Intermediate lines never carry user text, tool args, results, or stacks.
        blob = b"".join(raws[:-1]).decode("utf-8")
        assert "run the bash tool now" not in blob
        assert "echo hi" not in blob
        assert "invalid arguments" not in blob
        assert "Traceback" not in blob
    finally:
        _stop(httpd, thread)


def test_stream_sentinel_without_terminal_writes_fallback_failure(
    tmp_path: Path, monkeypatch
) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="pong"))))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")

        def sentinel_only_worker(app, session_id, text, items, state, **_kwargs):
            items.put(TurnEvent(type="turn.accepted"))
            items.put(SENTINEL)

        monkeypatch.setattr("thyca.bridge.bridge_worker", sentinel_only_worker)
        response = _stream(
            httpd,
            f"/api/sessions/{created['id']}/turn/stream",
            data=b'{"text":"hi"}',
        )
        assert response.status == 200
        lines = _stream_lines(response)
        assert [item["type"] for item in lines] == ["turn.accepted", "turn.failed"]
        assert lines[-1] == {
            "type": "turn.failed",
            "code": "chat_unavailable",
            "message": "chat unavailable",
        }
    finally:
        _stop(httpd, thread)


def test_chat_js_shipped() -> None:
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    view = (WEBUI / "backend" / "chat-view.js").read_text(encoding="utf-8")
    thinking = (WEBUI / "backend" / "chat-thinking.js").read_text(encoding="utf-8")
    api = (WEBUI / "backend" / "api.js").read_text(encoding="utf-8")
    css = (WEBUI / "backend.css").read_text(encoding="utf-8")
    styles = (WEBUI / "styles.css").read_text(encoding="utf-8")

    assert 'postNdjson(' in app
    assert '/turn/stream' in app
    assert 'postJson("/api/sessions", {})' in app
    assert "function fillComposerControls" in app
    assert "function composerTurn" in app
    assert "/turn/cancel" in app
    assert "composerTurn({ retry: true })" in app
    assert 'getJson("/api/config")' in app
    assert "formatMarkdown" in view
    assert "chat-brand" in view
    assert "brandState" in view
    assert "createThinkingNote" in view
    assert "settledThinkingNote" in view
    assert "ambientLineForEvent" not in view
    assert not (WEBUI / "backend" / "chat-ambient.js").exists()
    assert "tool.started" in view
    assert "llm.thinking" in view
    assert "resetLiveStatus(live, startedAt)" in app
    assert "bindThinkingToggle" in thinking
    assert "thought-footer" in thinking
    assert "search-event" in thinking
    assert "giây" in thinking
    assert "settledThinkingNote" in thinking
    assert "timer = setInterval(tick, 1000);" in thinking
    assert "const startTimer = () =>" in thinking
    assert "startTimer();\n      tick();" in thinking
    assert "state.startedAt = Date.now();\n      onElapsed?.(0);" not in thinking
    assert ".thought-footer" in styles
    assert ".search-event" in styles
    assert "turn.completed" in api
    assert "Tools used:" not in view
    assert ".live-status" in css
    script = WEBUI.parent.parent / "scripts" / "retitle_sessions.py"
    assert script.is_file()
    assert "retitle_missing" in script.read_text(encoding="utf-8")


def test_chat_nav_opens_new_session() -> None:
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    html = (WEBUI / "index.html").read_text(encoding="utf-8")
    new_session = app[app.index("function newSession()") : app.index("async function ensureSession")]
    ensure_session = app[app.index("async function ensureSession") : app.index("async function sendMessage")]

    assert 'id="new-session"' in html
    assert 'id="message-list"' in html
    assert 'id="composer"' in html
    assert 'querySelector("#new-session")' in app
    assert "state.activeId = \"\";" in new_session
    assert "postJson" not in new_session
    assert 'postJson("/api/sessions", {})' in ensure_session
    assert "sessionId = await ensureSession()" in app

    provider = (WEBUI / "provider.js").read_text(encoding="utf-8")
    assert 'getJson("/api/config")' in provider
    assert 'postJson("/api/config"' in provider
    assert 'postJson("/api/onboarding/verify"' in provider
    assert "contextTokens" in provider
    assert not (WEBUI / "staff").exists()


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
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    assert 'id="idle-nudge"' in html
    assert "Phiên im 15 phút" in html
    assert "IDLE_MS = 15 * 60 * 1000" in app
    assert "ask_remember" in app
    assert "idleArmed" in app
    assert "idleFromNudge" in app
    assert not app.rstrip().endswith("armIdle();")

def test_retry_hook_emits_llm_retry_events(tmp_path: Path) -> None:
    """ChatApp wires OpenAIChat-style retry hooks to non-error TurnEvents."""

    class Recovering:
        def __init__(self) -> None:
            self._hook = None
            self.n = 0

        def set_retry_hook(self, hook) -> None:
            self._hook = hook

        async def chat(self, messages, tools=None):
            self.n += 1
            # Only the first (turn) call simulates a transient retry notify.
            if self.n == 1 and self._hook:
                self._hook(1, 3)
            return ChatReply(content="ok" if self.n == 1 else '"Tên phiên."')

    app = _chat(tmp_path, Recovering())
    created = app.create()
    events: list[TurnEvent] = []
    payload = app.turn(created["id"], "ping", event_sink=events.append)
    assert payload["reply"] == "ok"
    retries = [e for e in events if e.type == "llm.retry"]
    assert retries == [TurnEvent(type="llm.retry", attempt=1, max_attempts=3)]
    app.shutdown()


def test_aborted_static_and_body_keep_server_quiet(tmp_path: Path, capsys) -> None:
    """Client ngắt mid-response/mid-body (đổi Trace↔Chat): im lặng, server sống."""
    import socket

    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        port = httpd.server_address[1]
        # 1. GET đóng ngay sau request: wfile.flush() cuối handle_one_request
        #    dâng BrokenPipe lên handle_error (không qua _send).
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(b"GET /api/sessions HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        finally:
            sock.close()
        # 2. POST khai Content-Length lớn rồi đóng giữa body.
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(
                b"POST /api/sessions HTTP/1.1\r\nHost: x\r\n"
                b"Content-Length: 5000\r\nContent-Type: application/json\r\n"
                b"Connection: close\r\n\r\n{\"a\":"
            )
        finally:
            sock.close()
        # 3. Static file đóng ngay sau request.
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        finally:
            sock.close()
        time.sleep(0.5)
        assert thread.is_alive()
        payload = _json(httpd, "/api/sessions")
        assert payload["sessions"] == []
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "BrokenPipeError" not in captured.err
    finally:
        _stop(httpd, thread)


def test_session_detail_tags_skill_loads(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skill_md = skills_root / "create-skill"
    skill_md.mkdir(parents=True)
    (skill_md / "SKILL.md").write_text("---\nname: create-skill\n---\n", encoding="utf-8")
    llm = ScriptedLLM(
        [
            ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="read",
                        arguments={"path": str(skill_md / "SKILL.md")},
                    ),
                    ToolCall(id="call-2", name="read", arguments={"path": str(tmp_path / "notes.md")}),
                ],
            ),
            ChatReply(content="xong"),
        ]
    )
    httpd, thread = _start(tmp_path, _chat(tmp_path, llm))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        body = json.dumps({"text": "viết skill"}).encode("utf-8")
        _json(httpd, f"/api/sessions/{created['id']}/turn", method="POST", data=body)
        detail = _json(httpd, f"/api/sessions/{created['id']}")
        calls = next(
            item["tool_calls"] for item in detail["messages"] if item.get("tool_calls")
        )
        # The skill read carries its name so replay can label it a skill; the
        # ordinary read stays a plain tool call, and arguments stay off payload.
        assert calls[0] == {"id": "call-1", "name": "read", "skill": "create-skill"}
        assert calls[1] == {"id": "call-2", "name": "read"}
    finally:
        _stop(httpd, thread)


def test_empty_submit_nudges_without_sending() -> None:
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    html = (WEBUI / "index.html").read_text(encoding="utf-8")
    css = (WEBUI / "styles.css").read_text(encoding="utf-8")
    send_message = app[app.index("async function sendMessage()") : app.index("function bind()")]
    empty_branch = send_message[: send_message.index("if (composerBusy()) return;")]

    # The hint is the accessible half of the nudge: motion needs text too.
    assert 'id="composer-hint"' in html
    assert 'role="status"' in html
    assert 'querySelector("#composer-hint")' in app
    assert "Chưa có nội dung để gửi." in app
    # An empty submit shakes and returns without touching the network.
    assert "shakeComposer()" in empty_branch
    assert "fetch(" not in empty_branch
    assert "@keyframes composer-nudge" in css
    assert ".composer.is-nudging" in css


def test_busy_session_reports_running_and_refuses_a_second_turn(tmp_path: Path) -> None:
    """A turn in flight is visible to the UI and refuses a second one."""
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        session_id = created["id"]
        assert _json(httpd, f"/api/sessions/{session_id}")["running"] is False
        response = _stream(
            httpd,
            f"/api/sessions/{session_id}/turn/stream",
            data=b'{"text":"first"}',
        )
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted"}
        try:
            # The reloaded page reads exactly this to know a turn is in flight.
            detail = _json(httpd, f"/api/sessions/{session_id}")
            assert detail["running"] is True
            assert detail["started_at"]
            request = Request(
                _url(httpd, f"/api/sessions/{session_id}/turn"),
                data=b'{"text":"second"}',
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                urlopen(request, timeout=5)
            except HTTPError as exc:
                assert exc.code == 409
                assert json.loads(exc.read().decode("utf-8")) == {"error": "session busy"}
            else:
                raise AssertionError("expected 409")
        finally:
            response.close()
            release.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            if _json(httpd, f"/api/sessions/{session_id}")["running"] is False:
                break
            time.sleep(0.05)
        assert _json(httpd, f"/api/sessions/{session_id}")["running"] is False
    finally:
        _stop(httpd, thread)


def test_follow_stream_replays_and_tails_a_running_turn(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        session_id = created["id"]
        starter = _stream(
            httpd,
            f"/api/sessions/{session_id}/turn/stream",
            data=b'{"text":"hello follow"}',
        )
        assert json.loads(starter.readline().decode("utf-8")) == {"type": "turn.accepted"}
        assert started.wait(2)
        follower = urlopen(
            Request(
                _url(httpd, f"/api/sessions/{session_id}/turn/stream"),
                method="GET",
            ),
            timeout=10,
        )
        assert follower.status == 200
        assert follower.headers.get_content_type() == "application/x-ndjson"
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted"}
        release.set()
        starter_types = [item["type"] for item in _stream_lines(starter)]
        follower_lines = _stream_lines(follower)
        follower_types = [item["type"] for item in follower_lines]
        assert starter_types[-1] == "turn.completed"
        assert follower_types[-1] == "turn.completed"
        assert "llm.started" in follower_types
        assert follower_lines[-1]["detail"]["reply"] == "late"
    finally:
        _stop(httpd, thread)


def test_stream_emits_thinking_deltas_and_persists(tmp_path: Path) -> None:
    class ThinkingLLM:
        async def chat(self, messages, tools=None, on_reasoning=None):
            if on_reasoning:
                on_reasoning("First I check")
                on_reasoning(" the chords")
            return ChatReply(content="yes", reasoning="First I check the chords")

    httpd, thread = _start(tmp_path, _chat(tmp_path, ThinkingLLM()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        response = _stream(
            httpd,
            f"/api/sessions/{created['id']}/turn/stream",
            data=b'{"text":"kalimba"}',
        )
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        thinking = [item for item in lines if item["type"] == "llm.thinking"]
        assert types[0] == "turn.accepted"
        assert types[1] == "llm.started"
        assert thinking == [
            {"type": "llm.thinking", "round": 1, "delta": "First I check"},
            {"type": "llm.thinking", "round": 1, "delta": " the chords"},
        ]
        assert "llm.finished" in types
        assert types[-1] == "turn.completed"
        detail = _json(httpd, f"/api/sessions/{created['id']}")
        assistant = next(item for item in detail["messages"] if item["role"] == "assistant" and item.get("content") == "yes")
        assert assistant["reasoning"] == "First I check the chords"
        assert assistant["content"] == "yes"
    finally:
        _stop(httpd, thread)


def test_stream_emits_content_deltas_alongside_thinking(tmp_path: Path) -> None:
    class StreamingLLM:
        async def chat(self, messages, tools=None, on_reasoning=None, on_content=None):
            if on_reasoning:
                on_reasoning("I will greet")
            if on_content:
                on_content("Xin ")
                on_content("chào")
            return ChatReply(content="Xin chào", reasoning="I will greet")

    httpd, thread = _start(tmp_path, _chat(tmp_path, StreamingLLM()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        response = _stream(
            httpd,
            f"/api/sessions/{created['id']}/turn/stream",
            data=b'{"text":"kalimba"}',
        )
        lines = _stream_lines(response)
        content = [item for item in lines if item["type"] == "llm.content"]
        assert content == [
            {"type": "llm.content", "round": 1, "delta": "Xin "},
            {"type": "llm.content", "round": 1, "delta": "chào"},
        ]
        assert any(item["type"] == "llm.thinking" for item in lines)
        assert lines[-1]["type"] == "turn.completed"
    finally:
        _stop(httpd, thread)


def test_follow_stream_idle_or_missing(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        try:
            urlopen(
                Request(
                    _url(httpd, f"/api/sessions/{created['id']}/turn/stream"),
                    method="GET",
                ),
                timeout=5,
            )
        except HTTPError as exc:
            assert exc.code == 409
            assert json.loads(exc.read().decode("utf-8")) == {"error": "session idle"}
        else:
            raise AssertionError("expected 409")
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/sessions/2026-01-01T00-00-00_ffff/turn/stream"),
                    method="GET",
                ),
                timeout=5,
            )
        except HTTPError as exc:
            assert exc.code == 404
            assert json.loads(exc.read().decode("utf-8")) == {"error": "session not found"}
        else:
            raise AssertionError("expected 404")
    finally:
        _stop(httpd, thread)


def test_follow_stream_survives_starter_disconnect(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="kept")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        session_id = created["id"]
        starter = _stream(
            httpd,
            f"/api/sessions/{session_id}/turn/stream",
            data=b'{"text":"stay"}',
        )
        assert json.loads(starter.readline().decode("utf-8")) == {"type": "turn.accepted"}
        assert started.wait(2)
        follower = urlopen(
            Request(
                _url(httpd, f"/api/sessions/{session_id}/turn/stream"),
                method="GET",
            ),
            timeout=10,
        )
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted"}
        starter.close()
        release.set()
        lines = _stream_lines(follower)
        assert lines[-1]["type"] == "turn.completed"
        assert lines[-1]["detail"]["reply"] == "kept"
    finally:
        _stop(httpd, thread)


def test_webui_keeps_streaming_card_across_session_switch() -> None:
    """Leaving a streaming session and coming back must not reset its card.

    renderDetail replaces #message-list, which detaches the live card. Keeping
    the object per session (and re-appending it) is what lets the in-flight
    stream keep drawing into it; a fresh card would read as a bare status line
    with no usage row and no further updates.
    """
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    render = app[app.index("function renderDetail(detail)") : app.index("function watchRunning(sessionId)")]
    send_message = app[app.index("async function sendMessage()") : app.index("function bind()")]

    assert "const liveTurns = new Map();" in app
    assert "liveTurns.set(sessionId, live);" in send_message
    assert "liveTurns.delete(sessionId);" in send_message
    # Reuse the card for the session on screen (resume() restarts the clock
    # tick that stopped while the card was detached); mint one only when there
    # is none to reuse, counted from the turn's real started_at.
    assert "const live = liveTurns.get(state.activeId);" in render
    assert "el.messageList.append(live.article);" in render
    assert "live.thinking?.resume();" in render
    assert "createLiveStatus(el.messageList, detail.started_at)" in render
    # A detached card must not drag the visible conversation around.
    assert "if (state.activeId === sessionId) scrollToBottom();" in send_message


def test_webui_follows_a_turn_it_did_not_start() -> None:
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    view = (WEBUI / "backend" / "chat-view.js").read_text(encoding="utf-8")
    status = (WEBUI / "backend" / "chat-status.js").read_text(encoding="utf-8")
    load_session = app[app.index("async function loadSession") : app.index("function newSession")]
    watch = app[app.index("function watchRunning(sessionId)") : app.index("async function loadSession")]
    composer = app[app.index("function composerBusy()") : app.index("function setSending")]

    # Reload mid-turn: attach GET /turn/stream so the card receives the same
    # events as the starter. Poll is only the fallback if that stream is gone.
    assert "RUNNING_POLL_MS = 2000" in app
    assert "detail && detail.running === true" in app
    assert "createLiveStatus(el.messageList, detail.started_at)" in app
    assert "async function followTurn" in app
    assert "void followTurn(sessionId, detail.started_at)" in load_session
    assert "abortFollow()" in load_session
    assert "getNdjson" in app
    assert "createLiveStatus" in view
    # No turn status is invented for the reload case: the card reads like any
    # in-flight turn, from copy that already exists.
    assert "Đang trả lời lượt trước" not in status
    assert "Phiên đang trả lời" not in status
    assert "SESSION_BUSY_STATUS" not in status

    # A single failed GET must not strand the composer behind a "running"
    # state nobody polls any more.
    assert "RUNNING_POLL_MAX_FAILURES" in app
    assert "failures += 1" in watch
    assert "setRunning(false)" in watch
    assert "RUNNING_POLL_MS * failures" in watch

    # Blocking is per session: only sessions this tab streams from have their
    # composer disabled, and finishing one turn must not orphan the other's
    # reader — hence a Set of streaming sessions, not a single id.
    assert "const streamingSessions = new Set();" in app
    assert "streamingSessions.has(sessionKey())" in composer
    assert "streamingSessions.delete(sessionId)" in app
    # Sending to one session must not disable another session's composer.
    assert "state.sending || state.running" not in composer

    # The 409 branch must not depend on an identifier the module never
    # imported (a missing ApiError import threw at runtime, not at load).
    # Every name the module uses has to be in the import list, not just the
    # first one: assert the name itself, wherever it sits in the braces.
    import re

    names = re.search(r'import \{([^}]*)\}\s*from "\./backend/api\.js"', app)
    assert names, "app.js must import from ./backend/api.js"
    imported = {name.strip() for name in names.group(1).split(",") if name.strip()}
    assert {
        "ApiError",
        "deleteJson",
        "getJson",
        "getNdjson",
        "patchJson",
        "postJson",
        "postNdjson",
    } <= imported
    assert "error instanceof ApiError" in app
    assert "state.busy" not in load_session
    # The sidebar and New session stay usable while a turn runs.
    assert "el.newSession.disabled" not in app
    assert "button.disabled = busy" not in app


def test_session_summary_counts_turns_the_way_trace_does(tmp_path: Path) -> None:
    """One turn per user message — not the raw message count.

    Tool rounds inflate message_count (every assistant round and tool result
    is a line), which would read as "5 lượt" for a single question.
    """
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    try:
        created = app.create()
        app.turn(created["id"], "ping")
        app.turn(created["id"], "pong?")
        listed = app.list_payload()["sessions"]
        assert [item["turns"] for item in listed] == [2]
        assert listed[0]["message_count"] == 4
    finally:
        app.shutdown()


def test_rename_and_delete_over_http(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        sid = created["id"]
        body = json.dumps({"text": "ping"}).encode("utf-8")
        _json(httpd, f"/api/sessions/{sid}/turn", method="POST", data=body)

        patch = json.dumps({"title": "  Kế hoạch   tuần này "}).encode("utf-8")
        renamed = _json(httpd, f"/api/sessions/{sid}", method="PATCH", data=patch)
        assert renamed == {"ok": True, "id": sid, "title": "Kế hoạch tuần này"}
        detail = _json(httpd, f"/api/sessions/{sid}")
        assert detail["title"] == "Kế hoạch tuần này"
        listed = _json(httpd, "/api/sessions")
        assert listed["sessions"][0]["title"] == "Kế hoạch tuần này"

        # The title is stored on disk as a meta line carrying its source, so
        # the naming policy knows not to vet it.
        stored = SessionManager(tmp_path / "sessions").load(sid)
        assert stored.title == "Kế hoạch tuần này"
        assert stored.title_source == "user"
        assert '"source": "user"' in (tmp_path / "sessions" / f"{sid}.jsonl").read_text(
            encoding="utf-8"
        )

        # Empty title is refused, and so is a body that is not a title.
        for bad in ({"title": "   "}, {"title": 7}, {}):
            data = json.dumps(bad).encode("utf-8")
            try:
                _json(httpd, f"/api/sessions/{sid}", method="PATCH", data=data)
            except HTTPError as exc:
                assert exc.code == 400
            else:
                raise AssertionError("expected 400")

        deleted = _json(httpd, f"/api/sessions/{sid}", method="DELETE")
        assert deleted == {"ok": True, "id": sid}
        assert not (tmp_path / "sessions" / f"{sid}.jsonl").exists()
        try:
            urlopen(_url(httpd, f"/api/sessions/{sid}"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404 after delete")
        # The notebook is gone; the memory it left behind is not touched.
        assert _json(httpd, "/api/sessions")["sessions"] == []

        # Dropping a notebook is idempotent: a missing file is already the
        # wanted end state, so a second DELETE from another tab is not an
        # error. A malformed id is still refused.
        assert _json(httpd, f"/api/sessions/{sid}", method="DELETE")["ok"] is True
        try:
            _json(
                httpd,
                f"/api/sessions/{sid}",
                method="PATCH",
                data=json.dumps({"title": "x"}).encode("utf-8"),
            )
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404 for PATCH on a missing session")
        for method in ("PATCH", "DELETE"):
            data = json.dumps({"title": "x"}).encode("utf-8") if method == "PATCH" else None
            try:
                _json(httpd, "/api/sessions/not-an-id", method=method, data=data)
            except HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError("expected 404 for a malformed id")
    finally:
        _stop(httpd, thread)


def test_delete_refuses_a_session_mid_turn(tmp_path: Path) -> None:
    """A running turn is still appending: deleting under it is a 409."""
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    app = _chat(tmp_path, Slow())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        sid = created["id"]

        worker = threading.Thread(target=lambda: app.turn(sid, "ping"), daemon=True)
        worker.start()
        assert started.wait(timeout=5)
        try:
            _json(httpd, f"/api/sessions/{sid}", method="DELETE")
        except HTTPError as exc:
            assert exc.code == 409
        else:
            raise AssertionError("expected 409 for a running session")
        assert (tmp_path / "sessions" / f"{sid}.jsonl").exists()
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert (tmp_path / "sessions" / f"{sid}.jsonl").exists()
        # Once the turn has landed the same call goes through.
        assert _json(httpd, f"/api/sessions/{sid}", method="DELETE")["ok"] is True
    finally:
        release.set()
        _stop(httpd, thread)
        app.shutdown()


def test_webui_has_row_actions_for_rename_and_delete() -> None:
    """The sidebar row carries both actions; hover reveals, keyboard reaches."""
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    html = (WEBUI / "index.html").read_text(encoding="utf-8")
    css = (WEBUI / "styles.css").read_text(encoding="utf-8")
    api = (WEBUI / "backend" / "api.js").read_text(encoding="utf-8")

    assert 'patchJson(`/api/sessions/${encodeURIComponent(id)}`, { title })' in app
    assert 'deleteJson(`/api/sessions/${encodeURIComponent(id)}`)' in app
    assert "export function patchJson" in api
    assert "export function deleteJson" in api

    # Both row actions keep the dialog's own buttons reachable, and the row
    # button stays a sibling so pressing it never triggers an action.
    assert 'row.append(button, actions);' in app
    assert "button.addEventListener(\"click\", onClick);" in app

    # Two dialogs, the same .screen-dialog the mục lục uses.
    assert 'id="rename-dialog"' in html
    assert 'class="screen-dialog"' in html
    assert 'id="delete-dialog"' in html
    assert 'id="rename-name"' in html
    assert 'maxlength="120"' in html
    assert "screen-button is-danger" in html

    # Words, not glyphs: no icon element and no mask art on the actions.
    assert "session-action-icon" not in app
    assert "session-action-icon" not in css
    # The send button still masks the pen; the row actions do not use art.
    action_block = css[css.index("/* Row actions:"):css.index(".workspace {")]
    assert "but-may.svg" not in action_block
    assert "tay.svg" not in action_block
    # Both carry their word as text, and name their row in the label.
    assert '"Đổi tên",' in app
    assert '"Xóa", () =>' in app
    assert "`Đặt tên cho phiên ${title}`" in app
    assert "`Xóa phiên ${title}`" in app
    action = css[css.index(".session-action {") :][:700]
    assert "text-decoration: underline;" in action
    assert "text-decoration-thickness: 1px;" in action
    assert "text-underline-offset: 3px;" in action
    assert "font-family: var(--font-ui);" in action
    assert "font-size: 0.72rem;" in action
    # Hidden until the row is pointed at, reachable by keyboard, and visible
    # on touch where hover does not exist.
    assert ".session-row:hover .session-actions" in css
    assert ".session-row:focus-within .session-actions" in css
    assert "@media (hover: none)" in css
    # Centred on the row; both lines make room for the words.
    assert "inset-block: 0;" in css
    assert "align-items: center;" in css
    assert "padding-inline-end: 5.2rem;" in css
    # Second line carries when and how many turns.
    assert "lượt`;" in app or "lượt" in app
    assert "function sessionMeta(session)" in app


def test_rename_midturn_survives_the_agent_naming_step(tmp_path: Path) -> None:
    """Naming must not append over a title the user typed during the turn.

    The turn holds its own Session snapshot from load time; the sidebar writes
    a meta line to the same file. Without a re-read the agent's naming step
    would append its own line and the stored title would be the model's.
    """
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    class Slow:
        async def chat(self, messages, tools=None):
            calls["n"] += 1
            if calls["n"] == 1:
                started.set()
                await asyncio.to_thread(release.wait)
                return ChatReply(content="pong")
            # The naming step proposes a title the policy would normally accept:
            # it differs from both the user's words and the reply.
            return ChatReply(content="Nhịp sáng")

    app = _chat(tmp_path, Slow())
    try:
        created = app.create()
        sid = created["id"]
        worker = threading.Thread(target=lambda: app.turn(sid, "ping"), daemon=True)
        worker.start()
        assert started.wait(timeout=5)

        # The user names it from the sidebar while the turn is still running.
        assert app.rename_session(sid, "Tên tôi tự đặt") == "Tên tôi tự đặt"
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()

        stored = SessionManager(tmp_path / "sessions").load(sid)
        assert stored.title == "Tên tôi tự đặt"
        assert stored.title_source == "user"
        assert app.get_payload(sid)["title"] == "Tên tôi tự đặt"
    finally:
        release.set()
        app.shutdown()


def test_delete_racing_a_claim_does_not_lose_the_transcript(tmp_path: Path) -> None:
    """The delete gate and the claim share one lock.

    Before: ``delete_session`` snapshotted the in-flight map and then unlinked,
    so a turn claiming in that window was not protected — the file went away
    under it and its next append re-created it truncated, dropping history.
    """
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    try:
        created = app.create()
        sid = created["id"]
        app.turn(sid, "câu hỏi đầu")
        path = tmp_path / "sessions" / f"{sid}.jsonl"
        assert path.exists()

        # Hold a claim exactly as a live turn does, then ask the delete to run.
        app._turns.claim(sid)
        try:
            app.delete_session(sid)
        except SessionBusy:
            pass
        else:
            raise AssertionError("delete must refuse a claimed session")
        assert path.exists()
        app._turns.release(sid)

        # Free again: the same call drops the notebook.
        app.delete_session(sid)
        assert not path.exists()
    finally:
        app.shutdown()


def test_webui_submits_a_rename_or_delete_once() -> None:
    """A double submit must not send the same request twice."""
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    rename = app[app.index("async function submitRename()") : app.index("function openDelete")]
    remove = app[app.index("async function submitDelete()") : app.index("function rememberActiveSession")]

    assert "if (state.saving) return;" in rename
    assert "if (state.saving) return;" in remove
    assert "state.saving = true;" in rename and "state.saving = true;" in remove
    assert "finally {\n    state.saving = false;" in rename
    assert "finally {\n    state.saving = false;" in remove
    assert "saving: false," in app


def test_chat_row_uses_the_shared_session_item() -> None:
    """Chat sidebar rows use the same .session-item grid as Hồ sơ / Nhật ký;
    Trace moved into the dashboard and renders its session list there as
    .journal-entry rows on the shared kit instead."""
    css = (WEBUI / "styles.css").read_text(encoding="utf-8")
    app = (WEBUI / "app.js").read_text(encoding="utf-8")
    trace = (WEBUI / "trace.js").read_text(encoding="utf-8")

    # No restack wrapper: icon and name sit on the item, like profile.js.
    assert "session-body" not in css
    assert "session-body" not in app
    assert "session-body" not in trace
    assert "button.append(icon, name, time);" in app
    # Trace's main-area session list: one journal entry per session, the
    # title as a journal row-title button, meta line from the shared kit.
    assert 'item.className = "journal-entry";' in trace
    assert 'open.className = "journal-row-title trace-session-open";' in trace
    assert 'meta.className = "journal-meta";' in trace
    assert "item.append(stampNode(group.startedAt), body);" in trace

    icon = css[css.index(".session-icon {") : css.index(".session-name {")]
    icon = icon[: icon.index("}")]
    assert "width: 1.85rem;" in icon
    assert "height: 1.85rem;" in icon
    assert "grid-column: 1;" in icon
    assert "grid-row: 1;" in icon
    assert ".session-item:has(time) .session-icon {" in css
    span_icon = css[css.index(".session-item:has(time) .session-icon {") :]
    span_icon = span_icon[: span_icon.index("}")]
    assert "grid-row: 1 / -1;" in span_icon

    name = css[css.index(".session-name {") :]
    name = name[: name.index("}")]
    assert "grid-column: 2;" in name
    assert "grid-row: 1;" in name

    time = css[css.index(".session-item time {") :]
    time = time[: time.index("}")]
    assert "grid-column: 2;" in time
    assert "grid-row: 2;" in time
    assert "justify-self: start;" in time
    assert "font-family: var(--font-reading);" in time
    assert "font-size: 0.72rem;" in time
    assert "font-style: italic;" in time

    item = css[css.index(".session-item {") :]
    item = item[: item.index("}")]
    assert "grid-template-columns: 1.85rem minmax(0, 1fr) auto;" in item
    assert "min-height: 3.65rem;" in item

    narrow = css[css.index("@media (max-width: 75rem)") :]
    assert "grid-template-columns: 1.75rem minmax(0, 1fr);" in narrow[:2000]
    assert "1.5rem minmax(0, 1fr)" not in narrow[:2000]


def test_hovering_a_row_keeps_its_divider() -> None:
    """The pointer wash does not hide the line above the row it lights up."""
    css = (WEBUI / "styles.css").read_text(encoding="utf-8")

    # The wash is the only hover change: the row's own top rule stays, so the
    # divider between it and the session above keeps showing.
    assert ".session-item:not(.is-active):hover::before {" in css
    assert ".session-item:not(.is-active):hover {" not in css
    assert "border-block-start-color: transparent;" not in css
    # Selecting a row used the same trick: `border-color: transparent` dropped
    # the line above the active session. The stripe and wash stay; the rule goes.
    assert ".session-item.is-active {" not in css
    assert "border-color: transparent;" not in css


def _http_error(httpd, path: str, data: bytes, timeout: float = 5) -> tuple[int, dict]:
    request = Request(
        _url(httpd, path),
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urlopen(request, timeout=timeout)
    except HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body
    raise AssertionError("expected HTTPError")


def test_stream_model_and_effort_reach_provider(tmp_path: Path, monkeypatch) -> None:
    captured: list = []

    class Spy:
        async def chat(self, messages, tools=None):
            return ChatReply(content="pong")

        async def aclose(self):
            return

    def create(kind, provider):
        captured.append(provider)
        return Spy()

    monkeypatch.setattr("thyca.chat_app.ConnectFactory.create", create)
    cfg = default_config()
    cfg = replace(
        cfg,
        models={"card-model": ModelCfg()},
        defaultModel="gpt-4o-mini",
    )
    save(cfg, tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        path = f"/api/sessions/{created['id']}/turn/stream"
        omitted = _stream_lines(_stream(httpd, path, data=b'{"text":"omit"}'))
        assert omitted[-1]["type"] == "turn.completed"
        assert captured[0].model == "gpt-4o-mini"
        assert captured[0].reasoningEffort == "high"
        body = json.dumps(
            {"text": "override", "model": "card-model", "effort": "low"}
        ).encode("utf-8")
        overridden = _stream_lines(_stream(httpd, path, data=body))
        assert overridden[-1]["type"] == "turn.completed"
        assert captured[-1].model == "card-model"
        assert captured[-1].reasoningEffort == "low"
        card = replace(
            cfg,
            models={"card-model": ModelCfg(reasoningEffort="low")},
            defaultModel="gpt-4o-mini",
        )
        save(card, tmp_path / "config.json")
        inherited = _stream_lines(
            _stream(
                httpd,
                path,
                data=b'{"text":"card-effort","model":"card-model"}',
            )
        )
        assert inherited[-1]["type"] == "turn.completed"
        assert captured[-1].model == "card-model"
        assert captured[-1].reasoningEffort == "low"
        stored = SessionManager(tmp_path / "sessions").load(created["id"])
        assistants = [
            item
            for item in stored.messages
            if item.role == "assistant" and (item.meta or {}).get("kind") != "naming"
        ]
        assert assistants[-1].meta["model"] == "card-model"
        assert assistants[0].meta["model"] == "gpt-4o-mini"
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_stream_rejects_unknown_model_and_bad_effort(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        path = f"/api/sessions/{created['id']}/turn/stream"
        code, body = _http_error(
            httpd, path, b'{"text":"hi","model":"not-configured"}'
        )
        assert code == 400
        assert body == {"error": "invalid model"}
        code, body = _http_error(httpd, path, b'{"text":"hi","effort":""}')
        assert code == 400
        assert body == {"error": "invalid effort"}
        code, body = _http_error(httpd, path, b'{"text":"hi","model":""}')
        assert code == 400
        assert body == {"error": "invalid model"}
        code, body = _http_error(
            httpd, path, json.dumps({"text": "hi", "model": "a" * 201}).encode()
        )
        assert code == 400
        assert body == {"error": "invalid model"}
        code, body = _http_error(
            httpd, path, json.dumps({"text": "hi", "model": "a\nb"}).encode()
        )
        assert code == 400
        assert body == {"error": "invalid model"}
    finally:
        _stop(httpd, thread)


def test_cancel_in_flight_turn(tmp_path: Path) -> None:
    started = threading.Event()
    gate = asyncio.Event()

    class Blocked:
        async def chat(self, messages, tools=None):
            started.set()
            await gate.wait()
            return ChatReply(content="should-not")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Blocked()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        session_id = created["id"]
        response = _stream(
            httpd,
            f"/api/sessions/{session_id}/turn/stream",
            data=b'{"text":"hold"}',
        )
        first = json.loads(response.readline().decode("utf-8"))
        assert first == {"type": "turn.accepted"}
        assert started.wait(2)
        follower = urlopen(
            Request(
                _url(httpd, f"/api/sessions/{session_id}/turn/stream"),
                method="GET",
            ),
            timeout=10,
        )
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted"}
        cancelled = _json(
            httpd,
            f"/api/sessions/{session_id}/turn/cancel",
            method="POST",
            data=b"",
        )
        assert cancelled == {"ok": True}
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        assert types[-1] == "turn.cancelled"
        follower_types = [item["type"] for item in _stream_lines(follower)]
        assert follower_types[-1] == "turn.cancelled"
        assert "turn.completed" not in types
        assert "turn.failed" not in types
        detail = _json(httpd, f"/api/sessions/{session_id}")
        assert detail["running"] is False
        stored = SessionManager(tmp_path / "sessions").load(session_id)
        assert [(item.role, item.content) for item in stored.messages] == [
            ("user", "hold")
        ]
        code, body = _http_error(
            httpd, f"/api/sessions/{session_id}/turn/cancel", b""
        )
        assert code == 409
        assert body == {"error": "session idle"}
    finally:
        _stop(httpd, thread)


def test_retry_last_user_turn(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="first"),
            ChatReply(content='"Notebook."'),
            ChatReply(content="second"),
        ]
    )
    httpd, thread = _start(tmp_path, _chat(tmp_path, llm))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        session_id = created["id"]
        path = f"/api/sessions/{session_id}/turn/stream"
        first = _stream_lines(_stream(httpd, path, data=b'{"text":"hello"}'))
        assert first[-1]["type"] == "turn.completed"
        retried = _stream_lines(_stream(httpd, path, data=b'{"retry":true}'))
        assert retried[0] == {"type": "turn.accepted"}
        assert retried[-1]["type"] == "turn.completed"
        stored = SessionManager(tmp_path / "sessions").load(session_id)
        users = [item for item in stored.messages if item.role == "user"]
        assistants = [
            item
            for item in stored.messages
            if item.role == "assistant" and (item.meta or {}).get("kind") != "naming"
        ]
        assert [item.content for item in users] == ["hello"]
        assert assistants[-1].content == "second"
        think_users = [item.content for item in llm.requests[-1] if item.role == "user"]
        assert think_users == ["hello"]

        empty = _json(httpd, "/api/sessions", method="POST", data=b"")
        code, body = _http_error(
            httpd, f"/api/sessions/{empty['id']}/turn/stream", b'{"retry":true}'
        )
        assert code == 400
        assert body == {"error": "no user"}
    finally:
        _stop(httpd, thread)

    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        busy = _json(httpd, "/api/sessions", method="POST", data=b"")
        response = _stream(
            httpd,
            f"/api/sessions/{busy['id']}/turn/stream",
            data=b'{"text":"first"}',
        )
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted"}
        assert started.wait(2)
        try:
            code, body = _http_error(
                httpd,
                f"/api/sessions/{busy['id']}/turn/stream",
                b'{"retry":true}',
            )
            assert code == 409
            assert body == {"error": "session busy"}
        finally:
            response.close()
            release.set()
    finally:
        _stop(httpd, thread)


def test_cancel_then_retry_keeps_one_user(tmp_path: Path) -> None:
    started = threading.Event()
    gate = asyncio.Event()

    class BlockThenReply:
        def __init__(self) -> None:
            self.requests: list = []
            self._blocked = True

        async def chat(self, messages, tools=None):
            self.requests.append(list(messages))
            if self._blocked:
                started.set()
                await gate.wait()
                return ChatReply(content="should-not")
            return ChatReply(content="retried")

    llm = BlockThenReply()
    httpd, thread = _start(tmp_path, _chat(tmp_path, llm))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        session_id = created["id"]
        path = f"/api/sessions/{session_id}/turn/stream"
        response = _stream(httpd, path, data=b'{"text":"hold"}')
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted"}
        assert started.wait(2)
        assert _json(httpd, f"{path.replace('/stream', '/cancel')}", method="POST", data=b"") == {
            "ok": True
        }
        assert _stream_lines(response)[-1]["type"] == "turn.cancelled"
        llm._blocked = False
        retried = _stream_lines(_stream(httpd, path, data=b'{"retry":true}'))
        assert retried[-1]["type"] == "turn.completed"
        stored = SessionManager(tmp_path / "sessions").load(session_id)
        users = [item for item in stored.messages if item.role == "user"]
        assistants = [
            item
            for item in stored.messages
            if item.role == "assistant" and (item.meta or {}).get("kind") != "naming"
        ]
        assert [item.content for item in users] == ["hold"]
        assert assistants[-1].content == "retried"
        user_turns = [
            [item.content for item in req if item.role == "user"]
            for req in llm.requests
        ]
        assert user_turns.count(["hold"]) == 2
    finally:
        _stop(httpd, thread)


def _provider_stub(calls: list, reply_model: str):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw.decode("utf-8"))
            calls.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "model": body.get("model"),
                    }
                )
            payload = json.dumps(
                {
                    "model": reply_model,
                    "choices": [
                        {
                            "message": {"content": "stub-ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 2,
                        "total_tokens": 7,
                    },
                }
            ).encode()
            self.send_response(200)
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


def test_turn_model_override_routes_to_its_provider(tmp_path: Path) -> None:
    calls_a: list = []
    calls_b: list = []
    stub_a, thread_a = _provider_stub(calls_a, "model-a")
    stub_b, thread_b = _provider_stub(calls_b, "model-b")
    try:
        base_a = f"http://127.0.0.1:{stub_a.server_address[1]}"
        base_b = f"http://127.0.0.1:{stub_b.server_address[1]}"
        cfg = default_config()
        cfg = replace(
            cfg,
            providers={
                "default": replace(
                    cfg.providers["default"], baseUrl=base_a, apiKey="sk-a"
                ),
                "second": replace(
                    cfg.providers["default"], baseUrl=base_b, apiKey="sk-b"
                ),
            },
            defaultModel="model-a",
            models={"model-a": ModelCfg(), "model-b": ModelCfg(provider="second")},
        )
        save(cfg, tmp_path / "config.json")
        app = ChatApp(tmp_path, load(tmp_path / "config.json"))
        httpd, thread = _start(tmp_path, app)
        try:
            created = _json(httpd, "/api/sessions", method="POST", data=b"")
            path = f"/api/sessions/{created['id']}/turn/stream"
            lines_b = _stream_lines(
                _stream(httpd, path, data=b'{"text":"hi b","model":"model-b"}')
            )
            assert lines_b[-1]["type"] == "turn.completed"
            lines_a = _stream_lines(
                _stream(httpd, path, data=b'{"text":"hi a","model":"model-a"}')
            )
            assert lines_a[-1]["type"] == "turn.completed"
        finally:
            _stop(httpd, thread)
            app.shutdown()
        assert calls_b and all(
            call["auth"] == "Bearer sk-b" and call["model"] == "model-b"
            for call in calls_b
        )
        assert calls_a and all(
            call["auth"] == "Bearer sk-a" and call["model"] == "model-a"
            for call in calls_a
        )
    finally:
        stub_a.shutdown()
        thread_a.join(timeout=2)
        stub_a.server_close()
        stub_b.shutdown()
        thread_b.join(timeout=2)
        stub_b.server_close()


def test_failed_turn_logs_one_stderr_line(tmp_path: Path, capsys) -> None:
    class BoomLLM:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 404: model gone")

        async def aclose(self):
            return

    app = _chat(tmp_path, BoomLLM())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        path = f"/api/sessions/{created['id']}/turn/stream"
        lines = _stream_lines(_stream(httpd, path, data=b'{"text":"hi"}'))
        assert lines[-1]["type"] == "turn.failed"
        assert lines[-1]["code"] == "llm_error"
        assert "model gone" in lines[-1]["message"]
    finally:
        _stop(httpd, thread)
        app.shutdown()
    err = capsys.readouterr().err
    assert (
        f"turn failed session={created['id']} model=gpt-4o-mini "
        "provider=default code=llm_error msg=provider HTTP 404: model gone" in err
    )


def test_stream_invalid_model_logs_preaccept_failure(tmp_path: Path, capsys) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="unused")))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        path = f"/api/sessions/{created['id']}/turn/stream"
        request = Request(
            _url(httpd, path),
            data=b'{"text":"hi","model":"ghost-model"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(request, timeout=5)
            raise AssertionError("expected HTTP 400")
        except HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read().decode()) == {"error": "invalid model"}
    finally:
        _stop(httpd, thread)
        app.shutdown()
    err = capsys.readouterr().err
    assert (
        f"turn failed session={created['id']} model=ghost-model "
        "provider=default code=invalid_model msg=invalid model" in err
    )


def test_failed_turn_marks_transcript_and_trace(tmp_path: Path) -> None:
    class BoomLLM:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 404: model gone")

        async def aclose(self):
            return

    app = _chat(tmp_path, BoomLLM())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        path = f"/api/sessions/{created['id']}/turn/stream"
        lines = _stream_lines(_stream(httpd, path, data=b'{"text":"hi"}'))
        assert lines[-1]["type"] == "turn.failed"
        stored = SessionManager(tmp_path / "sessions").load(created["id"])
        users = [item for item in stored.messages if item.role == "user"]
        assert users[-1].meta == {
            "error": {"code": "llm_error", "message": "provider HTTP 404: model gone"}
        }
        traces = _json(httpd, "/api/traces")
        mine = [row for row in traces["traces"] if row["session_id"] == created["id"]]
        assert len(mine) == 1
        assert mine[0]["status"] == "failed"
        assert mine[0]["error"] == {
            "code": "llm_error",
            "message": "provider HTTP 404: model gone",
        }
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_retry_after_failure_strips_marker_on_success(tmp_path: Path) -> None:
    class FlakyLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                raise LLMError("boom-1")
            return ChatReply(content="recovered")

        async def aclose(self):
            return

    app = _chat(tmp_path, FlakyLLM())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        path = f"/api/sessions/{created['id']}/turn/stream"
        failed = _stream_lines(_stream(httpd, path, data=b'{"text":"hi"}'))
        assert failed[-1]["type"] == "turn.failed"
        retried = _stream_lines(_stream(httpd, path, data=b'{"retry":true}'))
        assert retried[-1]["type"] == "turn.completed"
        stored = SessionManager(tmp_path / "sessions").load(created["id"])
        users = [item for item in stored.messages if item.role == "user"]
        assert (users[-1].meta or {}).get("error") is None
        assistants = [
            item
            for item in stored.messages
            if item.role == "assistant" and (item.meta or {}).get("kind") != "naming"
        ]
        assert assistants[-1].content == "recovered"
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_cancelled_turn_leaves_no_error_marker(tmp_path: Path) -> None:
    started = threading.Event()
    gate = asyncio.Event()

    class Blocked:
        async def chat(self, messages, tools=None):
            started.set()
            await gate.wait()
            return ChatReply(content="should-not")

    app = _chat(tmp_path, Blocked())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        session_id = created["id"]
        response = _stream(
            httpd, f"/api/sessions/{session_id}/turn/stream", data=b'{"text":"hold"}'
        )
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted"}
        assert started.wait(2)
        cancelled = _json(
            httpd, f"/api/sessions/{session_id}/turn/cancel", method="POST", data=b""
        )
        assert cancelled == {"ok": True}
        assert _stream_lines(response)[-1]["type"] == "turn.cancelled"
        stored = SessionManager(tmp_path / "sessions").load(session_id)
        assert [(item.role, item.content) for item in stored.messages] == [("user", "hold")]
        assert (stored.messages[0].meta or {}).get("error") is None
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_unexpected_turn_error_marks_chat_unavailable(tmp_path: Path) -> None:
    class BoomGeneric:
        async def chat(self, messages, tools=None):
            raise RuntimeError("boom-secret-path-/tmp/x")

        async def aclose(self):
            return

    app = _chat(tmp_path, BoomGeneric())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        path = f"/api/sessions/{created['id']}/turn/stream"
        lines = _stream_lines(_stream(httpd, path, data=b'{"text":"hi"}'))
        assert lines[-1] == {
            "type": "turn.failed",
            "code": "chat_unavailable",
            "message": "chat unavailable",
        }
        stored = SessionManager(tmp_path / "sessions").load(created["id"])
        users = [item for item in stored.messages if item.role == "user"]
        assert users[-1].meta == {
            "error": {"code": "chat_unavailable", "message": "chat unavailable"}
        }
    finally:
        _stop(httpd, thread)
        app.shutdown()
