from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard, BotModel
from thyca.bots.context import current_bot_turn_context
from thyca.config import ModelCfg, ProviderEntry, default_config, load, save
from seeded_config import seeded_config
from thyca.core.protocol import Message, ToolCall
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.sessions import SessionManager
from thyca.serve.trace import turns_from_session


class RecordingLLM:
    def __init__(self, reply: ChatReply | None = None) -> None:
        self.reply = reply or ChatReply(content="ok", model="test-model")
        self.requests: list[list[Message]] = []
        self.schemas: list[list[dict] | None] = []
        self.views: list[tuple[str | None, str | None, str | None, str | None]] = []
        self.hook = None

    def set_retry_hook(self, hook) -> None:
        self.hook = hook

    async def chat(self, messages, tools=None) -> ChatReply:
        ctx = current_bot_turn_context()
        view = None
        from thyca.memory.visibility import current_memory_view

        current = current_memory_view()
        if current is not None:
            view = (
                current.bot_id,
                current.window.window_id if current.window else None,
                current.window.visibility if current.window else None,
            )
        self.views.append((ctx.sender_id if ctx else None, *(view or (None, None, None))))
        self.requests.append(list(messages))
        self.schemas.append(tools)
        return self.reply

    async def aclose(self) -> None:
        return None


def _app(
    root: Path, llm: RecordingLLM, *, context_tokens: int | None = None
) -> ChatApp:
    cfg = seeded_config()
    if context_tokens is not None:
        cfg = replace(cfg, limits=replace(cfg.limits, contextTokens=context_tokens))
    save(cfg, root / "config.json")
    return ChatApp(root, load(root / "config.json"), connect=llm)


def _coder(app: ChatApp) -> None:
    app._bots.create(
        BotCard(
            bot_id="coder",
            name="coder",
            role="Coder",
            description="Writes code.",
            persona="",
        )
    )


def _rows(app: ChatApp, bot_id: str, room_id: str) -> list[Message]:
    return app._runtime.internal_session(bot_id, room_id).messages


def _append_long_canonical_history(app: ChatApp, bot_id: str, room_id: str) -> None:
    room = app._sessions.store.load(room_id)
    window = app._webui.window_for(room)
    internal = app._runtime.internal_session(bot_id, room_id)
    store = app._runtime._canonical_store(bot_id, window, internal.id)
    manager = SessionManager(store=store)
    manager.load(internal.id)
    for index in range(4):
        manager.append(Message(role="user", content=f"old user {index} " + "u" * 1200))
        manager.append(Message(role="assistant", content=f"old assistant {index} " + "a" * 1200))


