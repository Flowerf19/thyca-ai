"""Loopback chat HTTP — webui-chat-backend."""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from thyca.agent.events import TurnEvent
from thyca.serve.bridge import SENTINEL
from thyca.app.chat_app import ChatApp, session_title
from thyca.config import ModelCfg, default_config, load, save
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.core.protocol import Message, ToolCall
from thyca.serve import ServeError, default_webui, make_server
from thyca.sessions import Session, SessionBusy, SessionManager
from thyca.sessions.title import fallback_title
from thyca.tools.mcp import StartupDiagnostic
from thyca.memory.facade import MemoryFacade

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
    monkeypatch.setattr("thyca.app.chat_app.MCPManager", FakeManager)
    monkeypatch.setattr("thyca.app.chat_app.sys.stderr", stderr)
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


def _json_result(httpd, path: str) -> tuple[int, dict]:
    try:
        return 200, _json(httpd, path)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_bot_operations_endpoint_scope_options_and_read_only(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        group = app.create(bots=["thyca", "coder"])
        timeline = tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl"
        assert not timeline.exists()
        httpd, thread = _start(tmp_path, app)
        try:
            options = urlencode({
                "model": app._current_cfg().defaultModel,
                "effort": app._current_cfg().provider.reasoningEffort,
            })
            status, payload = _json_result(
                httpd,
                f"/api/bots/coder/rooms/{group['id']}/operations?{options}",
            )
            assert status == 200
            assert set(payload) == {
                "bot_id", "session_id", "model", "context", "last_turn",
                "conversation", "rates", "trace",
            }
            assert payload["bot_id"] == "coder" and payload["session_id"] == group["id"]
            assert payload["model"]["scope"] == "next_turn_configuration"
            assert payload["model"]["id"] == app._current_cfg().defaultModel
            assert payload["model"]["reasoning_effort"] == app._current_cfg().provider.reasoningEffort
            assert payload["context"]["estimated"] is True
            assert payload["last_turn"] is None
            assert payload["conversation"]["requests"] == 0
            assert payload["conversation"]["cost_usd"] is None
            assert all(value is None for value in payload["conversation"]["usage"].values())
            forbidden = {"apiKey", "baseURL", "messages", "prompts", "reasoning_details", "arguments"}
            def keys(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        yield key
                        yield from keys(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from keys(child)
            assert forbidden.isdisjoint(keys(payload))
            assert not timeline.exists()
            assert app._runtime.workspaces.store.get_binding("coder", group["id"]) is None

            for query in (
                "?unknown=value", "?model=one&model=two", "?effort=",
                "?model=bogus", "?effort=unknown",
                f"?model={'x' * 201}",
            ):
                status, body = _json_result(
                    httpd,
                    f"/api/bots/coder/rooms/{group['id']}/operations{query}",
                )
                assert status == 400, body
            for bot_id, session_id in (
                ("ghost", group["id"]), ("coder", "2026-10-20T09-00-00_dead"),
                ("coder", dm["id"]),
            ):
                status, body = _json_result(
                    httpd,
                    f"/api/bots/{bot_id}/rooms/{session_id}/operations",
                )
                assert status == 404
                if session_id == "2026-10-20T09-00-00_dead":
                    assert body == {"error": "session not found"}
            assert not timeline.exists()
            assert app._runtime.workspaces.store.get_binding("coder", group["id"]) is None
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_operations_measurement_failure_is_sanitized_503(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        room = app.create(bots=["thyca", "coder"])

        def broken_peek(*args, **kwargs):
            raise OSError(f"provider failure at {tmp_path}/private.conf")

        app._memory.peek = broken_peek
        httpd, thread = _start(tmp_path, app)
        try:
            status, body = _json_result(
                httpd, f"/api/bots/coder/rooms/{room['id']}/operations",
            )
            assert status == 503
            assert body == {"error": "bot information unavailable"}
            assert str(tmp_path) not in json.dumps(body)
            assert not (tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl").exists()
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_operations_missing_timeline_is_503_without_repair(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        group = app.create(bots=["thyca", "coder"])
        app._runtime.internal_session("coder", group["id"])
        timeline = tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl"
        assert timeline.is_file()
        timeline.unlink()
        httpd, thread = _start(tmp_path, app)
        try:
            status, body = _json_result(
                httpd, f"/api/bots/coder/rooms/{group['id']}/operations",
            )
            assert status == 503
            assert body == {"error": "bot information unavailable"}
            assert str(tmp_path) not in json.dumps(body)
            assert not timeline.exists()
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


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
        # One completed turn is below the naming threshold: no second call.
        assert len(llm.requests) == 1
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
    from thyca.bots.card import BotCard

    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
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
    # Phase 2B: the no-bots create is the same DM, so the concurrent
    # create opens a group instead — still blank and non-blocking.
    group = app.create(bots=["thyca", "coder"])
    assert group["id"] != first
    created.update(group)
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


def test_notebook_title_names_on_second_turn_and_persists(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="again"),
            ChatReply(content='"Cà phê với Hòa."'),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    first = app.turn(created["id"], "alo")
    assert first["title"] == fallback_title(created["id"])
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
            if tools is None:
                self.requests.append(list(messages))
                self.tools.append(tools)
                raise LLMError("title failed")
            return await super().chat(messages, tools)

    llm = TitleBoom(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    created = app.create()
    app.turn(created["id"], "alo")
    turned = app.turn(created["id"], "thêm")
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
        assert first == {"type": "turn.accepted", "bot_id": "thyca"}
        # Headers + first line went out while the Slow LLM is still blocked.
        assert release.is_set() is False
        release.set()
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        assert types == [
            "llm.started",
            "llm.finished",
            "turn.completed",
        ]
        assert lines[0]["round"] == 1
        assert lines[1] == {"type": "llm.finished", "round": 1, "tool_count": 0, "bot_id": "thyca"}
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
        assert first == {"type": "turn.accepted", "bot_id": "thyca"}
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
            assert json.loads(body) == {"error": "invalid text: empty"}
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
            assert json.loads(body) == {"error": "invalid text: invalid"}
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
            "turn.completed",
        ]
        assert lines[2] == {"type": "llm.finished", "round": 1, "tool_count": 1, "bot_id": "thyca"}
        assert lines[3] == {
            "type": "tool.started",
            "round": 1,
            "call_id": "call-1",
            "name": "bash",
            "bot_id": "thyca",
        }
        assert lines[4] == {
            "type": "tool.finished",
            "round": 1,
            "call_id": "call-1",
            "name": "bash",
            "ok": False,
            "bot_id": "thyca",
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

        monkeypatch.setattr("thyca.serve.bridge.bridge_worker", sentinel_only_worker)
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
    """The rebuilt chat UI ships its modules: page js + the shared sidebar,
    data and shell layers it is wired against."""
    app = (WEBUI / "ui" / "chat" / "js" / "app.js").read_text(encoding="utf-8")
    chat = (WEBUI / "ui" / "chat" / "js" / "chat.js").read_text(encoding="utf-8")
    data = (WEBUI / "ui" / "chat" / "js" / "data.js").read_text(encoding="utf-8")
    shared = (WEBUI / "ui" / "share" / "data.js").read_text(encoding="utf-8")
    sidebar = (WEBUI / "ui" / "share" / "sidebar.js").read_text(encoding="utf-8")
    styles = (WEBUI / "ui" / "chat" / "chatcss" / "chat.css").read_text(encoding="utf-8")
    sidebar_css = (WEBUI / "ui" / "share" / "sidebar.css").read_text(encoding="utf-8")
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")

    # Turns stream over NDJSON; send/stop/retry go through sendTurn/stopRun.
    assert "/turn/stream" in chat
    assert "readNdjson" in chat
    assert "function sendTurn" in chat
    assert "/turn/cancel" in chat
    assert "retry: true" in app
    assert "getJson('/api/config')" in data
    assert "'/api/sessions'" in shared
    assert "method:'POST'" in sidebar                    # group dialog create
    assert "formatMarkdown" in chat
    # Stream events drive the live card + the bot-card pocket feed; the old
    # live-status/thinking card on the message list did not survive the
    # rebuild (activity moved onto the bot card — see feedPush/feedTools).
    assert "handleStreamEvent" in chat
    assert "tool.started" in chat
    assert "llm.thinking" in chat
    assert "turn.completed" in chat
    assert "feedPush" in chat
    assert "feedTools" in chat
    assert "ambientLineForEvent" not in chat
    assert not (WEBUI / "ui" / "chat" / "js" / "chat-ambient.js").exists()
    # The elapsed tick still reads in Vietnamese on the bot card.
    assert " giây" in chat
    assert "setInterval(" in chat
    assert ".progress-list" in sidebar_css
    assert ".live-status" in styles
    assert ".streaming-caret" in styles
    # Tools collapse into a per-message pill, not a "Tools used:" block.
    assert "usage-pill" in chat
    assert "Tools used:" not in chat
    # The page actually loads the modules this test reads.
    for tag in (
        'src="js/data.js"', 'src="js/chat.js"', 'src="js/app.js"',
        'src="../share/data.js"', 'src="../share/sidebar.js"',
        'src="../share/shell-app.js"',
    ):
        assert tag in html


def test_chat_nav_opens_new_session() -> None:
    """The + button only opens the group picker — a session is POSTed when the
    dialog submits, and DM sessions are find-or-create through the bot."""
    sidebar = (WEBUI / "ui" / "share" / "sidebar.js").read_text(encoding="utf-8")
    chat = (WEBUI / "ui" / "chat" / "js" / "chat.js").read_text(encoding="utf-8")
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")

    assert 'id="team-shared-create"' in html
    assert 'id="group-dialog"' in html
    assert 'id="message-list"' in html
    assert 'id="composer"' in html

    # Clicking + fills and shows the dialog — it must not hit the network.
    # The button only exists in chat's static markup, so the shared sidebar
    # guards before binding it.
    assert "const teamSharedCreate=$('#team-shared-create');" in sidebar
    open_body = sidebar[sidebar.index("teamSharedCreate.onclick"):]
    open_body = open_body[: open_body.index("\n")]
    assert "$('#group-dialog').showModal()" in open_body
    assert "getJson" not in open_body
    # Submitting the form is what creates the session, then opens it.
    form_body = sidebar[sidebar.index("$('#group-form').onsubmit"):]
    assert "'/api/sessions'" in form_body
    assert "method:'POST'" in form_body
    assert "loadSession(detail.id" in form_body
    # Picking a bot goes through the find-or-create DM, not a blank create.
    assert "/api/bots/${encodeURIComponent(id)}/dm" in chat

    provider = (WEBUI / "ui" / "provider" / "js" / "provider.js").read_text(encoding="utf-8")
    assert "getJson('/api/config')" in provider
    assert "postJson('/api/config'" in provider
    assert "postJson('/api/onboarding/verify'" in provider
    assert "contextTokens" in provider
    assert not (WEBUI / "staff").exists()


def test_create_prunes_previous_blank(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    from thyca.bots.card import BotCard

    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    first = app.create()
    assert app.list_payload()["sessions"] == []
    # Phase 2B: repeated creates return the same DM singleton; a fresh
    # group still prunes the previous blank group.
    assert app.create()["id"] == first["id"]
    g1 = app.create(bots=["thyca", "coder"])
    g2 = app.create(bots=["thyca", "coder"])
    assert g1["id"] != g2["id"]
    assert not (tmp_path / "sessions" / f"{g1['id']}.jsonl").exists()
    assert (tmp_path / "sessions" / f"{g2['id']}.jsonl").exists()
    assert (tmp_path / "sessions" / f"{first['id']}.jsonl").exists()
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


def test_session_payload_includes_context(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    try:
        created = app.create()
        for payload in (created, app.turn(created["id"], "hi"), app.get_payload(created["id"])):
            ctx = payload["context"]
            assert ctx["estimated_tokens"] == ctx["transcript_tokens"] + ctx["overhead_tokens"]
            assert ctx["context_tokens"] > 0
            assert abs(ctx["ratio"] - ctx["estimated_tokens"] / ctx["context_tokens"]) < 1e-9
            # Hot prompt + tools schema are real turn overhead, not zero.
            assert ctx["overhead_tokens"] > 0
    finally:
        app.shutdown()


def _chat_small_context(tmp_path: Path) -> ChatApp:
    cfg = default_config()
    cfg = replace(cfg, limits=replace(cfg.limits, contextTokens=1000))
    save(cfg, tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=FakeLLM(ChatReply(content="x")))


def _seed_long_session(tmp_path: Path, session_id: str) -> None:
    manager = SessionManager(tmp_path / "sessions")
    manager.load(session_id)
    for i in range(4):
        manager.append(Message(role="user", content="u" * 300 + str(i)))
        manager.append(Message(role="assistant", content="a" * 300))


def test_compact_session_skips_absent_canonical_context(tmp_path: Path) -> None:
    app = _chat_small_context(tmp_path)
    try:
        created = app.create()
        _seed_long_session(tmp_path, created["id"])
        before = app.get_payload(created["id"])
        result = app.compact_session(created["id"], instructions="keep the goal")
        assert result["compaction"]["compacted"] is False
        assert result["compaction"]["per_bot"]["thyca"] == {
            "compacted": False,
            "skipped": "no_existing_context",
        }
        assert result["messages"] == before["messages"]
        assert app._runtime.workspaces.store.get_binding(
            "thyca", created["id"]
        ) is None
    finally:
        app.shutdown()


def test_compact_session_refuses_mid_turn(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    try:
        created = app.create()
        app._turns.claim(created["id"])
        try:
            app.compact_session(created["id"])
        except SessionBusy:
            pass
        else:
            raise AssertionError("expected SessionBusy")
        finally:
            app._turns.release(created["id"])
    finally:
        app.shutdown()


def _seed_canonical_long_session(app: ChatApp, session_id: str) -> int:
    room = app._sessions.store.load(session_id)
    window = app._webui.window_for(room)
    binding = app._runtime.workspaces.store.bind_window("thyca", window)
    store = app._runtime._canonical_store(
        "thyca", window, binding.internal_session_id
    )
    manager = SessionManager(store=store)
    manager.load(binding.internal_session_id)
    for i in range(4):
        manager.append(Message(role="user", content="canonical user " + "u" * 300 + str(i)))
        manager.append(Message(role="assistant", content="canonical reply " + "a" * 300))
    return len(manager.current.messages)


def test_compact_over_http(tmp_path: Path) -> None:
    app = _chat_small_context(tmp_path)
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        _seed_long_session(tmp_path, created["id"])
        public_before = app.get_payload(created["id"])["messages"]
        canonical_before = _seed_canonical_long_session(app, created["id"])
        compacted = _json(
            httpd, f"/api/sessions/{created['id']}/compact", method="POST", data=b"{}"
        )
        assert compacted["compaction"]["per_bot"]["thyca"]["compacted"] is True
        assert compacted["messages"] == public_before
        binding = app._runtime.workspaces.store.get_binding("thyca", created["id"])
        canonical = app._runtime._canonical_store(
            "thyca", app._webui.window_for(app._sessions.store.load(created["id"])),
            binding.internal_session_id,
        ).load(binding.internal_session_id)
        assert canonical_before > len(canonical.messages)
        assert "context" in compacted
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/sessions/2026-01-01T00-00-00_ffff/compact"),
                    data=b"{}", method="POST", headers={"Content-Type": "application/json"},
                ), timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")
        try:
            urlopen(
                Request(
                    _url(httpd, f"/api/sessions/{created['id']}/compact"),
                    data=b'{"model": "nope-not-registered"}', method="POST",
                    headers={"Content-Type": "application/json"},
                ), timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("expected 400")
        try:
            urlopen(
                Request(
                    _url(httpd, f"/api/sessions/{created['id']}/compact"),
                    data=json.dumps({"instructions": "x" * 501}).encode("utf-8"),
                    method="POST", headers={"Content-Type": "application/json"},
                ), timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read().decode("utf-8")) == {"error": "invalid instructions"}
        else:
            raise AssertionError("expected 400")
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_chat_mobile_pick_pattern() -> None:
    """On mobile the bot/session list is an off-canvas overlay, not a separate
    pick screen: `.mobile-open` slides it in over the content, the backdrop
    or Esc closes it, and loading a session closes it too — the same shared
    wiring every page gets from share/shell-app.js. The header's
    .menu-button is the mobile-only trigger (hidden on desktop)."""
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")
    chat = (WEBUI / "ui" / "chat" / "js" / "chat.js").read_text(encoding="utf-8")
    shell_app = (WEBUI / "ui" / "share" / "shell-app.js").read_text(encoding="utf-8")
    sidebar_css = (WEBUI / "ui" / "share" / "sidebar.css").read_text(encoding="utf-8")
    base_css = (WEBUI / "ui" / "share" / "base.css").read_text(encoding="utf-8")

    assert 'class="sidebar" id="chat-sidebar"' in html
    assert 'id="mobile-overlay"' in html
    assert 'class="icon-button menu-button"' in html
    assert 'aria-controls="chat-sidebar"' in html
    # Hidden on desktop, revealed under the 850px breakpoint.
    assert ".menu-button{display:none}" in base_css
    assert ".menu-button{display:inline-flex}" in base_css
    assert "screen-shell" not in html
    # The pick-shell two-pane pattern is gone: under 850px the sidebar sits
    # translated off-canvas until .mobile-open slides it over the content.
    assert "max-width:850px" in sidebar_css
    assert ".sidebar.mobile-open" in sidebar_css
    assert "function openMobile" in shell_app
    assert "function closeMobile" in shell_app
    assert "mobile-open" in shell_app
    assert "$('#mobile-overlay').onclick = closeMobile" in shell_app
    # The menu button toggles the overlay on mobile; the wiring uses optional
    # chaining so it stays safe if a page ever drops the element.
    assert "$('.menu-button')" in shell_app
    assert "$('.menu-button')?.setAttribute" in shell_app
    assert "matchMedia('(max-width:850px)')" in shell_app
    # Opening a session (group row tap, deep link, drawer row) reveals the
    # conversation by closing the overlay.
    load_session = chat[chat.index("async function loadSession") : chat.index("async function selectBot")]
    assert "closeMobile()" in load_session
    assert "pick-shell" not in html + chat + shell_app
    assert "is-picking" not in html + chat + shell_app


def test_context_usage_moved_to_bot_card() -> None:
    """TURN-CURATE: the composer meter/nudge is gone — the agent manages its
    own context; the ~% meter lives in the bot drawer's "Khả năng" view."""
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")
    assert 'id="context-nudge"' not in html
    assert 'id="context-meter"' not in html
    assert 'id="context-focus"' not in html
    assert "Gọn phiên" not in html
    sidebar = (WEBUI / "ui" / "share" / "sidebar.js").read_text(encoding="utf-8")
    css = (WEBUI / "ui" / "share" / "sidebar.css").read_text(encoding="utf-8")
    assert "NGỮ CẢNH" in sidebar
    assert "ctx-meter" in sidebar
    assert "data-drawer-context" in sidebar
    assert "fillDrawerContext" in sidebar
    assert "/operations" in sidebar
    assert ".ctx-meter" in css
    assert ".ctx-bar" in css

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
    assert retries == [TurnEvent(type="llm.retry", attempt=1, max_attempts=3, bot_id="thyca")]
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
    skill_path = skill_md / "SKILL.md"
    skill_path.write_text("---\nname: create-skill\n---\nskill body\n", encoding="utf-8")
    notes_path = tmp_path / "notes.md"

    class ThinkingToolLLM:
        def __init__(self) -> None:
            self.round = 0

        async def chat(self, messages, tools=None, on_reasoning=None):
            self.round += 1
            thought = "I will read the skill" if self.round == 1 else "The skill is loaded"
            if on_reasoning:
                on_reasoning(thought)
            if self.round == 1:
                return ChatReply(
                    content=None,
                    reasoning=thought,
                    tool_calls=[
                        ToolCall(
                            id="call-1", name="read",
                            arguments={"path": str(skill_path)},
                        ),
                        ToolCall(
                            id="call-2", name="read",
                            arguments={"path": str(notes_path)},
                        ),
                    ],
                )
            return ChatReply(content="xong", reasoning=thought)

    httpd, thread = _start(tmp_path, _chat(tmp_path, ThinkingToolLLM()))
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        body = json.dumps({"text": "viết skill"}).encode("utf-8")
        stream = _stream(
            httpd, f"/api/sessions/{created['id']}/turn/stream", data=body
        )
        events = _stream_lines(stream)
        assert any(event["type"] == "llm.thinking" for event in events)

        detail = _json(httpd, f"/api/sessions/{created['id']}")
        assert all("tool_calls" not in item for item in detail["messages"])
        assert all("reasoning" not in item for item in detail["messages"])
        assert [item["content"] for item in detail["messages"] if item["role"] == "assistant"] == ["xong"]

        trace = _json(httpd, f"/api/traces/{created['id']}/0")
        calls = next(item["tool_calls"] for item in trace["messages"] if item.get("tool_calls"))
        assert calls[0] == {
            "id": "call-1", "name": "read",
            "arguments": {"path": str(skill_path)}, "skill": "create-skill",
        }
        assert calls[1] == {
            "id": "call-2", "name": "read",
            "arguments": {"path": str(notes_path)},
        }
        assistant_call = next(
            item for item in trace["messages"]
            if item["role"] == "assistant" and item.get("tool_calls")
        )
        assert assistant_call["reasoning"] == "I will read the skill"
        tool_rows = [item for item in trace["messages"] if item["role"] == "tool"]
        assert {item["tool_call_id"] for item in tool_rows} == {"call-1", "call-2"}
        assert all(item["content"] for item in tool_rows)
        assert all(
            item["meta"]["span_id"] == assistant_call["meta"]["span_id"]
            for item in tool_rows
        )
        final = next(
            item for item in trace["messages"]
            if item["role"] == "assistant" and item.get("content") == "xong"
        )
        assert final["reasoning"] == "The skill is loaded"
    finally:
        _stop(httpd, thread)


def test_empty_submit_nudges_without_sending() -> None:
    """An empty submit returns before sendTurn — nothing hits the network.
    (The old shake/"Chưa có nội dung để gửi." hint was dropped in the rebuild;
    the composer-hint row is now static text under the composer.)"""
    app = (WEBUI / "ui" / "chat" / "js" / "app.js").read_text(encoding="utf-8")
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")
    css = (WEBUI / "ui" / "chat" / "chatcss" / "chat.css").read_text(encoding="utf-8")

    submit = app[app.index("$('#composer').onsubmit"):]
    guard = submit[: submit.index("sendTurn(")]

    # The hint row under the composer is the accessible status text.
    assert 'class="composer-hint"' in html
    assert 'role="status"' in html
    # Empty text — or a turn already running — returns before sendTurn.
    assert "e.preventDefault()" in guard
    assert "if (!text || runs.has(currentSession)) return;" in guard
    assert "fetch(" not in guard
    assert ".composer-hint" in css


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
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
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
        assert json.loads(starter.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
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
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
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
            {"type": "llm.thinking", "round": 1, "delta": "First I check", "bot_id": "thyca"},
            {"type": "llm.thinking", "round": 1, "delta": " the chords", "bot_id": "thyca"},
        ]
        assert "llm.finished" in types
        assert types[-1] == "turn.completed"
        detail = _json(httpd, f"/api/sessions/{created['id']}")
        assistant = next(
            item for item in detail["messages"]
            if item["role"] == "assistant" and item.get("content") == "yes"
        )
        assert assistant["content"] == "yes"
        assert "reasoning" not in assistant
        trace = _json(httpd, f"/api/traces/{created['id']}/0")
        execution = next(
            item for item in trace["messages"]
            if item["role"] == "assistant" and item.get("content") == "yes"
        )
        assert execution["reasoning"] == "First I check the chords"
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
            {"type": "llm.content", "round": 1, "delta": "Xin ", "bot_id": "thyca"},
            {"type": "llm.content", "round": 1, "delta": "chào", "bot_id": "thyca"},
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
        assert json.loads(starter.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        assert started.wait(2)
        follower = urlopen(
            Request(
                _url(httpd, f"/api/sessions/{session_id}/turn/stream"),
                method="GET",
            ),
            timeout=10,
        )
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        starter.close()
        release.set()
        lines = _stream_lines(follower)
        assert lines[-1]["type"] == "turn.completed"
        assert lines[-1]["detail"]["reply"] == "kept"
    finally:
        _stop(httpd, thread)


def test_webui_keeps_streaming_card_across_session_switch() -> None:
    """Leaving a streaming session and coming back must not lose the turn.

    loadSession replaces #message-list, which detaches the live card — the run
    token in `runs` keeps the stream alive meanwhile (a detached node just
    stops scrolling the visible list). Coming back re-renders the transcript
    and re-attaches the in-flight stream onto a fresh running-response card
    via followRunningTurn, so the turn keeps drawing instead of freezing.
    """
    chat = (WEBUI / "ui" / "chat" / "js" / "chat.js").read_text(encoding="utf-8")
    data = (WEBUI / "ui" / "chat" / "js" / "data.js").read_text(encoding="utf-8")

    send_turn = chat[chat.index("async function sendTurn"):]
    load_session = chat[
        chat.index("async function loadSession") : chat.index("async function selectBot")
    ]
    follow = chat[chat.index("function followRunningTurn"):]
    content_branch = chat[
        chat.index("case 'llm.content'") : chat.index("case 'session.naming.finished'")
    ]

    # One run token per in-flight session, keyed so a session switch finds it.
    assert "const runs = new Map();" in data
    assert "runs.set(token.sessionId, token);" in send_turn
    assert "runs.delete(token.sessionId);" in chat
    # A running session's half-drawn list is not snapshotted into histories.
    assert "!runs.has(currentSession)" in chat
    # Coming back: the transcript re-renders and the live stream re-attaches
    # to a fresh card — never a bare status line with no further updates.
    assert "if (detail.running) followRunningTurn(id);" in load_session
    assert "$('#message-list').append(node);" in follow
    assert "runs.set(sessionId, token);" in follow
    assert "/turn/stream" in follow
    # A detached card must not drag the visible conversation around; the
    # settled re-render only repaints the list when the session is on screen.
    assert "token.sessionId === currentSession" in content_branch
    assert "currentSession === token.sessionId" in chat


def test_webui_follows_a_turn_it_did_not_start() -> None:
    """Reload mid-turn: attach GET /turn/stream so the card receives the same
    events as the starter. There is no polling fallback — the stream replay
    IS the follow mechanism."""
    chat = (WEBUI / "ui" / "chat" / "js" / "chat.js").read_text(encoding="utf-8")
    data = (WEBUI / "ui" / "chat" / "js" / "data.js").read_text(encoding="utf-8")
    shared = (WEBUI / "ui" / "share" / "data.js").read_text(encoding="utf-8")
    app = (WEBUI / "ui" / "chat" / "js" / "app.js").read_text(encoding="utf-8")

    load_session = chat[
        chat.index("async function loadSession") : chat.index("async function selectBot")
    ]
    select_bot = chat[
        chat.index("async function selectBot") : chat.index("function isNearBottom")
    ]
    follow = chat[chat.index("function followRunningTurn"):]
    busy_fn = chat[chat.index("function busy(") : chat.index("/* POST")]

    # Both entry points (sidebar load, bot DM open) follow a running turn by
    # replaying the same /turn/stream the starter reads.
    assert "if (detail.running) followRunningTurn(id);" in load_session
    assert "if (detail.running) followRunningTurn(detail.id);" in select_bot
    assert "function followRunningTurn" in chat
    assert (
        "fetch(`/api/sessions/${encodeURIComponent(sessionId)}/turn/stream`)" in follow
    )
    assert "readNdjson" in chat
    assert "$('#message-list').append(node);" in follow
    # No turn status is invented for the reload case: the card reads like any
    # in-flight turn, from copy that already exists.
    for copy in (
        "Đang trả lời lượt trước", "Phiên đang trả lời", "SESSION_BUSY_STATUS"
    ):
        assert copy not in chat
    # A failed GET must not strand the composer behind a "running" state
    # nobody follows any more — the catch settles the token via finishTurn.
    assert (
        ".catch(() => { if (runs.get(sessionId) === token) finishTurn(token); })"
        in follow
    )

    # Blocking is per session: the composer refuses only while the session on
    # screen has a run token, and busy() never disables sidebar controls —
    # the sidebar and group dialog stay usable while a turn runs.
    assert "const runs = new Map();" in data
    assert "runs.has(currentSession)" in app           # submit guard
    assert "busy(runs.has(currentSession))" in chat
    assert ".disabled" not in busy_fn
    # Errors carry their HTTP status so a busy/conflict answer stays readable
    # (non-module scripts share globals — no import list to drift).
    assert "status: res.status" in chat
    assert "err.status = res.status" in shared


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


def test_chat_ui_has_no_direct_rename_delete_controls_or_dialogs() -> None:
    """No chat sidebar entity exposes rename/delete UI or stale handlers."""
    sidebar = (WEBUI / "ui" / "share" / "sidebar.js").read_text(encoding="utf-8")
    app = (WEBUI / "ui" / "chat" / "js" / "app.js").read_text(encoding="utf-8")
    chat = (WEBUI / "ui" / "chat" / "js" / "chat.js").read_text(encoding="utf-8")
    data = (WEBUI / "ui" / "chat" / "js" / "data.js").read_text(encoding="utf-8")
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")
    css = (WEBUI / "ui" / "chat" / "chatcss" / "chat.css").read_text(encoding="utf-8")
    sidebar_css = (WEBUI / "ui" / "share" / "sidebar.css").read_text(encoding="utf-8")
    api = (WEBUI / "ui" / "share" / "data.js").read_text(encoding="utf-8")
    admin = (Path(__file__).resolve().parents[1] / "thyca" / "bots" / "admin_tools.py").read_text(
        encoding="utf-8")

    visible = "\n".join((sidebar, app, chat, data, html))
    assert not re.search(r"\brefreshBotDrawer\b", visible)
    for removed in (
        "openRename", "openDelete", "submitRename", "submitDelete",
        "rename-dialog", "delete-dialog", "rename-form", "delete-form",
        "rename-id", "delete-id", "renameStatus", "deleteStatus",
        "state.saving", "deleteId", "patchJson", "deleteJson",
        "refreshTeamBots", "onBotDeleted",
        "onBotRosterChanged", "onSessionsChanged",
    ):
        assert removed not in visible
    assert "team-bot-flap" not in css
    assert ".session-action" not in sidebar_css
    # No patchJson/deleteJson helpers any more: the shared getJson takes an
    # options passthrough, which is what the group dialog's PATCH uses.
    assert "async function getJson(url, options)" in api
    assert "fetch(url, options)" in api
    assert "method:'PATCH'" in sidebar
    assert "name=\"bot_delete\"" in admin or 'name="bot_delete"' in admin
    assert "bot_edit_card" in admin
    assert 'id="bot-drawer"' in html


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


def test_session_rename_delete_http_apis_remain_wired() -> None:
    """Removing chat UI controls does not remove the session HTTP APIs."""
    root = Path(__file__).resolve().parents[1]
    routes = (root / "thyca" / "serve" / "routes.py").read_text(encoding="utf-8")
    api = (root / "thyca" / "serve" / "sessions_api.py").read_text(encoding="utf-8")
    assert "session_rename(self, app, path)" in routes
    assert "session_delete(self, app, path)" in routes
    assert "def session_rename(" in api
    assert "def session_delete(" in api


def test_chat_rows_use_the_shared_sidebar_markup() -> None:
    """Conversation rows come from the shared sidebar markup every page gets:
    `.group-row` buttons in the "Hội thoại chung" list and `.info-session`
    rows inside the bot drawer — the old .session-item grid is gone. Trace
    renders its own session cards (.tl-session) with .tl-turn row buttons."""
    sidebar = (WEBUI / "ui" / "share" / "sidebar.js").read_text(encoding="utf-8")
    sidebar_css = (WEBUI / "ui" / "share" / "sidebar.css").read_text(encoding="utf-8")
    shell = (WEBUI / "ui" / "share" / "shell.js").read_text(encoding="utf-8")
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")
    trace = (WEBUI / "ui" / "trace" / "js" / "trace.js").read_text(encoding="utf-8")
    trace_css = (WEBUI / "ui" / "trace" / "tracecss" / "trace.css").read_text(encoding="utf-8")

    # Group rows: one button per shared session — title, roster, meta line —
    # emitted by the same shared sidebar.js on every page.
    assert 'class="group-row"' in sidebar
    assert "group-title" in sidebar
    assert "group-roster" in sidebar
    assert "group-meta" in sidebar
    assert ".group-row" in sidebar_css
    # The bot drawer lists that bot's sessions as .info-session rows.
    assert 'class="info-session"' in sidebar
    assert ".info-session" in sidebar_css
    # The skeleton those rows render into: #team-bot-list ships in the shared
    # shell markup on every page, but "Hội thoại chung" only exists on chat —
    # its #group-list lives in chat's static sidebar and renderGroups no-ops
    # on pages without it.
    assert 'id="team-bot-list"' in shell
    assert 'id="group-list"' not in shell
    assert 'id="group-list"' in html
    assert "Hội thoại chung" in html
    assert "if(!gl)return" in sidebar
    # Trace's session list: one paper card per session, .tl-turn row buttons.
    assert 'class="paper-card tl-session"' in trace
    assert 'class="tl-turn"' in trace
    assert ".tl-session" in trace_css
    # The retired per-row grid must not creep back in.
    assert "session-body" not in sidebar + trace
    assert ".session-item" not in sidebar_css


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

    monkeypatch.setattr("thyca.app.chat_app.ConnectFactory.create", create)
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
        assert first == {"type": "turn.accepted", "bot_id": "thyca"}
        assert started.wait(2)
        follower = urlopen(
            Request(
                _url(httpd, f"/api/sessions/{session_id}/turn/stream"),
                method="GET",
            ),
            timeout=10,
        )
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
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
    from thyca.bots.card import BotCard

    llm = ScriptedLLM(
        [
            ChatReply(content="first"),
            ChatReply(content="second"),
        ]
    )
    app = _chat(tmp_path, llm)
    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        session_id = created["id"]
        path = f"/api/sessions/{session_id}/turn/stream"
        first = _stream_lines(_stream(httpd, path, data=b'{"text":"hello"}'))
        assert first[-1]["type"] == "turn.completed"
        retried = _stream_lines(_stream(httpd, path, data=b'{"retry":true}'))
        assert retried[0] == {"type": "turn.accepted", "bot_id": "thyca"}
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

        # A fresh group (not the DM singleton) has no user row: retry is 400.
        empty = _json(httpd, "/api/sessions", method="POST",
                      data=b'{"bots":["thyca","coder"]}')
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
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
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
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
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
        assert users[-1].meta["error"] == {
            "code": "llm_error", "message": "provider HTTP 404: model gone"
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
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
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
        assert users[-1].meta["error"] == {
            "code": "chat_unavailable", "message": "chat unavailable"
        }
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_4000_emoji_turn_accepted_end_to_end(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/sessions", method="POST", data=b"")
        text = "\U0001f600" * 4000
        assert len(text) == 4000
        body = json.dumps({"text": text}).encode("utf-8")
        assert len(body) > 16_384  # would trip the old fixed cap
        turned = _json(
            httpd, f"/api/sessions/{created['id']}/turn", method="POST", data=body
        )
        assert turned["reply"] == "pong"
        assert llm.requests[0][-1].content == text
    finally:
        _stop(httpd, thread)
        app.shutdown()
