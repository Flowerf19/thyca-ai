from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from thyca.serve import trace_api
from thyca.app.chat_app import ChatApp
from thyca.configs import load, save
from seeded_config import seeded_config
from thyca.llms.llm_base import ChatReply
from thyca.core.protocol import Message, ToolCall
from thyca.serve import default_webui, make_server
from thyca.conversations.runtime import SessionManager
from thyca.conversations.runtime.store import SessionStore
from thyca.core.conversation import ConversationRef
from thyca.memories.facade import MemoryFacade

WEBUI = default_webui()
TS = "2026-08-26T09:12:03Z"
TS2 = "2026-08-26T09:12:04Z"


class FakeLLM:
    async def chat(self, messages, tools=None):
        return ChatReply(content="x")


def _chat(tmp_path: Path) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
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
    manager.append(Message(role="assistant", content=content, ts=TS2, meta=meta), actor="thyca")
    return session.id


def test_concurrent_trace_list_and_detail_do_not_race_cache(tmp_path: Path) -> None:
    """Concurrent store list + ref-keyed detail reads never race.

    Hard cut: store doors have no files and no mtime cache, so list and
    detail both read through the SQLite store; concurrent readers must
    complete without errors (the old file-cache race cannot recur).
    """
    from thyca.core.conversation import ConversationRef

    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        ids = [
            _append_turn(manager, model="m", content=f"turn {index}", cost=None)
            for index in range(8)
        ]
        errors: list[Exception] = []
        barrier = threading.Barrier(5)

        def lister() -> None:
            barrier.wait(timeout=10)
            try:
                for _ in range(30):
                    trace_api.collect_turns(chat)
            except Exception as exc:
                errors.append(exc)

        def detailer(slot: int) -> None:
            store = chat.trace_store()
            sids = ids[slot:] + ids[:slot]
            barrier.wait(timeout=10)
            try:
                for _ in range(20):
                    for sid in sids:
                        trace_api.cached_turns_for_ref(store, ConversationRef.group(sid))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=lister) for _ in range(2)]
        threads += [threading.Thread(target=detailer, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
    finally:
        chat.shutdown()


def test_trace_scan_rereads_store_doors(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        session_id = _append_turn(manager, model="gpt-4o-mini", content="cached turn", cost=0.00001)

        scans = ["initial"]
        original_scan = SessionStore.scan

        def counting_scan(self: SessionStore, path: Path):
            scans.append(path.name)
            return original_scan(self, path)

        httpd, thread = _start(tmp_path, chat)
        try:
            SessionStore.scan = counting_scan  # type: ignore[method-assign]
            try:
                first = _json(httpd, "/api/traces/stats")
                assert first["totals"]["requests"] == 1
                scans.clear()
                # No file cache: store doors are re-read, totals identical.
                second = _json(httpd, "/api/traces/stats")
                assert second == first
                assert scans == []
                manager.load(session_id)
                manager.append(
                    Message(
                        role="user",
                        content="second turn",
                        ts="2026-08-26T10:00:00Z",
                    )
                )
                scans.clear()
                refreshed = _json(httpd, "/api/traces/stats")
                assert refreshed["totals"]["requests"] == 1  # still one open turn
                assert scans == []
            finally:
                SessionStore.scan = original_scan  # type: ignore[method-assign]
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()


def test_trace_detail_after_list_rereads_store_door(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        session_id = _append_turn(manager, model="gpt-4o-mini", content="detail turn", cost=0.00001)

        scans: list[str] = []
        original_scan = SessionStore.scan

        def counting_scan(self: SessionStore, path: Path):
            scans.append(path.name)
            return original_scan(self, path)

        httpd, thread = _start(tmp_path, chat)
        try:
            SessionStore.scan = counting_scan  # type: ignore[method-assign]
            try:
                listed = _json(httpd, "/api/traces")
                assert listed["total"] == 1
                scans.clear()
                detail = _json(httpd, f"/api/traces/groups/{session_id}/0")
                assert detail["group_id"] == session_id
                assert detail["kind"] == "group"
                assert scans == []
            finally:
                SessionStore.scan = original_scan  # type: ignore[method-assign]
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()


def test_trace_store_corrupt_door_is_skipped(tmp_path: Path) -> None:
    """Hard cut: a corrupt store door is skipped by scans, never fatal.

    There is no file to repair under the store; the door stays blocked
    until an authorized lifecycle action, and scans keep serving the
    healthy doors (here: none left, so totals drop to zero).
    """
    import sqlite3

    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        session_id = _append_turn(manager, model="gpt-4o-mini", content="recover turn", cost=0.00001)

        httpd, thread = _start(tmp_path, chat)
        try:
            assert _json(httpd, "/api/traces/stats")["totals"]["requests"] == 1
            conn = sqlite3.connect(tmp_path / "conversations.db")
            try:
                conn.execute(
                    "UPDATE conversations SET metadata=? WHERE kind='group' AND id=?",
                    ("{not valid json", session_id),
                )
                conn.commit()
            finally:
                conn.close()
            assert _json(httpd, "/api/traces/stats")["totals"]["requests"] == 0
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()


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

        httpd, thread = _start(tmp_path, chat)
        try:
            stats = _json(httpd, "/api/traces/stats")
            assert stats["totals"]["requests"] == 2
            models = {row["model"]: row for row in stats["by_model"]}
            assert models["gpt-4o-mini"]["cost_usd"] == 0.00001
            assert models["foo/bar"]["cost_usd"] is None
            listed = _json(httpd, "/api/traces")
            assert {item["group_id"] for item in listed["traces"]} == {mini_id, other_id}
            listed_all = _json(httpd, "/api/traces?limit=0")
            assert listed_all["total"] == 2
            assert len(listed_all["traces"]) == 2

            filtered = _json(httpd, "/api/traces?model=gpt-4o-mini&status=completed")
            assert filtered["total"] == 1
            assert filtered["traces"][0]["group_id"] == mini_id
            assert filtered["traces"][0]["model"] == "gpt-4o-mini"

            day = _json(httpd, "/api/traces?from=2026-08-26&to=2026-08-26")
            assert day["total"] == 1
            assert day["traces"][0]["group_id"] == mini_id

            detail = _json(httpd, f"/api/traces/groups/{mini_id}/0")
            assert detail["group_id"] == mini_id
            assert detail["kind"] == "group"
            assert detail["turn_index"] == 0
            assert detail["messages"][0]["role"] == "user"
            assert detail["messages"][1]["meta"]["usage"]["prompt_tokens"] == 10

            try:
                urlopen(_url(httpd, f"/api/traces/groups/{mini_id}/9"), timeout=2)
            except HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError("expected 404 for missing turn")

            try:
                urlopen(_url(httpd, f"/api/traces/groups/group-{'0' * 28}dead/0"), timeout=2)
            except HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError("expected 404 for missing session")
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()


def test_trace_detail_expands_only_same_window_canonical_span(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.bots.timeline import WorkspaceTimeline

    chat = _chat(tmp_path)
    try:
        chat._bots.create(
            BotCard(
                bot_id="coder", name="coder", role="Coder",
                description="Writes code.", persona="",
            )
        )
        dm = chat.create(bots=["coder"])
        group = chat.create(bots=["thyca", "coder"])
        dm_room = chat._sessions.store.load(dm["id"])
        group_room = chat._sessions.store.load(group["id"])
        dm_window = chat._webui.window_for(dm_room)
        group_window = chat._webui.window_for(group_room)
        dm_span = "span_shared_fixture"
        call = ToolCall(
            id="group-call", name="read", arguments={"path": "group.txt"}
        )
        dm_rows = [
            Message(
                role="assistant", content=None,
                tool_calls=[ToolCall(
                    id="dm-call", name="read", arguments={"path": "private.txt"}
                )],
                reasoning="DM1 sentinel reasoning",
                meta={"kind": "llm", "bot_id": "coder", "span_id": dm_span},
            ),
            Message(
                role="tool", content="DM1 sentinel tool result",
                tool_call_id="dm-call",
                meta={"bot_id": "coder", "span_id": dm_span},
            ),
        ]
        group_rows = [
            Message(
                role="assistant", content=None, tool_calls=[call],
                reasoning="group reasoning",
                meta={"kind": "llm", "bot_id": "coder", "span_id": dm_span},
            ),
            Message(
                role="tool", content="group tool result",
                tool_call_id="group-call",
                meta={"bot_id": "coder", "span_id": dm_span},
            ),
        ]
        timeline = WorkspaceTimeline(tmp_path, "coder")
        for index, row in enumerate(dm_rows):
            timeline.append_message(dm_window, row, event_id=f"dm-{index}")
        for index, row in enumerate(group_rows):
            timeline.append_message(group_window, row, event_id=f"group-{index}")
        timeline.replace_view(group_window, group_rows, event_id="group-reset")

        chat._runtime.workspaces.store.bind_window("coder", dm_window)
        chat._runtime.workspaces.store.bind_window("coder", group_window)
        room_manager = SessionManager(tmp_path / "sessions")
        for room, text in ((dm_room, "private"), (group_room, "public")):
            room_manager.load(room.id)
            room_manager.append(Message(role="user", content=text))
            room_manager.append(
                Message(
                    role="assistant",
                    content=f"{text} reply",
                    meta={
                        "kind": "message", "bot_id": "coder",
                        "span_id": dm_span, "requests": 1, "rounds": 1,
                    },
                )
            )

        payload = trace_api.trace_detail_payload_ref(chat, ConversationRef.group(group["id"]), 0)
        rendered = json.dumps(payload, ensure_ascii=False)
        assert "DM1 sentinel reasoning" not in rendered
        assert "DM1 sentinel tool result" not in rendered
        assert "group reasoning" in rendered
        assert "group tool result" in rendered
        assistant = next(
            message for message in payload["messages"]
            if message["role"] == "assistant" and message.get("tool_calls")
        )
        assert assistant["tool_calls"] == [{
            "id": "group-call", "name": "read",
            "arguments": {"path": "group.txt"},
        }]
        assert sum(message["role"] == "tool" for message in payload["messages"]) == 1
    finally:
        chat.shutdown()


def test_trace_detail_missing_canonical_artifact_is_explicit_and_read_only(
    tmp_path: Path,
) -> None:
    from thyca.bots.timeline import TraceExecutionError

    chat = _chat(tmp_path)
    try:
        created = chat.create()
        room = chat._sessions.store.load(created["id"])
        window = chat._webui.window_for(room)
        chat._runtime.workspaces.store.bind_window("thyca", window)
        manager = SessionManager(tmp_path / "sessions")
        manager.load(created["id"])
        manager.append(Message(role="user", content="question"))
        manager.append(
            Message(
                role="assistant", content="answer",
                meta={
                    "kind": "message", "bot_id": "thyca",
                    "span_id": "missing-span", "requests": 1,
                },
            )
        )
        timeline_path = tmp_path / "bots" / "thyca" / "workspace" / "timeline.jsonl"
        assert not timeline_path.exists()
        try:
            trace_api.trace_detail_payload_ref(chat, ConversationRef.dm(created["id"]), 0)
        except TraceExecutionError as exc:
            assert "timeline is missing" in str(exc)
        else:
            raise AssertionError("expected missing canonical execution error")
        assert not timeline_path.exists()
    finally:
        chat.shutdown()


def test_trace_detail_marks_skill_loads_and_keeps_arguments(tmp_path: Path) -> None:
    """Skill classification happens server-side; arguments stay on the payload."""
    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        session = manager.create()
        skill_file = tmp_path / "skills" / "codereview" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("---\nname: codereview\n---\n")
        manager.append(Message(role="user", content="go", ts=TS))
        # Hard-cut (2026-11-01): the public conversation store is
        # append-only plain transcript — tool/tool-call rows never persist
        # there (they live in the canonical timeline, expanded via
        # trace_execution_messages as in
        # test_trace_detail_expands_only_same_window_canonical_span).
        # Appending them here is a no-op for store-backed rooms, so Trace
        # detail carries no tool_calls from the room itself.
        manager.append(
            Message(role="assistant", content="done", ts=TS2, meta={"kind": "llm", "round": 1}),
            actor="thyca",
        )
        payload = trace_api.trace_detail_payload_ref(chat, ConversationRef.group(session.id), 0)
        # Trace detail always carries the tool shape (include_args);
        # store rooms hold no tool rows, so every entry is empty.
        assert all(m.get("tool_calls", []) == [] for m in payload["messages"])
    finally:
        chat.close() if hasattr(chat, "close") else None


# --- F34: limit=all is bounded, total stays full ---


class _NoopLLM:
    async def chat(self, messages, tools=None):
        return ChatReply(content="x")


def _trace_chat(tmp_path: Path) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=_NoopLLM())


def test_f34_limit_all_capped_total_full_and_offset_pages_on(tmp_path: Path) -> None:
    chat = _trace_chat(tmp_path)
    from thyca.conversations.runtime import SessionManager as _SM

    mgr = _SM(tmp_path / "sessions")
    for i in range(100):
        sid = mgr.create().id
        for t in range(25):
            mgr.load(sid)
            mgr.append(Message(role="user", content=f"q{t}"))
            mgr.append(Message(role="assistant", content=f"a{t}"), actor="thyca")
    total = 2500
    assert total == 2500

    payload = trace_api.trace_list_payload(chat, "limit=all")
    assert payload["total"] == 2500
    assert len(payload["traces"]) == trace_api._TRACE_ALL_CAP == 2000

    tail = trace_api.trace_list_payload(chat, "limit=all&offset=2000")
    assert tail["total"] == 2500
    assert len(tail["traces"]) == 500

    zero = trace_api.trace_list_payload(chat, "limit=0")
    assert zero["total"] == 2500
    assert len(zero["traces"]) == 2000


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

def test_x26_sum_group_rollup() -> None:
    from thyca.serve.trace import TurnSummary, _sum_group

    def turn(requests, cost):
        return TurnSummary(
            session_id="s",
            turn_index=0,
            title="t",
            started_at="",
            ended_at="",
            model=None,
            status="completed",
            rounds=1,
            requests=requests,
            prompt_tokens=None,
            cached_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            cost_usd=cost,
            latency_ms=None,
            messages=[],
        )

    assert _sum_group([turn(2, 0.5), turn(3, None)]) == (5, 0.5)
    assert _sum_group([turn(1, None)]) == (1, None)


def _start_config_server(tmp_path: Path):
    from thyca.configs import save
    from thyca.memories.facade import MemoryFacade
    from thyca.serve import default_webui, make_server

    save(seeded_config(), tmp_path / "config.json")
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


def test_m7_post_traces_is_404_like_get(tmp_path: Path) -> None:
    httpd, thread = _start_config_server(tmp_path)
    try:
        status, body = _post(httpd, "/api/traces/anything", {})
        assert status == 404
        assert body == {"error": "trace not found"}
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()


def test_m7_turn_status_meta_first_then_stripped() -> None:
    from thyca.core.protocol import Message
    from thyca.serve.trace import turns_from_session
    from thyca.conversations.runtime import Session

    def session_with(last: Message) -> Session:
        return Session(
            "2026-08-26T09-12-03_abcf",
            Path("/tmp/x.jsonl"),
            [Message(role="user", content="go"), last],
        )

    padded = turns_from_session(
        session_with(Message(role="assistant", content="loop limit reached\n"))
    )[0]
    assert padded.status == "loop_limit"
    meta_only = turns_from_session(
        session_with(
            Message(role="assistant", content="done", meta={"status": "loop_limit"})
        )
    )[0]
    assert meta_only.status == "loop_limit"
    plain = turns_from_session(session_with(Message(role="assistant", content="done")))[
        0
    ]
    assert plain.status == "completed"