def test_bot_and_operation_model_precedence_reaches_chat_and_compaction(
    tmp_path: Path, monkeypatch
) -> None:
    from thyca.app.chat_app import ConnectFactory
    from thyca.config import PricingCfg

    cfg = replace(
        default_config(),
        providers={
            "default": ProviderEntry(
                baseUrl="https://global.invalid/v1", apiKey="global-key"
            ),
            "card-provider": ProviderEntry(
                baseUrl="https://card.invalid/v1", apiKey="card-key"
            ),
            "override-provider": ProviderEntry(
                baseUrl="https://override.invalid/v1", apiKey="override-key"
            ),
        },
        defaultProvider="default",
        defaultModel="global-model",
        models={
            "global-model": ModelCfg(contextTokens=8192, hotTailKB=2),
            "card-model": ModelCfg(
                provider="card-provider", contextTokens=16384, hotTailKB=3,
                input=1.0, cache=0.5, output=2.0,
            ),
            "override-model": ModelCfg(
                provider="override-provider", contextTokens=32768, hotTailKB=4,
                input=3.0, cache=1.5, output=4.0,
            ),
        },
    )
    save(cfg, tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"))
    captured_providers = []
    captured_builds = []
    captured_compactions = []
    original_build = __import__(
        "thyca.app.toolchain", fromlist=["build_agent_loop"]
    ).build_agent_loop

    def create(api, provider):
        captured_providers.append((api, provider))
        return RecordingLLM(ChatReply(content="ok", model=provider.model))

    def build_agent_loop(**kwargs):
        captured_builds.append({
            "model": kwargs["model"],
            "context_tokens": kwargs["context_tokens"],
            "tail_kb": kwargs["sessions"].limits.hotTailKB,
            "pricing": kwargs["pricing"],
        })
        return original_build(**kwargs)

    async def compact_stub(summarizer, sessions, **kwargs):
        captured_compactions.append({
            "model": kwargs["model"],
            "context_tokens": sessions.limits.contextTokens,
            "tail_kb": sessions.limits.hotTailKB,
            "pricing": kwargs["pricing"],
        })
        return {"compacted": False, "summary": None}

    async def no_flush(*args, **kwargs):
        return None

    monkeypatch.setattr(ConnectFactory, "create", create)
    monkeypatch.setattr("thyca.app.toolchain.build_agent_loop", build_agent_loop)
    monkeypatch.setattr("thyca.app.session_admin.service.compact_with_llm", compact_stub)
    app._background_jobs._flush_bot_memory = no_flush
    try:
        app._bots.create(
            BotCard(
                bot_id="coder",
                name="coder",
                role="Coder",
                description="Writes code.",
                persona="",
                model=BotModel(id="card-model"),
            )
        )
        group = app.create(bots=["thyca", "coder"])["id"]
        app.turn(group, "@coder card model")
        app.turn(group, "@thyca global default")
        app.turn(group, "@coder explicit model", model="override-model")

        assert [provider.model for _api, provider in captured_providers[:3]] == [
            "card-model", "global-model", "override-model"
        ]
        assert [provider.baseUrl for _api, provider in captured_providers[:3]] == [
            "https://card.invalid/v1",
            "https://global.invalid/v1",
            "https://override.invalid/v1",
        ]
        assert [provider.api_key() for _api, provider in captured_providers[:3]] == [
            "card-key", "global-key", "override-key"
        ]
        assert [item["context_tokens"] for item in captured_builds[:3]] == [
            16384, 8192, 32768
        ]
        assert [item["tail_kb"] for item in captured_builds[:3]] == [3, 2, 4]
        assert captured_builds[0]["pricing"]["card-model"] == PricingCfg(
            input=1.0, cache=0.5, output=2.0
        )
        assert captured_builds[2]["pricing"]["override-model"] == PricingCfg(
            input=3.0, cache=1.5, output=4.0
        )

        captured_providers.clear()
        captured_compactions.clear()
        app.compact_session(group)
        assert [provider.model for _api, provider in captured_providers] == [
            "global-model", "card-model"
        ]
        assert [item["model"] for item in captured_compactions] == [
            "global-model", "card-model"
        ]
        assert [item["context_tokens"] for item in captured_compactions] == [
            8192, 16384
        ]
        assert [item["tail_kb"] for item in captured_compactions] == [2, 3]

        captured_providers.clear()
        captured_compactions.clear()
        app.compact_session(group, model="override-model")
        assert [provider.model for _api, provider in captured_providers] == [
            "override-model", "override-model"
        ]
        assert [item["model"] for item in captured_compactions] == [
            "override-model", "override-model"
        ]
        assert [item["context_tokens"] for item in captured_compactions] == [
            32768, 32768
        ]
        assert [item["tail_kb"] for item in captured_compactions] == [4, 4]
    finally:
        app.shutdown()


def test_workspace_hot_composer_shares_prepared_inputs_and_memory_gate(
    tmp_path: Path,
) -> None:
    from thyca.bots.workspace_context import build_workspace_hot
    from thyca.core.channel import WindowRef
    from thyca.memory.active import ActiveSnapshot
    from thyca.bots.card import BotMemoryPolicy

    window = WindowRef(
        window_id="room", channel_id="webui", kind="dm", participants=("coder",)
    )
    card = BotCard(
        bot_id="coder", name="coder", role="coder", description="",
        memory=BotMemoryPolicy(read=True),
    )
    hot = ActiveSnapshot(soul="s", user="u", today="today")
    inputs = {
        "thyca_dir": tmp_path,
        "bot_id": "coder",
        "window": window,
        "card": card,
        "runtime_tasks": (),
        "memory_catalog": (),
        "recalled": (),
        "room_delta": "room text",
        "bot_user": "bot profile",
        "skills": "skills",
        "team": "team",
    }
    runtime_hot = build_workspace_hot(hot, **inputs)
    meter_hot = build_workspace_hot(hot, **inputs)
    assert runtime_hot == meter_hot

    blind = replace(card, memory=BotMemoryPolicy(read=False))
    blind_hot = build_workspace_hot(
        hot,
        **{**inputs, "card": blind, "bot_user": "must not show"},
    )
    assert blind_hot.today == ""
    assert blind_hot.bot_user == ""
    assert blind_hot.recalled == ""


def test_root_and_child_use_private_canonical_runtime_and_admin_policy(tmp_path: Path) -> None:
    class AdminLLM(RecordingLLM):
        calls: dict[str, int]

        def __init__(self) -> None:
            super().__init__()
            self.calls = {"thyca": 0, "coder": 0}

        async def chat(self, messages, tools=None) -> ChatReply:
            ctx = current_bot_turn_context()
            bot_id = ctx.sender_id if ctx else "thyca"
            self.calls[bot_id] += 1
            from thyca.memory.visibility import current_memory_view

            view = current_memory_view()
            self.views.append((
                bot_id,
                view.bot_id if view else None,
                view.window.window_id if view and view.window else None,
                view.window.visibility if view and view.window else None,
            ))
            names = {item["function"]["name"] for item in (tools or [])}
            self.schemas.append(tools)
            self.requests.append(list(messages))
            if self.calls[bot_id] == 1:
                assert ("bot_create" in names) is (bot_id == "thyca")
                return ChatReply(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=f"create-{bot_id}",
                            name="bot_create",
                            arguments={
                                "bot_id": f"created-{bot_id}",
                                "description": "Created by a test.",
                            },
                        )
                    ],
                    model="test-model",
                    usage={"prompt_tokens": 2, "completion_tokens": 1},
                )
            return ChatReply(
                content=f"{bot_id} done",
                model="test-model",
                usage={"prompt_tokens": 3, "completion_tokens": 1},
            )

    llm = AdminLLM()
    app = _app(tmp_path, llm)
    try:
        _coder(app)
        root_dm = app.bot_dm("thyca")["id"]
        child_dm = app.bot_dm("coder")["id"]
        root_result = app.turn(root_dm, "create a bot")
        child_result = app.turn(child_dm, "try the admin tool")

        assert app._bots.get("created-thyca").ok
        assert not app._bots.get("created-coder").ok
        assert all(
            "bot_create" in {item["function"]["name"] for item in schema or []}
            for schema in (llm.schemas[0], llm.schemas[1])
        )
        assert "bot_create" not in {
            item["function"]["name"] for item in llm.schemas[2] or []
        }
        assert "bot_create" not in {
            item["function"]["name"] for item in llm.schemas[3] or []
        }
        assert root_result["reply"] == "thyca done"
        assert child_result["reply"] == "coder done"
        root_meta = root_result["messages"][-1]["meta"]
        child_meta = child_result["messages"][-1]["meta"]
        assert root_meta["requests"] == root_meta["rounds"] == 2
        assert root_meta["tool_call_count"] == 1
        assert root_meta["usage"] == {
            "prompt_tokens": 5,
            "completion_tokens": 2,
        }
        assert child_meta["requests"] == child_meta["rounds"] == 2
        assert child_meta["tool_call_count"] == 1
        root_trace = turns_from_session(app._sessions.store.load(root_dm))[-1]
        assert root_trace.rounds == 2
        assert root_trace.requests == 2
        assert root_trace.tool_calls == 1
        assert root_trace.prompt_tokens == 5
        assert ("thyca", "thyca", root_dm, "private") in llm.views
        assert ("coder", "coder", child_dm, "private") in llm.views

        for bot_id, room_id in (("thyca", root_dm), ("coder", child_dm)):
            public = app.get_payload(room_id)["messages"]
            assert [row["role"] for row in public] == ["user", "assistant"]
            assert public[-1]["meta"]["kind"] == "message"
            assert not any(row.get("tool_calls") for row in public)
            assert "bot_create" not in str(public)
            assert f"created-{bot_id}" not in str(public)
            canonical = _rows(app, bot_id, room_id)
            assert any(row.role == "tool" for row in canonical)
            assert any(
                row.role == "assistant" and (row.meta or {}).get("kind") == "llm"
                for row in canonical
            )
        assert not list((tmp_path / "bots").glob("*/sessions/*.jsonl"))
    finally:
        app.shutdown()


