"""Wave 1 P0 regression tests: group profile stripping, child-bot PathGuard, group flush isolation."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.bench.memory_edge_pack_a import (
    _sim_child_bot_write_canonical_soul_user_identity,
    _sim_group_chat_leaks_auto_profile_block,
    _sim_idle_sweep_group_flush_leaks_dm_tasks_and_habits,
)
from thyca.memories.habits import (
    AUTO_END,
    AUTO_START,
    PROFILE_AUTO_END,
    PROFILE_AUTO_START,
    strip_auto_habits,
)
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.tools.path_guard import PathDenied, PathGuard


def test_strip_auto_habits_removes_both_habits_and_profile_auto_blocks() -> None:
    text = (
        "# User\n"
        "- Manual preference\n\n"
        "## Habits\n"
        f"{AUTO_START}\n- Auto habit line\n{AUTO_END}\n\n"
        "## Profile\n"
        "- Manual profile bullet\n"
        f"{PROFILE_AUTO_START}\n- Auto promoted secret\n{PROFILE_AUTO_END}\n"
    )
    cleaned = strip_auto_habits(text)
    assert "Manual preference" in cleaned
    assert "Manual profile bullet" in cleaned
    assert "Auto habit line" not in cleaned
    assert "Auto promoted secret" not in cleaned


def test_strip_auto_habits_handles_unclosed_profile_auto_start() -> None:
    broken = f"# User\n- Manual\n{PROFILE_AUTO_START}\n- Leaked tail\n"
    cleaned = strip_auto_habits(broken)
    assert "Manual" in cleaned
    assert "Leaked tail" not in cleaned


def test_group_chat_strips_auto_profile_in_prompt_and_canonical_get(tmp_path: Path) -> None:
    res = _sim_group_chat_leaks_auto_profile_block(tmp_path)
    assert res.passed, res.findings


def test_child_bot_denied_write_to_root_canonical_files_but_thyca_and_dm_read_allowed(
    tmp_path: Path,
) -> None:
    res = _sim_child_bot_write_canonical_soul_user_identity(tmp_path)
    assert res.passed, res.findings

    guard = PathGuard(tmp_path)
    user_md = tmp_path / "USER.md"
    user_md.write_text("# User\n", encoding="utf-8")

    tok_child = bind_bot_id("worker-a")
    try:
        assert guard.deny_read(str(user_md)) == user_md.resolve()
        with pytest.raises(PathDenied):
            guard.deny_write(str(user_md))
    finally:
        reset_bot_id(tok_child)

    tok_thyca = bind_bot_id("thyca")
    try:
        assert guard.deny_write(str(user_md)) == user_md.resolve()
    finally:
        reset_bot_id(tok_thyca)


def test_group_idle_sweep_flush_does_not_leak_dm_tasks_or_habits(tmp_path: Path) -> None:
    res = _sim_idle_sweep_group_flush_leaks_dm_tasks_and_habits(tmp_path)
    assert res.passed, res.findings
