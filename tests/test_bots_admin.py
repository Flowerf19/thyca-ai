"""BE-5: admin tools (Thyca-only), create flow, scheduler."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from thyca.bots.admin_tools import ADMIN_TOOL_NAMES, apply_card_edit, visible_for_bot
from thyca.bots.card import BotCard
from thyca.bots.create_flow import (
    answer_flow,
    build_card,
    current_question,
    start_flow,
)
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


def test_create_flow_happy_path() -> None:
    state = start_flow()
    assert "vai trò" in current_question(state)
    state, _ = answer_flow(state, "Trợ lý lịch")
    state, _ = answer_flow(state, "Nhắc sinh nhật")
    state, _ = answer_flow(state, "Chỉ lịch")
    state, prompt = answer_flow(state, "sometimes")
    assert "ask / allow / deny" in prompt  # re-asks on bad choice
    state, prompt = answer_flow(state, "ask")
    assert "Tóm tắt" in prompt
    state, _ = answer_flow(state, "ok")
    assert state.done is True
    card = build_card(state, "lich")
    assert card.bot_id == "lich"
    assert card.bash == "ask"
    assert "Nhắc sinh nhật" in card.description


def test_create_flow_edit_loop() -> None:
    state = start_flow()
    for text in ("R", "J", "D", "ask"):
        state, _ = answer_flow(state, text)
    state, prompt = answer_flow(state, "sửa role")
    assert "vai trò" in prompt
    state, _ = answer_flow(state, "R2")
    state, _ = answer_flow(state, "J")
    state, _ = answer_flow(state, "D")
    state, _ = answer_flow(state, "deny")
    state, _ = answer_flow(state, "ok")
    assert build_card(state, "x").bash == "deny"


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