class RetryLLM(RecordingLLM):
    def __init__(self) -> None:
        super().__init__()
        self.calls: dict[str, int] = {}

    async def chat(self, messages, tools=None) -> ChatReply:
        ctx = current_bot_turn_context()
        bot_id = ctx.sender_id if ctx else "thyca"
        self.calls[bot_id] = self.calls.get(bot_id, 0) + 1
        if self.hook is not None and self.calls[bot_id] == 1:
            self.hook(1, 2)
        self.requests.append(list(messages))
        self.schemas.append(tools)
        if self.calls[bot_id] == 1:
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"fetch-{bot_id}",
                        name="bot_fetch",
                        arguments={"span_id": "sp_missing"},
                    )
                ],
                model="test-model",
                usage={"prompt_tokens": 2},
            )
        if self.calls[bot_id] == 2:
            return ChatReply(content="initial", model="test-model")
        if self.calls[bot_id] == 3:
            raise RuntimeError("retry failure")
        return ChatReply(content="replayed", model="test-model")


def test_root_and_child_retry_truncate_both_transcripts_and_keep_mail(tmp_path: Path) -> None:
    llm = RetryLLM()
    app = _app(tmp_path, llm)
    try:
        _coder(app)
        room = app.create(bots=["thyca", "coder"])["id"]
        for bot_id in ("thyca", "coder"):
            events = []
            app.turn(room, f"@{bot_id} first", event_sink=events.append)
            old_cursor = app._runtime.room_cursor(bot_id, room)
            public_users_before_retry = sum(
                message.role == "user"
                for message in app._sessions.store.load(room).messages
            )
            assert any(event.type == "llm.retry" and event.bot_id == bot_id for event in events)
            sender = "coder" if bot_id == "thyca" else "thyca"
            app._runtime.send(
                sender=sender,
                recipient=bot_id,
                kind="message",
                session_id=room,
                payload=f"queued for {bot_id}",
            )
            with pytest.raises(RuntimeError, match="retry failure"):
                app.turn(room, retry=True)
            assert app._runtime._bus.pending(bot_id) == 1
            assert app._runtime.room_cursor(bot_id, room) == old_cursor
            public_after_failure = app._sessions.store.load(room).messages
            assert [row.role for row in public_after_failure].count("user") == public_users_before_retry
            public_last_user = next(
                message for message in reversed(public_after_failure)
                if message.role == "user"
            )
            assert (public_last_user.meta or {}).get("error", {}).get("code")
            internal_after_failure = _rows(app, bot_id, room)
            assert [row.role for row in internal_after_failure].count("user") == 1
            assert not [row for row in internal_after_failure if row.role in {"assistant", "tool"}]
            assert (internal_after_failure[-1].meta or {}).get("error", {}).get("code")

            result = app.turn(room, retry=True)
            assert result["reply"] == "replayed"
            assert app._runtime._bus.pending(bot_id) == 1
            public = app._sessions.store.load(room).messages
            internal = _rows(app, bot_id, room)
            assert [row.role for row in public].count("user") == public_users_before_retry
            assert [row.role for row in internal].count("user") == 1
            public_last_user = next(
                message for message in reversed(public) if message.role == "user"
            )
            assert not (public_last_user.meta or {}).get("error")
            assert not (internal[0].meta or {}).get("error")
            assert not [row for row in internal if row.role == "tool"]
            assert [row.content for row in internal if row.role == "assistant"] == ["replayed"]
    finally:
        app.shutdown()


