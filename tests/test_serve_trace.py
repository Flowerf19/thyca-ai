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
from thyca.config import default_config, load, save
from thyca.llm.llm_base import ChatReply
from thyca.core.protocol import Message, ToolCall
from thyca.serve import default_webui, make_server
from thyca.sessions import SessionManager
from thyca.sessions.store import SessionStore
from thyca.memory.facade import MemoryFacade

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


def test_concurrent_trace_list_and_detail_do_not_race_cache(tmp_path: Path) -> None:
    """List (iterate/evict) + detail (insert) share ``_trace_sessions``.

    Under ThreadingHTTPServer the overlap used to raise ``RuntimeError:
    dictionary changed size`` (→ 503). The shrunk window keeps detail
    inserting entries the list scan keeps evicting, so every round races
    without the lock.
    """
    chat = _chat(tmp_path)
    cap = trace_api._TRACE_SCAN_CAP
    try:
        manager = SessionManager(tmp_path / "sessions")
        ids = [
            _append_turn(manager, model="m", content=f"turn {index}", cost=None)
            for index in range(8)
        ]
        # Pin a stable window: the first six files are oldest, so they sit
        # outside a cap-2 scan window no matter the creation order.
        base = 1_700_000_000
        for rank, sid in enumerate(ids):
            path = tmp_path / "sessions" / f"{sid}.jsonl"
            os.utime(path, (base + rank, base + rank))
        ordered = [path.stem for path in chat.trace_store().list_paths()]
        assert ordered == ids[::-1]
        outside = ordered[2:]
        assert len(outside) == 6

        trace_api._TRACE_SCAN_CAP = 2
        trace_api._trace_sessions.clear()
        # Fat first-round scan window: the eviction pass iterates thousands
        # of entries while detail threads insert (size change mid-iteration
        # is the RuntimeError). Later rounds keep churning: detail
        # re-inserts outside-window entries every list scan evicts.
        trace_api._trace_sessions.update(
            (Path(f"/stale/{index}.jsonl"), (0, object())) for index in range(2000)
        )
        errors: list[Exception] = []
        barrier = threading.Barrier(5)
        interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)
        try:
            def lister() -> None:
                barrier.wait(timeout=10)
                try:
                    for _ in range(300):
                        trace_api.collect_turns(chat)
                except Exception as exc:
                    errors.append(exc)

            def detailer(slot: int) -> None:
                store = chat.trace_store()
                sids = outside[slot:] + outside[:slot]
                barrier.wait(timeout=10)
                try:
                    for _ in range(100):
                        for sid in sids:
                            trace_api.cached_turns_for(store, sid)
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
            sys.setswitchinterval(interval)
    finally:
        trace_api._TRACE_SCAN_CAP = cap
        trace_api._trace_sessions.clear()
        chat.shutdown()


