"""Metric integration: canonical scope, recorded prices, and context config."""
from dataclasses import replace

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard, BotModel
from thyca.config import ModelCfg, save
from seeded_config import seeded_config
from thyca.core.protocol import Message
from thyca.sessions.compaction import estimate_tokens


class NoCalls:
    async def chat(self, *args, **kwargs):
        raise AssertionError("reading operations must never call a model")


@pytest.fixture
def app(tmp_path):
    cfg = replace(seeded_config(), defaultModel="unknown-unpriced", models={
        "card-model": ModelCfg(contextTokens=4096, input=1, cache=0.5, output=2),
        "override-model": ModelCfg(contextTokens=8192, input=3, cache=1, output=4),
    })
    save(cfg, tmp_path / "config.json")
    instance = ChatApp(tmp_path, cfg, connect=NoCalls())
    instance._bots.create(BotCard(
        bot_id="coder", name="Coder", role="Coder", description="Writes code.",
        model=BotModel(id="card-model"),
    ))
    yield instance
    instance.shutdown()


def call(span, cost, *, tokens=11, status=None):
    return Message(role="assistant", content="not exposed", meta={
        "kind": "llm", "bot_id": "coder", "span_id": span,
        "trace_id": "tr-test", "model": "observed-old-model",
        "usage": {"prompt_tokens": tokens, "cached_tokens": 2,
                  "completion_tokens": 3, "total_tokens": tokens + 3},
        "cost_usd": cost, "status": status,
    })


def seed(app, room, messages):
    internal = app._runtime.internal_session("coder", room["id"])
    window = app._webui.window_for(app._sessions.store.load(room["id"]))
    store = app._runtime._canonical_store("coder", window, internal.id)
    for message in messages:
        store.append(store.path_for(internal.id), message)
    return internal.id, store


def test_operations_use_only_original_window_events_and_recorded_cost(app):
    room = app.create(["thyca", "coder"])
    sid, store = seed(app, room, [
        call("one", 0.01), call("two", 0.02),
        call("two", 0.02, status="loop_limit"),
    ])
    # Public aggregate references the execution, but must NOT add another cost.
    path = app._sessions.store.load(room["id"]).path
    app._sessions.store.append(path, Message(role="user", content="question"))
    app._sessions.store.append(path, Message(role="assistant", content="reply", meta={
        "kind": "message", "bot_id": "coder", "span_id": "two",
        "cost_usd": 100, "requests": 100,
    }))
    # Persist the public turn before opening another DM: existing session
    # lifecycle deliberately prunes older empty public groups on creation.
    private = app.bot_dm("coder")
    seed(app, private, [call("private", 900, tokens=99999)])
    basis = [Message(role="system", content="[compaction: short summary]")]
    store.rewrite(sid, store.path_for(sid), basis)
    # Stub the meter at its canonical home (the read-model service).
    app._ops._overhead_tokens = lambda *args, **kwargs: (100, 50)
    result = app.bot_operations(room["id"], "coder")
    assert result["conversation"]["requests"] == 2
    assert result["conversation"]["cost_usd"] == 0.03
    # Participant metadata may change without creating another room. All
    # original requests in that same room must retain their recorded cost.
    from thyca.bots.timeline import WorkspaceTimeline
    from thyca.core.channel import WindowRef
    changed_members = WindowRef(
        window_id=room["id"], channel_id="webui", kind="group",
        participants=("thyca", "coder", "researcher"),
    )
    history = WorkspaceTimeline(app._root, "coder", create=False).messages_for_window(changed_members)
    assert [message.meta["span_id"] for message in history] == ["one", "two", "two"]
    assert result["conversation"]["usage"]["prompt_tokens"] == 22
    assert result["last_turn"]["span_id"] == "two"
    assert result["last_turn"]["cost_usd"] == 0.02
    assert result["last_turn"]["model"] == "observed-old-model"
    assert result["trace"] == {"session_id": room["id"], "turn_index": 0}
    assert result["model"]["id"] == "card-model"
    assert result["context"]["context_tokens"] == 4096
    assert result["context"]["transcript_tokens"] == sum(map(estimate_tokens, basis))
    assert result["context"]["overhead_tokens"] == 150
    assert result["context"]["estimated"] is True
    assert result["rates"] == {
        "currency": "USD", "per_tokens": 1_000_000,
        "input": 1, "output": 2, "cache": 0.5,
    }
    assert "private" not in str(result)
    # A selected model changes context and unit rates, never recorded past cost.
    selected = app.bot_operations(room["id"], "coder", model="override-model", effort="low")
    assert selected["context"]["context_tokens"] == 8192
    assert selected["model"]["reasoning_effort"] == "low"
    assert selected["rates"]["input"] == 3
    assert selected["conversation"]["cost_usd"] == 0.03


def test_unpriced_cost_is_partial_not_zero_or_known_subtotal(app):
    room = app.create(["thyca", "coder"])
    seed(app, room, [call("one", 0.01), call("two", None)])
    app._ops._overhead_tokens = lambda *args, **kwargs: (0, 0)
    result = app.bot_operations(room["id"], "coder")
    assert result["conversation"]["cost_usd"] is None
    assert result["conversation"]["known_cost_usd"] == 0.01
    assert result["conversation"]["unpriced_requests"] == 1
    assert result["last_turn"]["cost_usd"] is None


def test_no_binding_is_read_only_and_unknown_rates_remain_unknown(app):
    room = app.create(["thyca", "coder"])
    before = {str(path) for path in app._root.rglob("*")}
    result = app.bot_operations(room["id"], "thyca")
    assert result["rates"] is None
    assert result["last_turn"] is None
    assert result["conversation"]["cost_usd"] is None
    assert result["context"]["transcript_tokens"] == 0
    assert result["context"]["overhead_tokens"] > 0
    assert result["model"]["scope"] == "next_turn_configuration"
    assert app._runtime.workspaces.store.get_binding("thyca", room["id"]) is None
    assert {str(path) for path in app._root.rglob("*")} == before


def test_measurement_failure_is_not_a_plausible_zero_context(app):
    room = app.create(["thyca", "coder"])
    def broken_peek(*args, **kwargs):
        raise OSError("cannot read memory")
    app._memory.peek = broken_peek
    with pytest.raises(OSError, match="cannot read memory"):
        app.bot_operations(room["id"], "coder")