def test_effort_and_model_override_reach_factory_before_connect(tmp_path: Path, monkeypatch) -> None:
    from thyca.llm.llm_factory import ConnectFactory
    from seeded_config import seeded_config

    cfg = replace(
        seeded_config(),
        models={"custom-model": ModelCfg(reasoningEfforts=("low", "custom"))},
    )
    save(cfg, tmp_path / "config.json")
    captured = []
    llm = RecordingLLM(ChatReply(content="done", model="custom-model"))

    def create(api, provider):
        captured.append((api, provider))
        return llm

    monkeypatch.setattr(ConnectFactory, "create", create)
    app = ChatApp(tmp_path, load(tmp_path / "config.json"))
    try:
        room = app.bot_dm("thyca")["id"]
        result = app.turn(room, "use custom", model="custom-model", effort="custom")
        assert result["reply"] == "done"
        assert captured[0][1].model == "custom-model"
        assert captured[0][1].reasoningEffort == "custom"
        assert captured[0][1].reasoningEfforts == ("low", "custom")
    finally:
        app.shutdown()


def test_multi_bot_compact_preserves_room_and_reports_absent_and_failed_contexts(
    tmp_path: Path, monkeypatch
) -> None:
    llm = RecordingLLM(ChatReply(content="summary", model="test-model"))
    app = _app(tmp_path, llm, context_tokens=4096)
    try:
        _coder(app)
        group = app.create(bots=["thyca", "coder"])["id"]
        app.turn(group, "@thyca root history")
        _append_long_canonical_history(app, "thyca", group)
        room_before = list(app._sessions.store.load(group).messages)
        original_flush = app._background_jobs._flush_bot_memory

        async def no_flush(*args, **kwargs):
            return None

        app._background_jobs._flush_bot_memory = no_flush
        result = app.compact_session(group)
        assert result["compaction"]["compacted"] is True, result["compaction"]
        assert result["compaction"]["per_bot"]["thyca"]["compacted"] is True
        assert result["compaction"]["per_bot"]["coder"] == {
            "compacted": False,
            "skipped": "no_existing_context",
        }
        assert app._runtime.workspaces.store.get_binding("coder", group) is None
        assert app._sessions.store.load(group).messages == room_before

        app.turn(group, "@coder child history")
        _append_long_canonical_history(app, "coder", group)
        _append_long_canonical_history(app, "thyca", group)
        room_before = list(app._sessions.store.load(group).messages)
        root_before = list(_rows(app, "thyca", group))
        child_before = list(_rows(app, "coder", group))
        result = app.compact_session(group)
        assert result["compaction"]["per_bot"]["thyca"]["compacted"] is True
        assert result["compaction"]["per_bot"]["coder"]["compacted"] is True
        assert _rows(app, "thyca", group) != root_before
        assert _rows(app, "coder", group) != child_before
        assert app._sessions.store.load(group).messages == room_before

        _append_long_canonical_history(app, "thyca", group)
        _append_long_canonical_history(app, "coder", group)
        room_before = list(app._sessions.store.load(group).messages)
        original_compact = app._sessions_admin._compact_async

        async def fail_thyca(sessions, room_id, turn_cfg, instructions, *, bot_id):
            if bot_id == "thyca":
                raise RuntimeError("compaction failed")
            return await original_compact(
                sessions, room_id, turn_cfg, instructions, bot_id=bot_id
            )

        app._sessions_admin._compact_async = fail_thyca
        result = app.compact_session(group)
        assert result["compaction"]["compacted"] is True
        assert result["compaction"]["per_bot"]["thyca"]["error"] == "RuntimeError"
        assert result["compaction"]["per_bot"]["coder"]["compacted"] is True
        assert app._sessions.store.load(group).messages == room_before
        app._background_jobs._flush_bot_memory = original_flush
    finally:
        app.shutdown()


