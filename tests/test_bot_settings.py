"""Per-bot model settings: settings.json ownership, registry hydration,
dedicated HTTP API, and runtime provider resolution (fake LLMs only)."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.admin_tools import apply_card_edit
from thyca.bots.card import BotCard, BotModel
from thyca.bots.registry import BotRegistry, RegistryError
from thyca.bots.settings import (
    SettingsError,
    read_settings,
    strip_card_model_text,
    write_settings,
)
from thyca.config import ModelCfg, ProviderEntry, default_config, load, save
from thyca.llm.llm_base import ChatReply
from thyca.memory.facade import MemoryFacade
from thyca.serve import default_webui, make_server


LEGACY_TWIN = """\
---
name: coder
role: Coder
description: Writes code.
tint: accent
version: 1
model:
  id: legacy-model
  thinking: legacy-effort
custom_note: keep me
bash: ask
memory:
  read: true
  write: true
schedule: null
---

You write code.
Second persona line.
"""


def _registry(root: Path) -> BotRegistry:
    reg = BotRegistry(root)
    reg.ensure_thyca()
    assert (root / "bots" / "thyca" / "settings.json").is_file()
    return reg


def _legacy_bot(root: Path, bot_id: str = "coder") -> BotRegistry:
    reg = _registry(root)
    dest = root / "bots" / bot_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "TWIN.md").write_text(LEGACY_TWIN, encoding="utf-8")
    return reg


# --- persistence & hydration --------------------------------------------


def test_create_persists_settings_and_strips_twin(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    created = reg.create(BotCard(
        bot_id="coder", name="coder", role="Coder",
        description="Writes code.", persona="You write code.",
        model=BotModel(id="m1", thinking="high"),
    ))
    assert created.model == BotModel(id="m1", thinking="high")
    payload = json.loads((tmp_path / "bots" / "coder" / "settings.json").read_text())
    assert payload == {"model": {"id": "m1", "thinking": "high"}}
    twin = (tmp_path / "bots" / "coder" / "TWIN.md").read_text()
    assert "model:" not in twin.split("---")[1]
    assert "You write code." in twin


def test_legacy_read_falls_back_with_zero_writes(tmp_path: Path) -> None:
    reg = _legacy_bot(tmp_path)
    before = (tmp_path / "bots" / "coder" / "TWIN.md").read_bytes()
    doc = reg.get("coder")
    assert doc.ok and doc.card is not None
    assert doc.card.model == BotModel(id="legacy-model", thinking="legacy-effort")
    assert not (tmp_path / "bots" / "coder" / "settings.json").exists()
    assert (tmp_path / "bots" / "coder" / "TWIN.md").read_bytes() == before
    assert (tmp_path / "bots" / "coder" / "custom.lock").exists() is False


def test_legacy_save_strips_only_model_preserving_persona(tmp_path: Path) -> None:
    reg = _legacy_bot(tmp_path)
    updated = reg.update_settings("coder", BotModel(id="m2", thinking=None))
    assert updated.model == BotModel(id="m2", thinking=None)
    payload = json.loads((tmp_path / "bots" / "coder" / "settings.json").read_text())
    assert payload == {"model": {"id": "m2", "thinking": None}}
    twin = (tmp_path / "bots" / "coder" / "TWIN.md").read_text()
    assert "custom_note: keep me" in twin
    assert "You write code.\nSecond persona line.\n" in twin
    assert "legacy-model" not in twin
    assert twin.count("---") == 2


def test_settings_precedence_over_conflicting_legacy(tmp_path: Path) -> None:
    import yaml

    reg = _legacy_bot(tmp_path)
    reg.update_settings("coder", BotModel(id="settings-model", thinking="low"))
    # Re-insert a REAL conflicting frontmatter model block (the strip had
    # removed the legacy key, so a plain text replace would be a no-op).
    twin_path = tmp_path / "bots" / "coder" / "TWIN.md"
    text = twin_path.read_text(encoding="utf-8")
    assert "\nmodel:" not in text.split("---")[1]
    head, body = text.split("---\n", 1)[1].split("\n---", 1)
    front = yaml.safe_load(head) or {}
    assert "model" not in front
    front = {"model": {"id": "legacy-model", "thinking": "high"}, **front}
    twin_path.write_text(
        "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False)
        + "---" + body,
        encoding="utf-8",
    )
    assert "legacy-model" in twin_path.read_text(encoding="utf-8")
    doc = reg.get("coder")
    assert doc.ok and doc.card is not None
    assert doc.card.model == BotModel(id="settings-model", thinking="low")


def test_missing_settings_falls_back_to_legacy_model(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    dest = tmp_path / "bots" / "plain"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "TWIN.md").write_text(
        LEGACY_TWIN.replace("legacy-model", "default").replace(
            "  thinking: legacy-effort\n", ""),
        encoding="utf-8",
    )
    doc = reg.get("plain")
    assert doc.ok and doc.card is not None
    assert doc.card.model == BotModel(id="default", thinking=None)
    assert not (dest / "settings.json").exists()


def test_corrupt_settings_marks_invalid_without_fallback(tmp_path: Path) -> None:
    reg = _legacy_bot(tmp_path)
    path = tmp_path / "bots" / "coder" / "settings.json"
    for bad in (
        "{not json",
        "[1, 2]",
        '{"model": {"id": ""}}',
        '{"model": {"id": "x", "thinking": ""}}',
        '{"model": {"id": "x", "extra": 1}}',
        '{"other": 1}',
        '{"model": "x"}',
        '{"model": {"thinking": "low"}}',
    ):
        path.write_text(bad, encoding="utf-8")
        doc = reg.get("coder")
        assert not doc.ok and doc.card is None, bad
        with pytest.raises(RegistryError):
            reg.require("coder")


def test_atomic_failure_retains_original(tmp_path: Path, monkeypatch) -> None:
    reg = _legacy_bot(tmp_path)
    reg.update_settings("coder", BotModel(id="good", thinking=None))
    settings_path = tmp_path / "bots" / "coder" / "settings.json"
    twin_path = tmp_path / "bots" / "coder" / "TWIN.md"
    before_settings = settings_path.read_bytes()
    before_twin = twin_path.read_bytes()

    def _boom(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr("thyca.bots.settings.atomic_write_text", _boom)
    # Settings write itself fails: nothing changes, legacy TWIN untouched.
    with pytest.raises((RegistryError, SettingsError, OSError)):
        reg.update_settings("coder", BotModel(id="bad", thinking=None))
    assert settings_path.read_bytes() == before_settings
    assert twin_path.read_bytes() == before_twin
    assert reg.get("coder").card is not None
    assert reg.get("coder").card.model == BotModel(id="good", thinking=None)


def test_mode_and_traversal_and_symlink_rejection(tmp_path: Path) -> None:
    reg = _legacy_bot(tmp_path)
    reg.update_settings("coder", BotModel(id="m", thinking=None))
    mode = (tmp_path / "bots" / "coder" / "settings.json").stat().st_mode & 0o777
    assert mode == 0o600
    for bad_id in ("../x", "A", "", "x" * 65, "a/b", "."):
        with pytest.raises((RegistryError, SettingsError)):
            reg.update_settings(bad_id, BotModel(id="m"))
        assert not reg.get(bad_id).ok
    # Symlinked bot dir is rejected, never followed.
    real = tmp_path / "realdir"
    real.mkdir()
    link = tmp_path / "bots" / "ghost"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported")
    with pytest.raises(SettingsError):
        read_settings(tmp_path, "ghost")
    with pytest.raises((RegistryError, SettingsError)):
        reg.update_settings("ghost", BotModel(id="m"))
    # Symlinked settings file is rejected.
    target = tmp_path / "bots" / "coder" / "settings.json"
    target.unlink()
    target.symlink_to(tmp_path / "bots" / "thyca" / "settings.json")
    with pytest.raises(SettingsError):
        read_settings(tmp_path, "coder")


def test_update_card_and_pause_retain_model(tmp_path: Path) -> None:
    reg = _legacy_bot(tmp_path)
    card = reg.require("coder")
    edited = apply_card_edit(card, {"role": "Senior Coder"})
    updated = reg.update_card("coder", edited)
    assert updated.role == "Senior Coder"
    assert updated.model == BotModel(id="legacy-model", thinking="legacy-effort")
    assert "model:" not in (tmp_path / "bots" / "coder" / "TWIN.md").read_text().split("---")[1]
    paused = reg.set_paused("coder", True)
    assert paused.paused is True
    assert paused.model == BotModel(id="legacy-model", thinking="legacy-effort")
    restored = reg.set_paused("coder", False)
    assert restored.paused is False
    assert restored.model == BotModel(id="legacy-model", thinking="legacy-effort")


def test_ensure_thyca_seeds_only_new_system_bot(tmp_path: Path) -> None:
    reg = BotRegistry(tmp_path)
    reg.ensure_thyca()
    assert (tmp_path / "bots" / "thyca" / "settings.json").is_file()
    (tmp_path / "bots" / "thyca" / "settings.json").unlink()
    reg.ensure_thyca()
    # Reads never migrate: a deleted settings file stays deleted.
    assert not (tmp_path / "bots" / "thyca" / "settings.json").exists()
    doc = reg.get("thyca")
    assert doc.ok and doc.card is not None
    # The canonical TWIN holds no model key, so with settings deleted the
    # legacy fallback is None (existing semantics preserved, no migration).
    assert doc.card.model is None


def test_strip_card_model_text_round_trip() -> None:
    stripped = strip_card_model_text(LEGACY_TWIN)
    assert "custom_note: keep me" in stripped
    assert "You write code.\nSecond persona line.\n" in stripped
    assert "model:" not in stripped.split("---")[1]
    with pytest.raises(SettingsError):
        strip_card_model_text("no frontmatter")


# --- runtime integration -------------------------------------------------


class _RecordingLLM:
    def __init__(self, reply: ChatReply | None = None) -> None:
        self.reply = reply or ChatReply(content="ok", model="test-model")
        self.requests: list = []

    async def chat(self, messages, tools=None):
        self.requests.append(list(messages))
        return self.reply

    async def aclose(self) -> None:
        return None


def _runtime_config() -> object:
    return replace(
        default_config(),
        providers={
            "default": ProviderEntry(baseUrl="https://global.invalid/v1", apiKey="k"),
            "card-provider": ProviderEntry(baseUrl="https://card.invalid/v1", apiKey="k"),
        },
        defaultModel="global-model",
        models={
            "global-model": ModelCfg(),
            "card-model": ModelCfg(
                provider="card-provider",
                reasoningEfforts=("low", "custom"),
            ),
        },
    )


def _runtime_app(root: Path, llm: _RecordingLLM, monkeypatch) -> tuple[ChatApp, list]:
    from thyca.app.chat_app import ConnectFactory

    save(_runtime_config(), root / "config.json")
    captured: list = []

    def create(api, provider):
        captured.append(provider)
        return llm

    monkeypatch.setattr(ConnectFactory, "create", create)
    app = ChatApp(root, load(root / "config.json"))
    return app, captured


def test_turn_uses_saved_model_and_effort(tmp_path: Path, monkeypatch) -> None:
    llm = _RecordingLLM()
    app, captured = _runtime_app(tmp_path, llm, monkeypatch)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="coder", role="Coder",
            description="Writes code.", persona="",
        ))
        app._bots.update_settings(
            "coder", BotModel(id="card-model", thinking="custom")
        )
        app.turn(app.bot_dm("coder")["id"], "hello coder")
        app.turn(app.bot_dm("thyca")["id"], "hello thyca")
        assert [p.model for p in captured] == ["card-model", "global-model"]
        assert [p.baseUrl for p in captured] == [
            "https://card.invalid/v1", "https://global.invalid/v1",
        ]
        assert [p.reasoningEffort for p in captured] == ["custom", "high"]
        assert captured[0].reasoningEfforts == ("low", "custom")
    finally:
        app.shutdown()


def test_explicit_override_wins_without_overwriting_settings(
    tmp_path: Path, monkeypatch,
) -> None:
    llm = _RecordingLLM()
    app, captured = _runtime_app(tmp_path, llm, monkeypatch)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="coder", role="Coder",
            description="Writes code.", persona="",
            model=BotModel(id="card-model", thinking="custom"),
        ))
        room = app.bot_dm("coder")["id"]
        app.turn(room, "override me", model="global-model", effort="max")
        assert captured[-1].model == "global-model"
        assert captured[-1].reasoningEffort == "max"
        settings = json.loads(
            (tmp_path / "bots" / "coder" / "settings.json").read_text()
        )
        assert settings == {"model": {"id": "card-model", "thinking": "custom"}}
        app.turn(room, "back to saved")
        assert captured[-1].model == "card-model"
        assert captured[-1].reasoningEffort == "custom"
    finally:
        app.shutdown()


def test_operations_and_compact_honor_saved_settings(
    tmp_path: Path, monkeypatch,
) -> None:
    from thyca.core.protocol import Message

    llm = _RecordingLLM(ChatReply(content="summary", model="card-model"))
    app, captured = _runtime_app(tmp_path, llm, monkeypatch)
    compact_models: list = []

    async def compact_stub(summarizer, sessions, **kwargs):
        compact_models.append(kwargs["model"])
        return {"compacted": False, "summary": None}

    monkeypatch.setattr("thyca.app.chat_app.compact_with_llm", compact_stub)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="coder", role="Coder",
            description="Writes code.", persona="",
        ))
        app._bots.update_settings(
            "coder", BotModel(id="card-model", thinking="low")
        )
        group = app.create(bots=["thyca", "coder"])["id"]
        app.turn(group, "@coder build it")
        ops = app.bot_operations(group, "coder")
        assert ops["model"]["id"] == "card-model"
        assert ops["model"]["provider"] == "card-provider"
        assert ops["model"]["reasoning_effort"] == "low"
        assert captured[-1].model == "card-model"
        assert captured[-1].reasoningEffort == "low"
        # Force a real compaction with a long canonical history.
        room = app._sessions.store.load(group)
        window = app._webui.window_for(room)
        internal = app._runtime.internal_session("coder", group)
        store = app._runtime._canonical_store("coder", window, internal.id)
        from thyca.sessions import SessionManager

        manager = SessionManager(store=store)
        manager.load(internal.id)
        for index in range(4):
            manager.append(Message(role="user", content=f"old user {index} " + "u" * 1200))
            manager.append(Message(role="assistant", content=f"old assistant {index} " + "a" * 1200))
        app.compact_session(group)
        assert "card-model" in compact_models
    finally:
        app.shutdown()


def test_a2a_recipient_uses_own_thinking(tmp_path: Path, monkeypatch) -> None:
    from thyca.bots.bus import new_span_id, new_trace_id
    from thyca.bots.context import BotTurnContext, BotTurnScope

    llm = _RecordingLLM()
    app, captured = _runtime_app(tmp_path, llm, monkeypatch)
    app._flush_bot_memory = lambda *a, **k: asyncio.sleep(0, result=None)
    try:
        app._bots.create(BotCard(
            bot_id="sender", name="sender", role="S",
            description="Sends.", persona="",
            model=BotModel(id="card-model", thinking="custom"),
        ))
        app._bots.create(BotCard(
            bot_id="coder", name="coder", role="Coder",
            description="Writes code.", persona="",
            model=BotModel(id="card-model", thinking="low"),
        ))
        group = app.create(bots=["sender", "coder"])["id"]
        envelope = app._runtime.send(
            sender="sender", recipient="coder", kind="delegate",
            session_id=group, payload="do the thing",
        )
        scope = BotTurnScope(
            store=app._sessions.store, act=app._act, tools=app._tools,
            memory=app._memory, state=app._state, zone=app._zone,
            injected_connect=None,
        )
        # No explicit effort: the recipient's own thinking wins, not the
        # sender's saved thinking.
        ctx = BotTurnContext(
            sender_id="sender", room_id=group, trace_id=new_trace_id(),
            span_id=new_span_id(), scope=scope,
            turn_cfg=app._current_cfg(),
        )
        asyncio.run(app._deliver_envelope(envelope, ctx))
        assert captured[-1].model == "card-model"
        assert captured[-1].reasoningEffort == "low"
        # An invalid explicit effort fails honestly instead of leaking.
        envelope2 = app._runtime.send(
            sender="sender", recipient="coder", kind="delegate",
            session_id=group, payload="do it fast",
        )
        ctx2 = BotTurnContext(
            sender_id="sender", room_id=group, trace_id=new_trace_id(),
            span_id=new_span_id(), scope=scope,
            turn_cfg=app._current_cfg(), effort_override="custom",
        )
        reply2 = asyncio.run(app._deliver_envelope(envelope2, ctx2))
        assert reply2 == "ok", reply2
        assert captured[-1].reasoningEffort == "custom"
    finally:
        app.shutdown()


# --- HTTP API ------------------------------------------------------------


class _IdleLLM:
    async def chat(self, messages, tools=None):
        return ChatReply(content="unused")


def _chat_app(root: Path) -> ChatApp:
    save(default_config(), root / "config.json")
    app = ChatApp(root, load(root / "config.json"), connect=_IdleLLM())
    app._bots.create(BotCard(
        bot_id="coder", name="coder", role="Coder",
        description="Writes code.", persona="",
    ))
    app._bots.create(BotCard(
        bot_id="designer", name="designer", role="Designer",
        description="Designs.", persona="",
    ))
    return app


def _serve(root: Path, app: ChatApp):
    server = make_server(
        host="127.0.0.1", port=0, webui=default_webui(),
        facade=MemoryFacade(root, timezone_name="Asia/Ho_Chi_Minh"), chat=app,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(server, path: str, method: str = "GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(
        f"http://127.0.0.1:{server.server_address[1]}{path}",
        data=data, method=method, headers=headers,
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _stop(server, thread, app: ChatApp) -> None:
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
    app.shutdown()


def test_http_get_post_persist_restart_and_isolation(tmp_path: Path) -> None:
    app = _chat_app(tmp_path)
    server, thread = _serve(tmp_path, app)
    try:
        status, body = _request(server, "/api/bots/coder/settings")
        assert status == 200
        assert body["bot_id"] == "coder"
        assert body["model"] == {"id": "default", "thinking": None}
        assert body["effective"]["model"] == "gpt-4o-mini"
        assert body["effective"]["provider"] == "default"

        status, body = _request(
            server, "/api/bots/coder/settings", "POST",
            {"model": {"id": "default", "thinking": "low"}},
        )
        assert status == 200
        assert body["model"] == {"id": "default", "thinking": "low"}
        assert body["effective"]["thinking"] == "low"

        status, body = _request(server, "/api/bots/designer/settings")
        assert status == 200 and body["model"] == {"id": "default", "thinking": None}

        status, body = _request(
            server, "/api/bots/nope/settings", "POST",
            {"model": {"id": "default"}},
        )
        assert status == 404 and body == {"error": "bot not found"}

        status, body = _request(server, "/api/bots/coder/settings", "PATCH", {"model": {"id": "default"}})
        assert status == 404 and body == {"error": "bot not found"}
        status, body = _request(server, "/api/bots/coder/settings", "DELETE")
        assert status == 404 and body == {"error": "bot not found"}
    finally:
        _stop(server, thread, app)
    # Restart with a fresh app on the same root: settings persisted.
    app2 = ChatApp(tmp_path, load(tmp_path / "config.json"), connect=_IdleLLM())
    try:
        assert app2.bot_settings("coder")["model"] == {"id": "default", "thinking": "low"}
        assert app2.bot_settings("designer")["model"] == {"id": "default", "thinking": None}
    finally:
        app2.shutdown()


def test_http_validation_errors_are_safe(tmp_path: Path) -> None:
    app = _chat_app(tmp_path)
    server, thread = _serve(tmp_path, app)
    try:
        for payload, error in (
            ({"model": {"id": "nope-model"}}, "unknown model"),
            ({"model": {"id": ""}}, "invalid model"),
            ({"model": {"id": "default", "thinking": ""}}, "invalid thinking"),
            ({"model": {"id": "default", "thinking": 3}}, "invalid thinking"),
            ({"model": {"id": "x", "bogus": 1}}, "invalid body"),
            ({"bogus": 1}, "invalid body"),
            ({"model": {"thinking": "low"}}, "invalid body"),
            ({"model": "default"}, "invalid body"),
            ([], "invalid body"),
        ):
            status, body = _request(server, "/api/bots/coder/settings", "POST", payload)
            assert status == 400, payload
            assert body == {"error": error}, (payload, body)
        # Invalid effort is a safe constant (no value/config echo).
        status, body = _request(
            server, "/api/bots/coder/settings", "POST",
            {"model": {"id": "default", "thinking": "ultra"}},
        )
        assert status == 400 and body == {"error": "invalid thinking"}, body
        # Unknown model id echoes no path/secret: exact constant only.
        status, body = _request(
            server, "/api/bots/coder/settings", "POST",
            {"model": {"id": "m" * 201}},
        )
        assert status == 400 and body == {"error": "invalid model"}
        # Unknown bot + traversal ids stay 404 with the constant.
        status, body = _request(server, "/api/bots/ghost/settings")
        assert status == 404 and body == {"error": "bot not found"}
        # Path traversal never reaches a bot dir (regex rejects it).
        raw = Request(f"http://127.0.0.1:{server.server_address[1]}/api/bots/../x/settings")
        try:
            with urlopen(raw, timeout=5) as response:
                raw_status = response.status
        except HTTPError as error:
            raw_status = error.code
        assert raw_status == 404, raw_status
    finally:
        _stop(server, thread, app)


def test_http_custom_effort_set_and_corrupt_is_503(tmp_path: Path) -> None:
    cfg = replace(
        default_config(),
        models={
            "custom-model": ModelCfg(
                provider="default", reasoningEfforts=("low", "custom")
            ),
        },
    )
    save(cfg, tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"), connect=_IdleLLM())
    server, thread = _serve(tmp_path, app)
    try:
        status, body = _request(
            server, "/api/bots/thyca/settings", "POST",
            {"model": {"id": "custom-model", "thinking": "custom"}},
        )
        assert status == 200, body
        assert body["effective"] == {
            "model": "custom-model", "provider": "default", "thinking": "custom",
        }
        status, body = _request(
            server, "/api/bots/thyca/settings", "POST",
            {"model": {"id": "custom-model", "thinking": "max"}},
        )
        assert status == 400, body
        # Corrupt the file behind the server: GET degrades to safe 503.
        (tmp_path / "bots" / "thyca" / "settings.json").write_text("{broken", encoding="utf-8")
        status, body = _request(server, "/api/bots/thyca/settings")
        assert status == 503 and body == {"error": "bot settings unavailable"}, body
        # POST on the corrupt bot is 503 too (the bot exists; its settings
        # are unreadable) — never a silent write or a 404.
        status, body = _request(
            server, "/api/bots/thyca/settings", "POST",
            {"model": {"id": "default"}},
        )
        assert status == 503 and body == {"error": "bot settings unavailable"}, body
    finally:
        _stop(server, thread, app)


def test_http_get_creates_no_files_and_config_untouched(tmp_path: Path) -> None:
    app = _chat_app(tmp_path)
    before_cfg = (tmp_path / "config.json").read_bytes()
    server, thread = _serve(tmp_path, app)
    try:
        settings_path = tmp_path / "bots" / "coder" / "settings.json"
        settings_path.unlink()
        twin_before = (tmp_path / "bots" / "coder" / "TWIN.md").read_bytes()
        status, body = _request(server, "/api/bots/coder/settings")
        assert status == 200
        assert body["model"] == {"id": "default", "thinking": None}
        assert not settings_path.exists()
        assert (tmp_path / "bots" / "coder" / "TWIN.md").read_bytes() == twin_before
        status, body = _request(
            server, "/api/bots/coder/settings", "POST",
            {"model": {"id": "default", "thinking": "high"}},
        )
        assert status == 200
        assert (tmp_path / "config.json").read_bytes() == before_cfg
        # save() also maintains auth.json; a settings POST must not touch it.
        auth_path = tmp_path / "auth.json"
        before_auth = auth_path.read_bytes() if auth_path.exists() else None
        status, body = _request(
            server, "/api/bots/designer/settings", "POST",
            {"model": {"id": "default"}},
        )
        assert status == 200
        assert (tmp_path / "config.json").read_bytes() == before_cfg
        if before_auth is None:
            assert not auth_path.exists()
        else:
            assert auth_path.read_bytes() == before_auth
    finally:
        _stop(server, thread, app)


def test_shape_bounds_reject_controls_and_padding(tmp_path: Path) -> None:
    from thyca.bots.settings import validate_model_id, validate_thinking

    assert validate_model_id("m1") == "m1"
    assert validate_model_id("  m1  ") == "m1"
    assert validate_thinking("low") == "low"
    assert validate_thinking(None) is None
    for bad in ("", "   ", "x" * 201, "m\n1", "m\r1", "m\t1", "\tm1\t",
                "m\x7f1", "m\x001", "m\u00851"):
        with pytest.raises(SettingsError):
            validate_model_id(bad)
    for bad in ("", "   ", "x" * 201, "lo\tw", "high\x7f", 3, ["low"]):
        with pytest.raises(SettingsError):
            validate_thinking(bad)


def test_invalid_utf8_and_directory_mark_invalid_without_raise(tmp_path: Path) -> None:
    reg = _legacy_bot(tmp_path)
    reg.create(BotCard(
        bot_id="okbot", name="okbot", role="R",
        description="Healthy.", persona="",
    ))
    settings = tmp_path / "bots" / "coder" / "settings.json"
    settings.write_bytes(b"{\xff\xfe broken")
    doc = reg.get("coder")
    assert not doc.ok and doc.card is None
    # The healthy bot stays listed; the roster skips only the broken one.
    ids = [doc.card.bot_id for doc in reg.list() if doc.ok and doc.card]
    assert "okbot" in ids and "coder" not in ids
    # A directory at the settings path is corruption, never a fallback.
    settings.unlink()
    settings.mkdir()
    doc = reg.get("coder")
    assert not doc.ok and doc.card is None
    # Symlinked card targets are never followed.
    twin = tmp_path / "bots" / "coder" / "TWIN.md"
    twin.unlink()
    twin.symlink_to(tmp_path / "bots" / "okbot" / "TWIN.md")
    assert not reg.get("coder").ok
    assert reg.require("okbot").bot_id == "okbot"


def test_failed_strip_reports_and_preserves_then_repair_succeeds(
    tmp_path: Path, monkeypatch,
) -> None:
    import thyca.bots.registry as registry_mod

    reg = _legacy_bot(tmp_path)
    twin_path = tmp_path / "bots" / "coder" / "TWIN.md"
    before = twin_path.read_bytes()
    assert b"legacy-model" in before
    real_atomic = registry_mod.atomic_write_text

    def _fail_twin(target, text, *, mode=0o600):
        if target.name == "TWIN.md":
            raise OSError("disk gone")
        return real_atomic(target, text, mode=mode)

    monkeypatch.setattr(registry_mod, "atomic_write_text", _fail_twin)
    with pytest.raises(RegistryError):
        reg.update_settings("coder", BotModel(id="new-model", thinking=None))
    # Card bytes intact (legacy model still there), settings already
    # landed and stay authoritative.
    assert twin_path.read_bytes() == before
    payload = json.loads((tmp_path / "bots" / "coder" / "settings.json").read_text())
    assert payload == {"model": {"id": "new-model", "thinking": None}}
    assert reg.get("coder").card.model == BotModel(id="new-model", thinking=None)
    # Repair and retry: the strip lands this time.
    monkeypatch.setattr(registry_mod, "atomic_write_text", real_atomic)
    updated = reg.update_settings("coder", BotModel(id="new-model", thinking=None))
    assert updated.model == BotModel(id="new-model", thinking=None)
    assert "legacy-model" not in twin_path.read_text(encoding="utf-8")


def test_directory_settings_path_is_503(tmp_path: Path) -> None:
    app = _chat_app(tmp_path)
    server, thread = _serve(tmp_path, app)
    try:
        path = tmp_path / "bots" / "coder" / "settings.json"
        path.unlink(missing_ok=True)
        path.mkdir()
        status, body = _request(server, "/api/bots/coder/settings")
        assert status == 503 and body == {"error": "bot settings unavailable"}
        status, body = _request(
            server, "/api/bots/coder/settings", "POST",
            {"model": {"id": "default"}},
        )
        assert status == 503 and body == {"error": "bot settings unavailable"}
    finally:
        _stop(server, thread, app)


def test_pause_serializes_behind_held_lock(tmp_path: Path) -> None:
    import threading

    from filelock import FileLock

    reg = _registry(tmp_path)
    reg.create(BotCard(
        bot_id="coder", name="coder", role="Coder",
        description="Writes code.", persona="",
        model=BotModel(id="m1", thinking="low"),
    ))
    other = BotRegistry(tmp_path)
    lock_path = tmp_path / "bots" / "coder.lock"
    held = FileLock(str(lock_path))
    held.acquire()
    started = threading.Event()
    done = threading.Event()
    outcome: dict = {}

    def _pause() -> None:
        started.set()
        try:
            outcome["card"] = other.set_paused("coder", True)
        except Exception as exc:  # noqa: BLE001 — recorded for assert
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_pause, daemon=True)
    try:
        thread.start()
        assert started.wait(timeout=10)
        # The pauser cannot have finished: the lock is held here.
        assert not done.is_set()
        # A concurrent settings save lands while the pauser waits. The
        # caller (this thread) already holds the lock, so write the file
        # directly instead of re-entering the registry lock.
        from thyca.bots.settings import write_settings as _write_settings

        _write_settings(tmp_path, "coder", BotModel(id="m2", thinking="high"))
        assert reg.get("coder").card.model == BotModel(id="m2", thinking="high")
    finally:
        held.release()
    assert done.wait(timeout=10)
    assert "error" not in outcome, outcome.get("error")
    # The pause applied on top of the NEW model, not the stale snapshot.
    assert outcome["card"].paused is True
    assert outcome["card"].model == BotModel(id="m2", thinking="high")
    assert thread is not None
    thread.join(timeout=10)


def test_stale_snapshot_identity_edit_retains_new_model(tmp_path: Path) -> None:
    reg = _legacy_bot(tmp_path)
    stale = reg.require("coder")
    assert stale.model == BotModel(id="legacy-model", thinking="legacy-effort")
    reg.update_settings("coder", BotModel(id="m2", thinking="high"))
    edited = apply_card_edit(
        stale, {"role": "Senior Coder", "persona": "Senior persona."}
    )
    updated = reg.update_card("coder", edited, model_changed=False)
    assert updated.role == "Senior Coder"
    assert updated.model == BotModel(id="m2", thinking="high")
    explicit = apply_card_edit(updated, {"model": "m3"})
    updated2 = reg.update_card("coder", explicit, model_changed=True)
    assert updated2.model == BotModel(id="m3", thinking="high")


def test_concurrent_delete_never_resurrects_and_lock_stable(tmp_path: Path) -> None:
    import threading

    reg = _legacy_bot(tmp_path)
    reg.update_settings("coder", BotModel(id="m1", thinking=None))
    lock_path = tmp_path / "bots" / "coder.lock"
    assert lock_path.is_file()
    ino_before = lock_path.stat().st_ino
    barrier = threading.Barrier(2)
    errors: list = []

    def _delete() -> None:
        barrier.wait(timeout=10)
        try:
            BotRegistry(tmp_path).delete("coder")
        except Exception as exc:  # noqa: BLE001 — recorded for assert
            errors.append(exc)

    def _save() -> None:
        barrier.wait(timeout=10)
        try:
            BotRegistry(tmp_path).update_settings(
                "coder", BotModel(id="m2", thinking=None)
            )
        except (RegistryError, SettingsError):
            pass  # losing the race to delete is the expected outcome

    first, second = threading.Thread(target=_delete), threading.Thread(target=_save)
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert not (tmp_path / "bots" / "coder").exists()
    assert lock_path.stat().st_ino == ino_before


def test_invalid_settings_trigger_no_fallback_provider_call(
    tmp_path: Path, monkeypatch,
) -> None:
    from thyca.app.chat_app import ConnectFactory
    from thyca.app.turn_options import resolve_bot_cfg

    llm = _RecordingLLM()
    captured: list = []

    def create(api, provider):
        captured.append(provider)
        return llm

    monkeypatch.setattr(ConnectFactory, "create", create)
    save(_runtime_config(), tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"))
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="coder", role="Coder",
            description="Writes code.", persona="",
            model=BotModel(id="card-model", thinking="custom"),
        ))
        group = app.create(bots=["coder"])["id"]
        (tmp_path / "bots" / "coder" / "settings.json").write_text(
            "{broken", encoding="utf-8"
        )
        with pytest.raises(RegistryError):
            asyncio.run(app._flush_bot_memory("coder", [], app._current_cfg()))
        assert captured == []
        # Compaction refuses the corrupt bot loudly instead of compacting
        # it under a fallback global model.
        with pytest.raises(RegistryError):
            app.compact_session(group)
        assert captured == []
    finally:
        app.shutdown()
