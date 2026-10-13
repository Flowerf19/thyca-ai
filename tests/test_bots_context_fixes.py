"""Regression tests for the backend context review findings (9 fixes)."""
from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.admin_tools import AdminDeps, register_admin_tools
from thyca.bots.bus import MAX_REF_CHARS, MAX_REFS, Bus, Envelope
from thyca.bots.card import BotCard, BotMemoryPolicy
from thyca.bots.context import BotTurnContext, bind_bot_turn_context, reset_bot_turn_context
from thyca.bots.registry import BotRegistry, RegistryError
from thyca.bots.runtime import BotRuntime, filtered_tool_schemas
from thyca.bots.tasks import derive_open_tasks
from thyca.config import default_config, load, save
from thyca.core.protocol import Message, ToolCall
from thyca.llm.llm_base import ChatReply
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.serve.trace import aggregate, turns_from_session
from thyca.sessions import SessionManager
from thyca.tools.registry import ToolRegistry


def _chat(tmp_path: Path, connect, **kw) -> ChatApp:
    from dataclasses import replace

    cfg = default_config()
    if "context_tokens" in kw:
        cfg = replace(cfg, limits=replace(cfg.limits, contextTokens=kw["context_tokens"]))
    save(cfg, tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _LLM:
    def __init__(self, content: str = "bot reply") -> None:
        self.content = content
        self.prompts: list[str] = []

    async def chat(self, messages, tools=None):
        self.prompts.append("\n".join(str(m.content or "") for m in messages))
        return ChatReply(content=self.content, model="m",
                         usage={"prompt_tokens": 5, "completion_tokens": 1,
                                "total_tokens": 6})


def _msg(role: str, content: str = "", **kw) -> Message:
    kw.setdefault("ts", "2026-08-01T10:00:00Z")
    return Message(role=role, content=content, **kw)


# --- 1. bot_fetch authorization -------------------------------------------

def test_bot_fetch_only_sender_or_recipient_get_content(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    for bot_id in ("coder", "helper", "researcher"):
        registry.create(BotCard(bot_id=bot_id, name=bot_id, role="R",
                                description="D", persona="P"))
    bus = Bus()
    runtime = BotRuntime(tmp_path, registry, bus)
    tools = ToolRegistry()
    register_admin_tools(tools, AdminDeps(registry, runtime, lambda: None))
    body = "SECRET-BODY-" + "z" * 4000
    envelope = runtime.send(sender="thyca", recipient="coder", kind="delegate",
                            session_id="room1", payload=body)
    # The artifact store recorded both parties at Bus.send time.
    assert bus.artifacts.parties(envelope.span_id) == ("thyca", "coder")

    spec = tools.get("bot_fetch")
    assert spec is not None

    def fetch_as(bot_id: str) -> str:
        ctx_token = bind_bot_turn_context(
            BotTurnContext(sender_id=bot_id, room_id="room1"))
        bot_token = bind_bot_id(bot_id)
        try:
            return asyncio.run(spec.handler({"span_id": envelope.span_id}))
        finally:
            reset_bot_id(bot_token)
            reset_bot_turn_context(ctx_token)

    # A third bot is denied and learns nothing of the body.
    denied = fetch_as("researcher")
    assert json.loads(denied)["ok"] is False
    assert "SECRET-BODY" not in denied and body not in denied
    # Recipient and sender can both fetch the full body.
    assert json.loads(fetch_as("coder"))["content"] == body
    assert json.loads(fetch_as("thyca"))["content"] == body
    # Unknown spans stay an honest not-found for a party-shaped caller.
    ctx_token = bind_bot_turn_context(BotTurnContext(sender_id="coder", room_id="r"))
    try:
        missing = json.loads(asyncio.run(spec.handler({"span_id": "sp_nope"})))
    finally:
        reset_bot_turn_context(ctx_token)
    assert missing["ok"] is False and "not found" in missing["error"]


# --- 2. Baseline tools on load ----------------------------------------------

def test_legacy_card_gets_baseline_but_policies_still_win() -> None:
    def schemas(*names: str) -> list[dict]:
        return [{"type": "function", "function": {"name": name}} for name in names]

    # Pre-existing card: tools list predates bot_fetch/task_open entirely.
    legacy = BotCard(bot_id="coder", name="coder", role="C", description="D",
                     persona="P", tools=("read",))
    kept = {s["function"]["name"] for s in filtered_tool_schemas(
        schemas("bot_send", "bot_fetch", "task_open", "memory_remember", "read"),
        legacy)}
    assert {"bot_fetch", "task_open", "bot_send", "memory_remember", "read"} <= kept

    # hidden_tools and memory/bash policies still win over the baseline.
    masked = BotCard(bot_id="coder", name="coder", role="C", description="D",
                     persona="P", tools=("read",), hidden_tools=("bot_fetch",),
                     memory=BotMemoryPolicy(read=True, write=False, propose_team=True),
                     bash="deny")
    kept = {s["function"]["name"] for s in filtered_tool_schemas(
        schemas("bot_fetch", "task_open", "memory_remember", "memory_search",
                "bash", "read"),
        masked)}
    assert "bot_fetch" not in kept       # hidden wins
    assert "memory_remember" not in kept  # memory.write=false wins
    assert "bash" not in kept            # bash=deny wins
    assert {"task_open", "memory_search", "read"} <= kept


# --- 3 + 9. task_open lifecycle and latest-status ----------------------------

def test_task_open_key_closes_on_linked_result_and_status_updates() -> None:
    call = ToolCall(id="t1", name="task_open", arguments={"title": "long research"})
    ack = json.dumps({"ok": True, "status": "open", "key": "sp_task1",
                      "span_id": "sp_task1", "task_key": "sp_task1"})
    msgs = [
        _msg("assistant", "", tool_calls=[call]),
        _msg("tool", ack, tool_call_id="t1"),
    ]
    tasks = derive_open_tasks(msgs, bot_id="coder")
    assert len(tasks) == 1 and tasks[0].key == "sp_task1"
    assert tasks[0].status == "open"

    # Latest status/step event (linked by the returned key) wins.
    with_status = msgs + [
        _msg("assistant", "halfway there", meta={
            "kind": "status", "span_id": "sp_s1", "parent_span_id": "sp_task1"}),
        _msg("assistant", "wiring done", meta={
            "kind": "step", "span_id": "sp_s2", "parent_span_id": "sp_task1"}),
    ]
    tasks = derive_open_tasks(with_status, bot_id="coder")
    assert len(tasks) == 1 and tasks[0].status == "wiring done"

    # A linked result event closes it; a linked error closes it too.
    closed = with_status + [
        _msg("assistant", "done", meta={
            "kind": "result", "span_id": "sp_r", "parent_span_id": "sp_task1"})]
    assert derive_open_tasks(closed, bot_id="coder") == []
    errored = msgs + [
        _msg("assistant", "boom", meta={
            "kind": "error", "span_id": "sp_e", "parent_span_id": "sp_task1"})]
    assert derive_open_tasks(errored, bot_id="coder") == []
    # The ack alone never closes (already covered) and a status row for an
    # unknown parent opens nothing.
    orphan = [_msg("assistant", "noise", meta={
        "kind": "status", "span_id": "sp_x", "parent_span_id": "sp_nope"})]
    assert derive_open_tasks(orphan, bot_id="coder") == []


def test_task_open_end_to_end_in_bot_transcript(tmp_path: Path) -> None:
    class TaskLLM:
        async def chat(self, messages, tools=None):
            text = "\n".join(str(m.content or "") for m in messages)
            if "PERSONA-CODER" not in text:
                return ChatReply(content="ok", model="m")
            if any(m.role == "tool" for m in messages):
                return ChatReply(content="opened reply", model="m")
            return ChatReply(content=None, model="m", tool_calls=[
                ToolCall(id="tc-open", name="task_open",
                         arguments={"title": "long research"})])

    app = _chat(tmp_path, TaskLLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        room = app.create(bots=["coder"])["id"]
        detail = app.turn(room, "start the long research please")
        assert detail["reply"] == "opened reply"

        internal = app._runtime.internal_session("coder", room)
        manager = SessionManager(store=app._runtime.store_for("coder"))
        manager.load(internal.id)
        tasks = derive_open_tasks(list(manager.current.messages), bot_id="coder")
        assert len(tasks) == 1 and tasks[0].task == "long research"
        key = tasks[0].key
        assert key.startswith("sp_")
        # A linked status then result, appended as transcript events,
        # update then close the very task the tool opened.
        manager.append(_msg("assistant", "halfway", meta={
            "kind": "status", "span_id": "sp_st", "parent_span_id": key}))
        tasks = derive_open_tasks(list(manager.current.messages), bot_id="coder")
        assert len(tasks) == 1 and tasks[0].status == "halfway"
        manager.append(_msg("assistant", "finished", meta={
            "kind": "result", "span_id": "sp_rs", "parent_span_id": key}))
        assert derive_open_tasks(list(manager.current.messages), bot_id="coder") == []
    finally:
        app.shutdown()


# --- 4 + 8. DM race, participant protection, blank-DM prune ------------------

def test_bot_dm_concurrent_lookups_create_exactly_one_dm(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        results: list[str] = []
        errors: list[Exception] = []

        def lookup() -> None:
            try:
                results.append(app.bot_dm("coder")["id"])
            except Exception as exc:  # collected, asserted below
                errors.append(exc)

        threads = [threading.Thread(target=lookup) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        assert len(results) == 8 and len(set(results)) == 1
        dms = [s for s in app._sessions.list_sessions() if s.dm_bot == "coder"]
        assert len(dms) == 1 and dms[0].id == results[0]
    finally:
        app.shutdown()


def test_dm_participants_protected_and_blank_dm_survives_prune(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        dm_id = app.bot_dm("coder")["id"]  # blank: no turn yet
        # Creating another session prunes blank sessions — the DM stays.
        other = app.create(bots=["thyca"])
        assert other["id"] != dm_id
        assert app._sessions.store.load(dm_id).dm_bot == "coder"
        assert app.bot_dm("coder")["id"] == dm_id

        # The DM's own bot cannot be removed; nobody can be added.
        with pytest.raises(RegistryError):
            app.set_room_bots(dm_id, "coder", "remove")
        with pytest.raises(RegistryError):
            app.set_room_bots(dm_id, "helper", "add")
        assert app.room_bots(dm_id) == ["coder"]
        assert app._sessions.store.load(dm_id).dm_bot == "coder"
        # Ordinary rooms keep working exactly as before.
        assert app.set_room_bots(other["id"], "coder", "add") == ["thyca", "coder"]
        assert app.set_room_bots(other["id"], "coder", "remove") == ["thyca"]
    finally:
        app.shutdown()


# --- 5. Idle sweep skips rooms with a turn in flight --------------------------

def test_bot_idle_sweep_skips_internal_session_of_busy_room(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM(content="[]"), context_tokens=2000)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        room = app.create(bots=["coder"])["id"]
        internal = app._runtime.internal_session("coder", room)
        manager = SessionManager(store=app._runtime.store_for("coder"))
        manager.load(internal.id)
        for index in range(8):
            manager.append(_msg("user", f"q{index} " + "u" * 400,
                                ts=f"2020-01-01T10:{index:02d}:00Z"))
            manager.append(_msg("assistant", f"a{index} " + "a" * 400,
                                ts=f"2020-01-01T10:{index:02d}:30Z"))

        app._turns.claim(room)
        try:
            report = app.sweep_idle_sessions(idle_s=900)
        finally:
            app._turns.release(room)
        key = f"coder:{internal.id}"
        assert key not in report["bots"], "busy room's bot session must be skipped"
        messages = app._runtime.store_for("coder").load(internal.id).messages
        assert not any((m.content or "").startswith("[compaction: ") for m in messages)

        report = app.sweep_idle_sessions(idle_s=900)
        assert report["bots"][key]["compacted"] is True
        messages = app._runtime.store_for("coder").load(internal.id).messages
        assert any((m.content or "").startswith("[compaction: ") for m in messages)
    finally:
        app.shutdown()


# --- 6. Flush prediction alignment + recall one-shot --------------------------

def test_flush_prediction_counts_mailbox_wire_and_summary_reserve(
    tmp_path: Path,
) -> None:
    # Transcript + hot + tools fits under the backstop on its own; the
    # mailbox-prepended wire text (what the loop actually assembles)
    # pushes it over. The old pre-check (raw chars/4 of the bare text)
    # missed this and let compaction land with no flush first.
    llm = _LLM()
    app = _chat(tmp_path, llm, context_tokens=3000)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        room = app.create(bots=["coder"])["id"]
        internal = app._runtime.internal_session("coder", room)
        manager = SessionManager(store=app._runtime.store_for("coder"))
        manager.load(internal.id)
        for index in range(2):
            manager.append(_msg("user", f"q{index} " + "u" * 200,
                                ts=f"2026-08-01T10:{index:02d}:00Z"))
            manager.append(_msg("assistant", f"a{index} " + "a" * 200,
                                ts=f"2026-08-01T10:{index:02d}:30Z"))
        for _ in range(5):
            app._runtime.send(sender="thyca", recipient="coder", kind="message",
                              session_id=room, payload="m" * 900)
        flush_calls: list[tuple[str, bool]] = []
        app._runtime.memory_flush = lambda bot_id, messages, cfg: flush_calls.append(
            (bot_id, any((m.content or "").startswith("[compaction: ")
                         for m in messages)))
        app.turn(room, "hi")
        assert flush_calls == [("coder", False)], (
            "flush must run once, on the intact transcript, before compact")
        messages = app._runtime.store_for("coder").load(internal.id).messages
        assert any((m.content or "").startswith("[compaction: ") for m in messages)
    finally:
        app.shutdown()


def test_recall_stays_armed_after_failed_turn(tmp_path: Path) -> None:
    class FlakyLLM:
        def __init__(self) -> None:
            self.fail = True
            self.prompts: list[str] = []

        async def chat(self, messages, tools=None):
            text = "\n".join(str(m.content or "") for m in messages)
            self.prompts.append(text)
            if "PERSONA-CODER" in text and self.fail:
                raise RuntimeError("provider down")
            return ChatReply(content="ok reply", model="m")

    llm = FlakyLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        room = app.create(bots=["coder"])["id"]
        app._runtime.memory_searcher = (
            lambda text, bot_id: ["- recalled: phoenix fact"])
        app._runtime._reinject_pending.add(("coder", room))

        with pytest.raises(Exception):
            app.turn(room, "first attempt")
        assert ("coder", room) in app._runtime._reinject_pending, (
            "a failed turn must not consume the one-shot recall")

        llm.fail = False
        before = len(llm.prompts)
        app.turn(room, "second attempt")
        assert any("phoenix fact" in p for p in llm.prompts[before:])
        assert ("coder", room) not in app._runtime._reinject_pending

        before = len(llm.prompts)
        app.turn(room, "third attempt")
        assert all("phoenix fact" not in p for p in llm.prompts[before:])
    finally:
        app.shutdown()


# --- 7. Refs bounds -------------------------------------------------------------

def test_bus_send_caps_ref_count_and_length() -> None:
    bus = Bus()
    bus.register("coder")
    refs = ["r" * 1000] + [f"/tmp/file-{i}.md" for i in range(11)]
    envelope = bus.send(sender="a", recipient="coder", kind="message",
                        session_id="r", payload="hi", refs=refs)
    assert len(envelope.refs) == MAX_REFS
    assert all(len(ref) <= MAX_REF_CHARS for ref in envelope.refs)
    assert envelope.refs[0] == "r" * MAX_REF_CHARS


# --- 8. Delivered truncated envelope carries the fetch marker -------------

_BODY_TAIL = "TAIL-SENTINEL-END"


class _DeliveryLLM:
    """Thyca delegates ``body`` to coder; coder bot_fetches on the marker."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.prompts: list[str] = []

    async def chat(self, messages, tools=None):
        text = "\n".join(str(m.content or "") for m in messages)
        self.prompts.append(text)
        tool_text = "\n".join(
            str(m.content or "") for m in messages if m.role == "tool"
        )
        if "PERSONA-CODER" in text:
            if tool_text:
                fetched = _BODY_TAIL in tool_text
                return ChatReply(
                    content="coder fetched full body" if fetched else "coder fetch missing",
                    model="m-coder",
                    usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                )
            match = re.search(
                r"\[truncated: full body via bot_fetch span_id=(sp_[^\]]+)\]", text
            )
            if match:
                return ChatReply(content=None, model="m-coder", tool_calls=[
                    ToolCall(id="c-fetch", name="bot_fetch",
                             arguments={"span_id": match.group(1)}),
                ])
            return ChatReply(content="coder reply", model="m-coder",
                             usage={"prompt_tokens": 5, "completion_tokens": 1,
                                    "total_tokens": 6})
        if tool_text:
            return ChatReply(content="thyca final", model="m",
                             usage={"prompt_tokens": 5, "completion_tokens": 1,
                                    "total_tokens": 6})
        return ChatReply(content=None, model="m", tool_calls=[
            ToolCall(id="t-delegate", name="bot_delegate",
                     arguments={"bot_id": "coder", "task": self.body}),
        ])


def _run_delivery(tmp_path: Path, body: str):
    llm = _DeliveryLLM(body)
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        room = app.create(bots=["thyca", "coder"])["id"]
        detail = app.turn(room, "please coordinate")
        rows = app.get_payload(room)["messages"]
        return llm, detail, rows
    finally:
        app.shutdown()


def test_delivered_truncated_envelope_prompt_has_fetch_marker_and_recipient_fetches(
    tmp_path: Path,
) -> None:
    body = "HEAD-" + "x" * 1500 + "-" + _BODY_TAIL
    llm, detail, rows = _run_delivery(tmp_path, body)
    assert detail["reply"] == "thyca final"
    coder_prompts = [p for p in llm.prompts if "PERSONA-CODER" in p]
    assert coder_prompts, "recipient bot never ran"
    match = re.search(
        r"\[truncated: full body via bot_fetch span_id=(sp_[^\]]+)\]",
        coder_prompts[0],
    )
    assert match, "delivered recipient prompt lacks the truncation fetch marker"
    # The marker's span is the envelope's own span (the room row for it).
    envelope_rows = [
        row for row in rows
        if (row.get("meta") or {}).get("kind") == "delegate"
    ]
    assert envelope_rows and envelope_rows[0]["meta"]["span_id"] == match.group(1)
    # The autonomous recipient acted on the marker: its bot_fetch (as the
    # envelope's recipient) returned the full body past the 1000-char cut.
    result_rows = [
        row for row in rows
        if (row.get("meta") or {}).get("bot_id") == "coder"
        and (row.get("meta") or {}).get("kind") == "result"
    ]
    assert result_rows and result_rows[0]["content"] == "coder fetched full body"


def test_delivered_short_envelope_prompt_has_no_fetch_marker(tmp_path: Path) -> None:
    llm, detail, _rows = _run_delivery(tmp_path, "short task")
    assert detail["reply"] == "thyca final"
    coder_prompts = [p for p in llm.prompts if "PERSONA-CODER" in p]
    assert coder_prompts
    assert "[truncated:" not in coder_prompts[0]
    assert "bot_fetch span_id=" not in coder_prompts[0]


# --- 9. Thyca's own turns attribute to thyca in trace by_bot ----------------

def test_plain_thyca_turn_attributes_to_thyca_not_unknown(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM(content="thyca reply"))
    try:
        room = app.create()["id"]
        detail = app.turn(room, "hello thyca")
        assert detail["reply"] == "thyca reply"
        session = app.trace_store().load(room)
        assistant = [m for m in session.messages if m.role == "assistant"]
        assert assistant and all(
            (m.meta or {}).get("bot_id") == "thyca" for m in assistant
        )
        turns = turns_from_session(session)
        by_bot = {row["bot_id"]: row for row in aggregate(turns)["by_bot"]}
        assert by_bot["thyca"]["requests"] >= 1
        assert "unknown" not in by_bot
    finally:
        app.shutdown()


def test_mailbox_render_clips_refs() -> None:
    envelope = Envelope(
        sender="a", recipient="coder", kind="message", session_id="room1",
        trace_id="tr_1", parent_span_id=None, ts="2026-08-01T10:00:00Z",
        payload="see refs", span_id="sp_1",
        refs=tuple(f"ref{i}-" + "x" * 600 for i in range(12)),
    )
    rendered = BotRuntime._mailbox_text([envelope], "room1", "hi")
    refs_line = [line for line in rendered.splitlines()
                 if line.startswith("  refs:")][0]
    assert "ref8-" not in refs_line and "ref11-" not in refs_line
    entries = refs_line.removeprefix("  refs: ").split(", ")
    assert len(entries) == MAX_REFS
    assert all(len(entry) <= MAX_REF_CHARS for entry in entries)


# --- 10. DM privacy + message_user (user decision 2026-10-13) ----------------

_GREETING = "GREETING-FROM-CODER"


class _DmFlowLLM:
    """Thyca delegates to coder; coder greets the user via message_user.

    ``greet=False`` instead makes coder answer plainly (its reply then
    returns to Thyca as a tool result only)."""

    def __init__(self, *, greet: bool) -> None:
        self.greet = greet

    async def chat(self, messages, tools=None):
        text = "\n".join(str(m.content or "") for m in messages)
        tool_text = "\n".join(
            str(m.content or "") for m in messages if m.role == "tool"
        )
        usage = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
        if "PERSONA-CODER" in text:
            if tool_text:
                return ChatReply(content="coder done", model="m-coder",
                                 usage=usage)
            if self.greet:
                return ChatReply(content=None, model="m-coder", tool_calls=[
                    ToolCall(id="c-dm", name="message_user",
                             arguments={"message": _GREETING}),
                ])
            return ChatReply(content="coder plain answer", model="m-coder",
                             usage=usage)
        if tool_text:
            return ChatReply(content="thyca summary", model="m", usage=usage)
        return ChatReply(content=None, model="m", tool_calls=[
            ToolCall(id="t-delegate", name="bot_delegate",
                     arguments={"bot_id": "coder", "task": "say hi to the user"}),
        ])


def _rows(app: ChatApp, session_id: str) -> list[dict]:
    return app.get_payload(session_id)["messages"]


def _bot_rows(rows: list[dict], bot_id: str) -> list[dict]:
    return [r for r in rows if (r.get("meta") or {}).get("bot_id") == bot_id]


def _dm_flow(tmp_path: Path, *, greet: bool):
    app = _chat(tmp_path, _DmFlowLLM(greet=greet))
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        thyca_dm = app.bot_dm("thyca")["id"]
        detail = app.turn(thyca_dm, "nhờ coder nhắn riêng chào mình")
        return app, thyca_dm, detail
    except Exception:
        app.shutdown()
        raise


def test_delegate_from_thyca_dm_records_no_other_bot_rows(tmp_path: Path) -> None:
    app, thyca_dm, detail = _dm_flow(tmp_path, greet=False)
    try:
        assert detail["reply"] == "thyca summary"
        rows = _rows(app, thyca_dm)
        assert _bot_rows(rows, "coder") == [], (
            "a fixed DM must never show another bot speaking in it")
        kinds = {(r.get("meta") or {}).get("kind") for r in rows}
        assert "delegate" not in kinds and "result" not in kinds
        # The recipient really ran — privately, in its internal session.
        internal = app._runtime.internal_session("coder", thyca_dm)
        msgs = app._runtime.store_for("coder").load(internal.id).messages
        assert any("say hi to the user" in (m.content or "") for m in msgs)
    finally:
        app.shutdown()


def test_message_user_lands_in_coders_own_dm_only(tmp_path: Path) -> None:
    app, thyca_dm, detail = _dm_flow(tmp_path, greet=True)
    try:
        assert detail["reply"] == "thyca summary"
        coder_dm = app.bot_dm("coder")["id"]
        dm_rows = _rows(app, coder_dm)
        greetings = [r for r in _bot_rows(dm_rows, "coder")
                     if _GREETING in (r.get("content") or "")]
        assert greetings, "message_user must post into the bot's own DM"
        thyca_rows = _rows(app, thyca_dm)
        assert _bot_rows(thyca_rows, "coder") == []
        assert all(_GREETING not in (r.get("content") or "") for r in thyca_rows)
    finally:
        app.shutdown()


class _DirectDmLLM:
    """Coder, already in its own DM, still tries message_user."""

    async def chat(self, messages, tools=None):
        tool_text = "\n".join(
            str(m.content or "") for m in messages if m.role == "tool"
        )
        usage = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
        if tool_text:
            assert "already in your private DM" in tool_text
            return ChatReply(content="coder direct reply", model="m-coder",
                             usage=usage)
        return ChatReply(content=None, model="m-coder", tool_calls=[
            ToolCall(id="c-dm", name="message_user",
                     arguments={"message": "DOUBLE-GREETING"}),
        ])


def test_message_user_inside_own_dm_does_not_double_post(tmp_path: Path) -> None:
    app = _chat(tmp_path, _DirectDmLLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        coder_dm = app.bot_dm("coder")["id"]
        detail = app.turn(coder_dm, "chào coder")
        assert detail["reply"] == "coder direct reply"
        rows = _rows(app, coder_dm)
        assert all("DOUBLE-GREETING" not in (r.get("content") or "") for r in rows)
    finally:
        app.shutdown()
