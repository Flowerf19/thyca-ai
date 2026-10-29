"""BE-5: admin tools (Thyca-only), scheduler."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from thyca.bots.admin_tools import ADMIN_TOOL_NAMES, apply_card_edit, visible_for_bot
from thyca.bots.card import BotCard
from thyca.bots.scheduler import CronError, due, match_field
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.tools.registry import ToolRegistry


def _card(**overrides) -> BotCard:
    base = dict(bot_id="coder", name="coder", role="Coder",
                description="Codes.", persona="persona")
    base.update(overrides)
    return BotCard(**base)


def test_admin_names_shape() -> None:
    assert set(ADMIN_TOOL_NAMES) == {
        "bot_create", "bot_pause", "bot_delete", "bot_restore",
        "bot_edit_card", "bot_delegate", "bot_stop",
    }


def test_admin_handlers_deny_child_bot(tmp_path: Path) -> None:
    import json

    from thyca.bots.admin_tools import AdminDeps, register_admin_tools
    from thyca.bots.bus import Bus
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime

    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    tool_registry = ToolRegistry()
    register_admin_tools(tool_registry,
                         AdminDeps(registry, BotRuntime(tmp_path, registry, Bus()),
                                   lambda: None))
    assert all(tool_registry.get(name) is not None for name in ADMIN_TOOL_NAMES)
    token = bind_bot_id("coder")
    try:
        for name in ADMIN_TOOL_NAMES:
            spec = tool_registry.get(name)
            args = {"bot_id": "x", "task": "t", "session_id": "s"}
            out = asyncio.run(spec.handler({k: v for k, v in args.items()}))
            assert out.startswith("denied:"), name
    finally:
        reset_bot_id(token)


def test_visible_for_bot_hides_admin() -> None:
    schemas = [{"type": "function", "function": {"name": n}}
               for n in ("bot_delegate", "read")]
    assert visible_for_bot(schemas, None) == schemas
    assert visible_for_bot(schemas, "thyca") == schemas
    assert visible_for_bot(schemas, "coder") == [schemas[1]]


def test_apply_card_edit() -> None:
    card = _card()
    updated = apply_card_edit(card, {"role": "Senior", "bash": "deny",
                                     "hidden_tools": ["bash"]})
    assert updated.role == "Senior"
    assert updated.bash == "deny"
    assert updated.hidden_tools == ("bash",)
    try:
        apply_card_edit(card, {"nope": 1})
    except ValueError as exc:
        assert "unknown card field" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def _create_via_tool(tmp_path: Path, args: dict):
    import json as _json

    from thyca.bots.admin_tools import AdminDeps, register_admin_tools
    from thyca.bots.bus import Bus
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime

    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    tool_registry = ToolRegistry()
    register_admin_tools(tool_registry,
                         AdminDeps(registry, BotRuntime(tmp_path, registry, Bus()),
                                   lambda: None))
    spec = tool_registry.get("bot_create")
    out = _json.loads(asyncio.run(spec.handler(args)))
    assert out["ok"] is True, out
    return registry.require(out["bot_id"]), out


def _schemas(*names: str) -> list[dict]:
    return [{"type": "function", "function": {"name": name}} for name in names]


def test_bot_create_grants_baseline_tools(tmp_path: Path) -> None:
    from thyca.bots.card import BASELINE_BOT_TOOLS
    from thyca.bots.runtime import filtered_tool_schemas

    card, out = _create_via_tool(tmp_path, {
        "bot_id": "helper", "role": "Helper", "description": "Helps.",
    })
    assert set(BASELINE_BOT_TOOLS) <= set(card.tools)
    assert set(out["tools"]) == set(card.tools)
    schemas = _schemas(*BASELINE_BOT_TOOLS, "read", "bash")
    names = [s["function"]["name"] for s in filtered_tool_schemas(schemas, card)]
    # Baseline is in the filtered schema; unlisted tools stay fail-closed.
    assert set(names) == set(BASELINE_BOT_TOOLS)


def test_bot_create_explicit_tools_union_and_hidden_wins(tmp_path: Path) -> None:
    from thyca.bots.runtime import filtered_tool_schemas

    card, _ = _create_via_tool(tmp_path, {
        "bot_id": "helper", "role": "Helper", "description": "Helps.",
        "tools": ["read", "memory_search"],
        "hidden_tools": ["memory_search", "bot_send"],
    })
    assert "read" in card.tools and "memory_remember" in card.tools
    schemas = _schemas("bot_send", "memory_remember", "memory_search",
                       "memory_get", "read")
    names = [s["function"]["name"] for s in filtered_tool_schemas(schemas, card)]
    assert "read" in names and "memory_remember" in names
    assert "memory_search" not in names  # hidden wins over explicit+baseline
    assert "bot_send" not in names


def test_bot_create_memory_policy_still_filters_baseline(tmp_path: Path) -> None:
    from thyca.bots.runtime import filtered_tool_schemas

    card, _ = _create_via_tool(tmp_path, {
        "bot_id": "helper", "role": "Helper", "description": "Helps.",
        "memory": {"read": False, "write": False},
    })
    schemas = _schemas("bot_send", "memory_remember", "memory_search",
                       "memory_get")
    names = [s["function"]["name"] for s in filtered_tool_schemas(schemas, card)]
    assert names == ["bot_send"]


def _tooled_registry(tmp_path: Path, models: dict):
    import asyncio as _asyncio  # noqa: F401 (kept local like other helpers)

    from thyca.bots.admin_tools import AdminDeps, register_admin_tools
    from thyca.bots.bus import Bus
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.config import ModelCfg, ProviderEntry, default_config, save
    from thyca.tools.registry import ToolRegistry

    from dataclasses import replace

    cfg = replace(
        default_config(),
        providers={"alpha": ProviderEntry(baseUrl="https://alpha.invalid/v1", apiKey="k")},
        defaultProvider="alpha",
        defaultModel="m1",
        models={name: ModelCfg(**spec) for name, spec in models.items()},
    )
    save(cfg, tmp_path / "config.json")
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    tool_registry = ToolRegistry()
    register_admin_tools(tool_registry,
                         AdminDeps(registry, BotRuntime(tmp_path, registry, Bus()),
                                   lambda: None))
    return tool_registry


def _run_tool(tool_registry, name: str, args: dict) -> str:
    import asyncio
    import json as _json

    spec = tool_registry.get(name)
    out = asyncio.run(spec.handler(args))
    try:
        body = _json.loads(out)
        assert body.get("ok") is True, out
        return out
    except (ValueError, AssertionError):
        return out


def test_bot_create_validates_model_against_live_config(tmp_path: Path) -> None:
    tools = _tooled_registry(tmp_path, {"m1": {}})
    ok = _run_tool(tools, "bot_create", {
        "bot_id": "helper", "role": "Helper", "description": "Helps.", "model": "m1",
    })
    assert '"ok": true' in ok
    failed = _run_tool(tools, "bot_create", {
        "bot_id": "ghost", "role": "Ghost", "description": "Haunts.", "model": "nope",
    })
    assert failed == "bot_create failed: unknown model"
    inherited = _run_tool(tools, "bot_create", {
        "bot_id": "plain", "role": "Plain", "description": "Plain."
    })
    assert '"ok": true' in inherited


def test_bot_edit_card_validates_model_changes_only(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard, BotModel
    from thyca.bots.registry import BotRegistry

    tools = _tooled_registry(tmp_path, {"m1": {}})
    _run_tool(tools, "bot_create", {
        "bot_id": "helper", "role": "Helper", "description": "Helps.", "model": "m1",
    })
    failed = _run_tool(tools, "bot_edit_card", {"bot_id": "helper", "model": "nope"})
    assert failed == "bot_edit_card failed: unknown model"
    moved = _run_tool(tools, "bot_edit_card", {"bot_id": "helper", "role": "Senior"})
    assert '"ok": true' in moved
    # Identity edits stay allowed on a legacy bot whose stored model is
    # unknown: validation only gates actual model changes.
    BotRegistry(tmp_path).create(BotCard(
        bot_id="legacy", name="legacy", role="Old", description="Old.",
        persona="p", model=BotModel(id="ghost"),
    ))
    renamed = _run_tool(tools, "bot_edit_card", {"bot_id": "legacy", "role": "New"})
    assert '"ok": true' in renamed
    # A thinking-only edit on a model-less legacy card validates against
    # the same "default" fallback instead of crashing on a missing model.
    from thyca.bots.card import BotCard as _BotCard
    from thyca.bots.registry import BotRegistry as _Registry

    _Registry(tmp_path).create(_BotCard(
        bot_id="plain-legacy", name="plain-legacy", role="Old", description="Old.",
        persona="p",
    ))
    thinking_only = _run_tool(
        tools, "bot_edit_card", {"bot_id": "plain-legacy", "thinking": "low"})
    assert '"ok": true' in thinking_only


def test_bot_edit_card_model_change_serializes_with_settings_update(tmp_path: Path) -> None:
    # A thinking-only admin edit must not clobber a concurrent model
    # assignment: the card is read, patched, validated, and persisted
    # inside one coordination hold. The hook pauses the handler between
    # its read and its write; the settings update provably cannot land in
    # between because it blocks on the same lock, so the final model is
    # deterministically the settings update (on the old code the stale
    # patched model would win instead).
    import threading
    import time

    from thyca.app.chat_app import ChatApp
    from thyca.bots.admin_tools import AdminDeps, register_admin_tools
    from thyca.bots.admin_tools import lifecycle
    from thyca.bots.bus import Bus
    from thyca.bots.card import BotModel
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.config import load
    from thyca.config.coordination import CoordinationError, hold

    tools = _tooled_registry(tmp_path, {"m1": {}, "m2": {}})
    BotRegistry(tmp_path).create(_card(model=BotModel(id="m1", thinking=None)))
    app = ChatApp(tmp_path, load(tmp_path / "config.json"))
    try:
        entered, release = threading.Event(), threading.Event()
        real_apply = lifecycle.apply_card_edit

        def hooked(card, patch):
            entered.set()
            assert release.wait(timeout=10)
            return real_apply(card, patch)

        real_target = lifecycle.apply_card_edit
        lifecycle.apply_card_edit = hooked
        try:
            spec = tools.get("bot_edit_card")
            outcome: dict = {}

            def run_edit() -> None:
                try:
                    outcome["edit"] = asyncio.run(
                        spec.handler({"bot_id": "coder", "thinking": "low"}))
                except Exception as exc:  # noqa: BLE001 — recorded for assert
                    outcome["error"] = exc

            editor = threading.Thread(target=run_edit)
            editor.start()
            try:
                assert entered.wait(timeout=10)
                # The handler holds coordination across read→patch→persist:
                # anything else serializes behind it instead of interleaving.
                with pytest.raises(CoordinationError):
                    with hold(tmp_path, timeout=0.3):
                        pass
                outcome["settings"] = None

                def run_settings() -> None:
                    try:
                        outcome["settings"] = app.update_bot_settings(
                            "coder", {"model": {"id": "m2", "thinking": None}})
                    except Exception as exc:  # noqa: BLE001 — recorded for assert
                        outcome["settings_error"] = exc

                setter = threading.Thread(target=run_settings)
                setter.start()
                time.sleep(0.3)
            finally:
                release.set()
            editor.join(timeout=20)
            setter.join(timeout=20)
            assert not editor.is_alive() and not setter.is_alive()
            assert "error" not in outcome, outcome.get("error")
            assert "settings_error" not in outcome, outcome.get("settings_error")
            assert json.loads(outcome["edit"])["ok"] is True
            final = BotRegistry(tmp_path).require("coder")
            assert final.model == BotModel(id="m2", thinking=None)
        finally:
            lifecycle.apply_card_edit = real_target
    finally:
        app.shutdown()


def test_cron_matcher() -> None:
    assert match_field("*", 7, 0, 59) is True
    assert match_field("*/15", 30, 0, 59) is True
    assert match_field("*/15", 31, 0, 59) is False
    assert match_field("9-17", 12, 0, 23) is True
    assert match_field("9,12,15", 12, 0, 23) is True
    try:
        match_field("nope", 1, 0, 59)
    except CronError:
        pass
    else:
        raise AssertionError("expected CronError")


def test_due_minute_precision() -> None:
    # Monday 2026-08-03 09:30. Standard cron: Monday=1, Sunday=0 and 7.
    now = datetime(2026, 8, 3, 9, 30)
    assert due("30 9 * * *", now) is True
    assert due("31 9 * * *", now) is False
    assert due("* * * * *", now) is True
    assert due(None, now) is False
    assert due("30 9 * * 1", now) is True  # Monday
    assert due("30 9 * * 2", now) is False  # Tuesday
    assert due("30 9 * * 0", now) is False  # Sunday
    sunday = datetime(2026, 8, 2, 9, 30)
    assert due("30 9 * * 0", sunday) is True
    assert due("30 9 * * 7", sunday) is True
    # DOM/DOW OR when both are restricted: Monday the 3rd fires on DOW,
    # Tuesday the 15th fires on DOM, Wednesday the 16th on neither.
    assert due("30 9 15 * 1", now) is True
    assert due("30 9 15 * 1", datetime(2026, 9, 15, 9, 30)) is True
    assert due("30 9 15 * 1", datetime(2026, 9, 16, 9, 30)) is False
    try:
        due("0 9 * *", now)
    except CronError:
        pass
    else:
        raise AssertionError("expected CronError")


def test_suggest_tint_picks_least_used_and_create_uses_it(tmp_path: Path) -> None:
    import json

    from thyca.bots.admin_tools import AdminDeps, register_admin_tools
    from thyca.bots.bus import Bus
    from thyca.bots.card import TEAM_TINTS
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime

    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()  # thyca holds accent
    tool_registry = ToolRegistry()
    register_admin_tools(tool_registry,
                         AdminDeps(registry, BotRuntime(tmp_path, registry, Bus()),
                                   lambda: None))
    create = tool_registry.get("bot_create")

    async def make(bot_id: str, **extra) -> str:
        out = await create.handler(
            {"bot_id": bot_id, "role": "R", "description": "D.", **extra})
        assert json.loads(out)["ok"], out
        return registry.require(bot_id).tint

    first = asyncio.run(make("twin-a"))
    second = asyncio.run(make("twin-b"))
    assert first in TEAM_TINTS and second in TEAM_TINTS
    assert first != "accent", "accent is Thyca's; a fresh twin must differ"
    assert second != first, "consecutive creations spread across the palette"
    explicit = asyncio.run(make("twin-c", tint="ink"))
    assert explicit == "ink", "an explicit tint always wins over the suggestion"


def test_bot_create_avatar_suggestion_and_explicit(tmp_path: Path) -> None:
    import json

    from thyca.bots.admin_tools import AdminDeps, register_admin_tools
    from thyca.bots.bus import Bus
    from thyca.bots.card import CHILD_AVATAR_VARIANTS
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime

    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    tool_registry = ToolRegistry()
    register_admin_tools(tool_registry,
                         AdminDeps(registry, BotRuntime(tmp_path, registry, Bus()),
                                   lambda: None))
    create = tool_registry.get("bot_create")

    async def make(bot_id: str, **extra) -> str:
        out = await create.handler(
            {"bot_id": bot_id, "role": "R", "description": "D.", **extra})
        assert json.loads(out)["ok"], out
        return registry.require(bot_id).avatar

    first = asyncio.run(make("twin-a"))
    second = asyncio.run(make("twin-b"))
    assert first in CHILD_AVATAR_VARIANTS and second in CHILD_AVATAR_VARIANTS
    assert first != second, "consecutive creations spread across note shapes"
    assert asyncio.run(make("twin-c", avatar="dotted")) == "dotted"
    # The star belongs to Thyca alone.
    out = asyncio.run(create.handler(
        {"bot_id": "twin-d", "role": "R", "description": "D.", "avatar": "star"}))
    assert "reserved for Thyca" in out
    assert not registry.exists("twin-d")
