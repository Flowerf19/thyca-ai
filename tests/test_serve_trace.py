from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from thyca.chat_app import ChatApp
from thyca.config import default_config, load, save
from thyca.llm.llm_base import ChatReply
from thyca.protocol import Message
from thyca.serve import default_webui, make_server
from thyca.sessions import SessionManager
from thyca.tools.memory import MemoryFacade

WEBUI = default_webui()
TS = "2026-08-26T09:12:03Z"
TS2 = "2026-08-26T09:12:04Z"


class FakeLLM:
    async def chat(self, messages, tools=None):
        return ChatReply(content="x")


def _chat(tmp_path: Path) -> ChatApp:
    save(default_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=FakeLLM())


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


def _url(httpd, path: str) -> str:
    return f"http://127.0.0.1:{httpd.server_address[1]}{path}"


def _json(httpd, path: str) -> dict:
    request = Request(_url(httpd, path), method="GET")
    with urlopen(request, timeout=5) as response:
        assert response.headers.get_content_type() == "application/json"
        return json.loads(response.read().decode("utf-8"))


def _append_turn(
    manager: SessionManager,
    *,
    model: str,
    content: str,
    cost: float | None,
    started: str = TS,
) -> str:
    session = manager.create()
    manager.set_title(content)
    manager.append(Message(role="user", content=content, ts=started))
    meta: dict = {
        "kind": "llm",
        "round": 1,
        "model": model,
        "latency_ms": 120,
        "usage": {
            "prompt_tokens": 10,
            "cached_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 13,
        },
        "finish_reason": "stop",
    }
    if cost is not None:
        meta["cost_usd"] = cost
    manager.append(Message(role="assistant", content=content, ts=TS2, meta=meta))
    return session.id


def test_traces_without_chat_are_404(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        try:
            urlopen(_url(httpd, "/api/traces/stats"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404 without chat")
    finally:
        _stop(httpd, thread)


def test_stats_filter_detail_and_corrupt_skip(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        mini_id = _append_turn(manager, model="gpt-4o-mini", content="mini turn", cost=0.00001)
        other_id = _append_turn(
            manager,
            model="foo/bar",
            content="other turn",
            cost=None,
            started="2026-08-25T09:12:03Z",
        )
        (tmp_path / "sessions" / "2026-01-01T00-00-00_ffff.jsonl").write_text("{bad\n", encoding="utf-8")

        httpd, thread = _start(tmp_path, chat)
        try:
            stats = _json(httpd, "/api/traces/stats")
            assert stats["totals"]["requests"] == 2
            models = {row["model"]: row for row in stats["by_model"]}
            assert models["gpt-4o-mini"]["cost_usd"] == 0.00001
            assert models["foo/bar"]["cost_usd"] is None
            listed = _json(httpd, "/api/traces")
            assert {item["session_id"] for item in listed["traces"]} == {mini_id, other_id}

            filtered = _json(httpd, "/api/traces?model=gpt-4o-mini&status=completed")
            assert filtered["total"] == 1
            assert filtered["traces"][0]["session_id"] == mini_id
            assert filtered["traces"][0]["model"] == "gpt-4o-mini"

            day = _json(httpd, "/api/traces?from=2026-08-26&to=2026-08-26")
            assert day["total"] == 1
            assert day["traces"][0]["session_id"] == mini_id

            detail = _json(httpd, f"/api/traces/{mini_id}/0")
            assert detail["session_id"] == mini_id
            assert detail["turn_index"] == 0
            assert detail["messages"][0]["role"] == "user"
            assert detail["messages"][1]["meta"]["usage"]["prompt_tokens"] == 10

            try:
                urlopen(_url(httpd, f"/api/traces/{mini_id}/9"), timeout=2)
            except HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError("expected 404 for missing turn")

            try:
                urlopen(_url(httpd, "/api/traces/2026-01-01T00-00-00_dead/0"), timeout=2)
            except HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError("expected 404 for missing session")
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()