def test_context_limit_status_survives_aggregate_room_row(tmp_path: Path, monkeypatch) -> None:
    from thyca.core.protocol import Message

    class ContextLimitLoop:
        def __init__(self, sessions) -> None:
            self.sessions = sessions

        async def run(self, text, *, bot_ctx, persist_user=True, persisted_user_msg=None, **kwargs):
            self.sessions.append(
                Message(
                    role="assistant",
                    content="context limit reached",
                    meta={
                        "kind": "llm",
                        "status": "context_limit",
                        "bot_id": bot_ctx["bot_id"],
                        "span_id": bot_ctx["span_id"],
                        "trace_id": bot_ctx["trace_id"],
                    },
                )
            )
            return "context limit reached"

    def build_agent_loop(*, sessions, **kwargs):
        return ContextLimitLoop(sessions)

    monkeypatch.setattr("thyca.app.toolchain.build_agent_loop", build_agent_loop)
    app = _app(tmp_path, RecordingLLM())
    try:
        room = app.bot_dm("thyca")["id"]
        result = app.turn(room, "large request")
        aggregate = result["messages"][-1]
        assert aggregate["meta"]["status"] == "context_limit"
        assert aggregate["meta"]["rounds"] == 0
        trace = turns_from_session(app._sessions.store.load(room))[-1]
        assert trace.status == "context_limit"
        assert trace.rounds == 0
        assert trace.requests == 0
    finally:
        app.shutdown()


def test_compaction_does_not_pollute_next_turn_usage_or_trace(tmp_path: Path) -> None:
    llm = RecordingLLM(
        ChatReply(
            content="turn reply",
            model="test-model",
            usage={"prompt_tokens": 11, "completion_tokens": 2},
        )
    )
    # 8500 (was 8192): the baseline tool set (incl. the compact tool)
    # counts as prompt overhead in the backstop estimate.
    app = _app(tmp_path, llm, context_tokens=8500)
    try:
        group = app.create(bots=["thyca"])["id"]
        app.turn(group, "first")
        _append_long_canonical_history(app, "thyca", group)
        async def no_flush(*args, **kwargs):
            return None
        app._background_jobs._flush_bot_memory = no_flush
        app.compact_session(group)
        result = app.turn(group, "after compact")
        row = next(
            row for row in reversed(result["messages"])
            if row["role"] == "assistant" and row.get("meta", {}).get("kind") == "message"
        )
        assert row["meta"]["usage"] == {"prompt_tokens": 11, "completion_tokens": 2}
        assert row["meta"]["requests"] == 1
        assert row["meta"]["rounds"] == 1
        trace = turns_from_session(app._sessions.store.load(group))
        assert trace[-1].rounds == 1
        assert trace[-1].requests == 1
        assert trace[-1].prompt_tokens == 11
    finally:
        app.shutdown()