def test_trace_scan_cache_reuses_unchanged_files(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        session_id = _append_turn(manager, model="gpt-4o-mini", content="cached turn", cost=0.00001)

        trace_api._trace_sessions.clear()
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
                # unchanged files: no re-parse, totals identical
                second = _json(httpd, "/api/traces/stats")
                assert second == first
                assert scans == []
                # appended line changes mtime_ns: only that file is re-parsed
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
                assert scans == [f"{session_id}.jsonl"]
            finally:
                SessionStore.scan = original_scan  # type: ignore[method-assign]
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()


def test_trace_detail_after_list_does_not_reparse_unchanged_files(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        session_id = _append_turn(manager, model="gpt-4o-mini", content="detail turn", cost=0.00001)

        trace_api._trace_sessions.clear()
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
                detail = _json(httpd, f"/api/traces/{session_id}/0")
                assert detail["session_id"] == session_id
                assert scans == []  # cache hit: detail skips re-parse
            finally:
                SessionStore.scan = original_scan  # type: ignore[method-assign]
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()


def test_trace_scan_cache_evicts_files_outside_window(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        _append_turn(manager, model="gpt-4o-mini", content="evict turn", cost=0.00001)
        ghost = tmp_path / "sessions" / "ghost.jsonl"

        trace_api._trace_sessions.clear()
        trace_api._trace_sessions[ghost] = (0, object())

        httpd, thread = _start(tmp_path, chat)
        try:
            stats = _json(httpd, "/api/traces/stats")
            assert stats["totals"]["requests"] == 1
            assert ghost not in trace_api._trace_sessions
            assert len(trace_api._trace_sessions) == 1
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()


def test_trace_scan_cache_skips_then_recovers_corrupt_file(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    try:
        manager = SessionManager(tmp_path / "sessions")
        session_id = _append_turn(manager, model="gpt-4o-mini", content="recover turn", cost=0.00001)
        path = tmp_path / "sessions" / f"{session_id}.jsonl"

        trace_api._trace_sessions.clear()
        httpd, thread = _start(tmp_path, chat)
        try:
            assert _json(httpd, "/api/traces/stats")["totals"]["requests"] == 1
            # corrupting the file must not poison the cache: skip, no crash
            good = path.read_text(encoding="utf-8")
            path.write_text(good + "{bad\n", encoding="utf-8")
            assert _json(httpd, "/api/traces/stats")["totals"]["requests"] == 0
            assert path not in trace_api._trace_sessions
            # repairing the file is picked up again
            path.write_text(good, encoding="utf-8")
            assert _json(httpd, "/api/traces/stats")["totals"]["requests"] == 1
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
            listed_all = _json(httpd, "/api/traces?limit=0")
            assert listed_all["total"] == 2
            assert len(listed_all["traces"]) == 2

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
        manager.append(
            Message(
                role="assistant",
                content=None,
                ts=TS,
                tool_calls=[
                    ToolCall(id="c1", name="read", arguments={"path": str(skill_file)}),
                    ToolCall(id="c2", name="read", arguments={"path": str(tmp_path / "notes.md")}),
                ],
                meta={"kind": "llm", "round": 1},
            )
        )
        manager.append(Message(role="tool", tool_call_id="c1", content="---", ts=TS2))
        manager.append(Message(role="tool", tool_call_id="c2", content="notes", ts=TS2))
        manager.append(
            Message(role="assistant", content="done", ts=TS2, meta={"kind": "llm", "round": 2})
        )

        trace_api._trace_sessions.clear()
        payload = trace_api.trace_detail_payload(chat, session.id, 0)

        calls = next(m for m in payload["messages"] if m["tool_calls"])
        assert calls["tool_calls"][0] == {
            "id": "c1",
            "name": "read",
            "arguments": {"path": str(skill_file)},
            "skill": "codereview",
        }
        assert calls["tool_calls"][1] == {
            "id": "c2",
            "name": "read",
            "arguments": {"path": str(tmp_path / "notes.md")},
        }
    finally:
        chat.close() if hasattr(chat, "close") else None


# --- F34: limit=all is bounded, total stays full ---


class _NoopLLM:
    async def chat(self, messages, tools=None):
        return ChatReply(content="x")


def _trace_chat(tmp_path: Path) -> ChatApp:
    save(default_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=_NoopLLM())


def _write_turn_files(sessions_dir: Path, n_files: int, turns_per_file: int) -> int:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        sid = (
            f"2026-09-{(i % 28) + 1:02d}"
            f"T10-{(i * 7) % 60:02d}-{(i * 13) % 60:02d}_{i:04x}"
        )
        lines = []
        for t in range(turns_per_file):
            lines.append(json.dumps({"role": "user", "content": f"q{t}", "ts": "2026-09-01T10:00:00Z"}))
            lines.append(json.dumps({"role": "assistant", "content": f"a{t}", "ts": "2026-09-01T10:00:01Z"}))
        (sessions_dir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n_files * turns_per_file


def test_f34_limit_all_capped_total_full_and_offset_pages_on(tmp_path: Path) -> None:
    total = _write_turn_files(tmp_path / "sessions", 100, 25)
    assert total == 2500
    chat = _trace_chat(tmp_path)
    trace_api._trace_sessions.clear()

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
    from thyca.sessions import Session

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
