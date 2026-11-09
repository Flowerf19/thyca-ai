"""SIMPLE slice 3 skill sharing: fail-first durable tests.

Approved SIMPLE design: Thyca-only share/unshare of existing team skills,
persisted shared-name state, default hidden, all current/future bots get
shared access, edits preserve share. Child may read/edit own private and
read shared; never edit shared or access foreign private. Thyca edits
preserve share. Links rejected for recognized skills, ordinary unrelated
hardlinks preserved. Bash/MCP/network untouched with explicit bypass.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _skill_body(name: str, desc: str) -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\nbody {name}\n"


def _write_team(tmp_path: Path, name: str, desc: str = "team skill") -> Path:
    d = tmp_path / "skills-team" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_skill_body(name, desc), encoding="utf-8")
    return d / "SKILL.md"


def _write_private(tmp_path: Path, bot: str, name: str, desc: str = "private") -> Path:
    d = tmp_path / "bots" / bot / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_skill_body(name, desc), encoding="utf-8")
    return d / "SKILL.md"


def _registry(tmp_path: Path):
    from thyca.bots.registry import BotRegistry

    reg = BotRegistry(tmp_path)
    reg.ensure_thyca()
    return reg


def test_existing_team_hidden_until_share(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C",
                       description="D", persona="P"))
    _write_team(tmp_path, "weather", "weather team")
    index = reg.skill_index_text("coder")
    assert "weather" not in index, "unshared team must stay hidden"


def test_share_unshare_persists_across_restart_and_bot_views(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.bots.registry import BotRegistry
    from thyca.skills.sharing import SharingService

    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C",
                       description="D", persona="P"))
    reg.create(BotCard(bot_id="helper", name="helper", role="H",
                       description="E", persona="Q"))
    _write_team(tmp_path, "weather", "weather team")
    svc = SharingService(tmp_path)
    assert svc.share("weather") is True
    assert "weather" in reg.skill_index_text("coder")
    assert "weather" in reg.skill_index_text("helper")
    # Restart: new registry instance over same root still sees shared.
    reg2 = BotRegistry(tmp_path)
    assert "weather" in reg2.skill_index_text("coder")
    assert "weather" in reg2.skill_index_text("helper")
    assert SharingService(tmp_path).list_shared() == ["weather"]
    svc.unshare("weather")
    assert "weather" not in reg.skill_index_text("coder")
    assert SharingService(tmp_path).list_shared() == []


def test_forged_child_and_missing_authority_denied(tmp_path: Path) -> None:
    import asyncio

    from thyca.bots.registry import BotRegistry
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore

    _registry(tmp_path)
    _write_team(tmp_path, "weather")
    registry = ToolRegistry()
    # Wire the real management tool over the real sharing service.
    from thyca.bots.admin_tools.skill_share import register_skill_share_tools

    register_skill_share_tools(registry, SharingService(tmp_path))
    gateway = ToolGateway(registry, TaskStore())
    from thyca.core.protocol import ToolCall

    async def _call_as(bot, args):
        token = bind_bot_id(bot)
        try:
            return await gateway.submit(ToolCall(id="t1", name="bot_skill_manage",
                                                 arguments=args))
        finally:
            reset_bot_id(token)

    # Forged child stays denied; extra caller fields never grant access
    # (schema rejects them, handler denies plain child calls as content).
    denied = asyncio.run(_call_as("coder", {"action": "share", "name": "weather"}))
    assert "denied" in denied.content.lower()
    assert SharingService(tmp_path).list_shared() == []
    forged = asyncio.run(_call_as("coder", {"action": "share", "name": "weather",
                                            "caller": "thyca", "bot_id": "thyca"}))
    assert forged.is_error
    assert SharingService(tmp_path).list_shared() == []
    # User (None) is privileged like Thyca for admin tools; Thyca allowed.
    missing = asyncio.run(_call_as(None, {"action": "share", "name": "weather"}))
    assert missing is not None
    allowed = asyncio.run(_call_as("thyca", {"action": "share", "name": "weather"}))
    assert not allowed.is_error


def test_own_private_readable_foreign_denied(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.tools.path_guard import PathDenied, PathGuard

    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C",
                       description="D", persona="P"))
    reg.create(BotCard(bot_id="helper", name="helper", role="H",
                       description="E", persona="Q"))
    own = _write_private(tmp_path, "coder", "mine", "own private")
    foreign = tmp_path / "bots" / "helper" / "skills" / "mine" / "SKILL.md"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text(_skill_body("mine", "foreign"), encoding="utf-8")
    guard = PathGuard(tmp_path)
    token = bind_bot_id("coder")
    try:
        assert guard.deny_read(str(own)).is_file()
        with pytest.raises(PathDenied):
            guard.deny_read(str(foreign))
    finally:
        reset_bot_id(token)


def test_shared_write_denied_own_edit_allowed(tmp_path: Path) -> None:
    import asyncio

    from thyca.bots.card import BotCard
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.builtin import register_file_tools
    from thyca.tools.path_guard import PathDenied, PathGuard
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.task_store import TaskStore
    from thyca.core.protocol import ToolCall

    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C",
                       description="D", persona="P"))
    shared_md = _write_team(tmp_path, "weather", "v1")
    SharingService(tmp_path).share("weather")
    own_md = _write_private(tmp_path, "coder", "mine", "v1")
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path), None)
    gateway = ToolGateway(registry, TaskStore())

    async def _write_as(bot, path, content):
        token = bind_bot_id(bot)
        try:
            return await gateway.submit(ToolCall(
                id="w1", name="write",
                arguments={"path": str(path), "content": content}))
        finally:
            reset_bot_id(token)

    denied = asyncio.run(_write_as("coder", str(shared_md), "pwned"))
    assert denied.is_error
    assert "weather" in shared_md.read_text(encoding="utf-8")
    ok = asyncio.run(_write_as("coder", str(own_md), _skill_body("mine", "v2")))
    assert not ok.is_error
    assert "v2" in own_md.read_text(encoding="utf-8")


def test_thyca_edit_preserves_share(tmp_path: Path) -> None:
    import asyncio

    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.builtin import register_file_tools
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.path_guard import PathGuard
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore
    from thyca.core.protocol import ToolCall

    _registry(tmp_path)
    shared_md = _write_team(tmp_path, "weather", "v1")
    SharingService(tmp_path).share("weather")
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path), None)
    gateway = ToolGateway(registry, TaskStore())
    token = bind_bot_id("thyca")
    try:
        result = asyncio.run(gateway.submit(ToolCall(
            id="w1", name="write",
            arguments={"path": str(shared_md), "content": _skill_body("weather", "v2")})))
    finally:
        reset_bot_id(token)
    assert not result.is_error
    assert SharingService(tmp_path).list_shared() == ["weather"]
    assert "v2" in shared_md.read_text(encoding="utf-8")


def test_symlink_hardlink_special_rejected_ordinary_hardlink_kept(tmp_path: Path) -> None:
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.tools.path_guard import PathDenied, PathGuard

    _registry(tmp_path)
    real = _write_team(tmp_path, "weather", "real")
    # Ordinary unrelated hardlink outside skill roots stays usable.
    outside = tmp_path / "notes.md"
    outside.write_text("hello", encoding="utf-8")
    import os

    try:
        os.link(outside, tmp_path / "notes-hard.md")
    except OSError:
        pytest.skip("hardlinks unsupported here")
    guard = PathGuard(tmp_path)
    assert guard.deny_read(str(tmp_path / "notes-hard.md")).is_file()
    # Symlinked skill file rejected.
    link_dir = tmp_path / "skills-team" / "linked"
    link_dir.mkdir(parents=True)
    link_md = link_dir / "SKILL.md"
    try:
        link_md.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unsupported here")
    token = bind_bot_id("thyca")
    try:
        with pytest.raises(PathDenied):
            guard.deny_read(str(link_md))
    finally:
        reset_bot_id(token)


def test_links_disallow_skill(tmp_path: Path) -> None:
    from thyca.bots.registry import BotRegistry
    from thyca.skills.sharing import SharingService

    _registry(tmp_path)
    target = tmp_path / "real-skill" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(_skill_body("weather", "real"), encoding="utf-8")
    link_dir = tmp_path / "skills-team" / "weather"
    link_dir.symlink_to(target.parent, target_is_directory=True)
    # Share first: the name grant succeeds, but the linked dir still stays
    # out of every index.
    assert SharingService(tmp_path).share("weather") is True
    reg = BotRegistry(tmp_path)
    # Symlinked skill dir must not appear as usable shared skill.
    assert "weather" not in reg.skill_index_text("coder")
    lines = reg.skill_index_text("thyca").splitlines()
    assert not any(line.startswith("- weather ") or
                   line.startswith("- weather (") for line in lines)


def test_slice2_path_guard_memory_controls_retained(tmp_path: Path) -> None:
    # Dirty slice-2 regression must stay green: pinned private raw-memory
    # denial with filtered own-entry exemption.
    from thyca.core.channel import WindowRef
    from thyca.memories.visibility import MemoryView, bound_memory_view
    from thyca.tools.path_guard import PathDenied, PathGuard

    (tmp_path / "memory").mkdir(parents=True)
    raw = tmp_path / "memory" / "2026-11-08.md"
    raw.write_text("TEAM MARKER\n", encoding="utf-8")
    guard = PathGuard(tmp_path)
    group_view = MemoryView(
        WindowRef(window_id="g", channel_id="webui", kind="group",
                  participants=("coder", "thyca")), "coder", tmp_path)
    with bound_memory_view(group_view):
        with pytest.raises(PathDenied):
            guard.deny_read(str(raw))
    pinned = MemoryView(window=None, bot_id="coder", thyca_dir=tmp_path,
                        denied_origins=frozenset({"g"}))
    with bound_memory_view(pinned):
        with pytest.raises(PathDenied):
            guard.deny_read(str(raw))


def test_shared_collisions_and_invalid_metadata(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C",
                       description="D", persona="P"))
    _write_private(tmp_path, "coder", "dup", "private copy")
    _write_team(tmp_path, "dup", "team copy")
    from thyca.skills.sharing import SharingService

    SharingService(tmp_path).share("dup")
    index = reg.skill_index_text("coder")
    assert "private copy" in index
    assert "team copy" not in index
    # Invalid shared skill shows diagnostic, not hidden denial.
    bad_dir = tmp_path / "skills-team" / "bad"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
    SharingService(tmp_path).share("bad")
    index2 = reg.skill_index_text("coder")
    assert "bad (SKILL.md invalid:" in index2


def test_gateway_real_shared_read_and_thyca_manage(tmp_path: Path) -> None:
    # Real gateway: child reads shared via checked descriptor, Thyca manages.
    import asyncio

    from thyca.bots.card import BotCard
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.builtin import register_file_tools
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.path_guard import PathGuard
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore
    from thyca.core.protocol import ToolCall
    from thyca.bots.admin_tools.skill_share import register_skill_share_tools

    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C",
                       description="D", persona="P"))
    _write_team(tmp_path, "weather", "shared body")
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path), None)
    register_skill_share_tools(registry, SharingService(tmp_path))
    gateway = ToolGateway(registry, TaskStore())

    async def _as(bot, name, args):
        token = bind_bot_id(bot)
        try:
            return await gateway.submit(ToolCall(id="t1", name=name, arguments=args))
        finally:
            reset_bot_id(token)

    shared_md = tmp_path / "skills-team" / "weather" / "SKILL.md"
    before = asyncio.run(_as("coder", "read", {"path": str(shared_md)}))
    assert before.is_error
    assert asyncio.run(_as("thyca", "bot_skill_manage",
                           {"action": "share", "name": "weather"})).is_error is False
    after = asyncio.run(_as("coder", "read", {"path": str(shared_md)}))
    assert not after.is_error and "shared body" in after.content


def _write_state_text(tmp_path: Path, text: str) -> None:
    (tmp_path / "skill-sharing.json").write_text(text, encoding="utf-8")


def _corrupt_case_root(tmp_path: Path, case: str) -> Path:
    from thyca.bots.card import BotCard

    root = tmp_path / case
    root.mkdir(parents=True, exist_ok=True)
    reg = _registry(root)
    reg.create(BotCard(bot_id="coder", name="coder", role="C",
                       description="D", persona="P"))
    _write_private(root, "coder", "mine", "own private")
    _write_team(root, "weather", "shared body")
    return root


def _check_corrupt_state(root: Path) -> None:
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingCorrupt, SharingService
    from thyca.tools.path_guard import PathDenied, PathGuard

    from thyca.bots.registry import BotRegistry

    reg = BotRegistry(root)
    # The child keeps its own skill and sees a diagnostic, never a crash.
    index = reg.skill_index_text("coder")
    assert "own private" in index
    assert "(sharing state invalid:" in index
    # A shared read is refused while state is untrusted.
    guard = PathGuard(root)
    token = bind_bot_id("coder")
    try:
        with pytest.raises(PathDenied):
            guard.deny_read(str(root / "skills-team" / "weather" / "SKILL.md"))
    finally:
        reset_bot_id(token)
    # Thyca's own index never crashes on corrupt sharing state.
    assert isinstance(reg.skill_index_text("thyca"), str)
    with pytest.raises(SharingCorrupt):
        SharingService(root).list_shared()


def test_corrupt_json_state_blocks_shared_keeps_private(tmp_path: Path) -> None:
    root = _corrupt_case_root(tmp_path, "bad-json")
    _write_state_text(root, "{not valid json")
    _check_corrupt_state(root)


def test_bad_version_state_blocks_shared_keeps_private(tmp_path: Path) -> None:
    import json

    root = _corrupt_case_root(tmp_path, "bad-version")
    _write_state_text(root, json.dumps({"version": 999, "shared": []}))
    _check_corrupt_state(root)


def test_invalid_name_state_blocks_shared_keeps_private(tmp_path: Path) -> None:
    import json

    root = _corrupt_case_root(tmp_path, "bad-name")
    _write_state_text(root, json.dumps({"version": 1, "shared": ["BAD NAME"]}))
    _check_corrupt_state(root)


def test_directory_state_is_corrupt_not_empty(tmp_path: Path) -> None:
    root = _corrupt_case_root(tmp_path, "dir-state")
    (root / "skill-sharing.json").mkdir(parents=True, exist_ok=True)
    _check_corrupt_state(root)


def test_fifo_state_is_corrupt_not_empty(tmp_path: Path) -> None:
    import os

    root = _corrupt_case_root(tmp_path, "fifo-state")
    try:
        os.mkfifo(root / "skill-sharing.json")
    except (OSError, AttributeError):
        pytest.skip("fifo unsupported here")
    _check_corrupt_state(root)


def test_thyca_private_skill_wins_over_global_name(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    thyca_private = tmp_path / "bots" / "thyca" / "skills" / "dup"
    thyca_private.mkdir(parents=True, exist_ok=True)
    (thyca_private / "SKILL.md").write_text(
        _skill_body("dup", "private copy"), encoding="utf-8")
    glob = tmp_path / "skills" / "dup"
    glob.mkdir(parents=True, exist_ok=True)
    (glob / "SKILL.md").write_text(
        _skill_body("dup", "global copy"), encoding="utf-8")
    index = reg.skill_index_text("thyca")
    assert "private copy" in index
    assert "global copy" not in index


def test_thyca_global_invalid_does_not_hide_private(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    thyca_private = tmp_path / "bots" / "thyca" / "skills" / "dup"
    thyca_private.mkdir(parents=True, exist_ok=True)
    (thyca_private / "SKILL.md").write_text(
        _skill_body("dup", "private copy"), encoding="utf-8")
    glob = tmp_path / "skills" / "dup"
    glob.mkdir(parents=True, exist_ok=True)
    (glob / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
    index = reg.skill_index_text("thyca")
    assert "private copy" in index
    assert "dup (SKILL.md invalid:" not in index


def test_root_symlink_end_to_end(tmp_path: Path) -> None:
    import asyncio
    import os

    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.builtin import register_file_tools
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.path_guard import PathDenied, PathGuard
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore
    from thyca.bots.registry import BotRegistry
    from thyca.core.protocol import ToolCall

    real = tmp_path / "real"
    (real / "bots" / "coder" / "skills" / "mine").mkdir(parents=True)
    (real / "bots" / "thyca" / "skills").mkdir(parents=True)
    (real / "skills-team" / "weather").mkdir(parents=True)
    (real / "skills-team" / "lurker").mkdir(parents=True)
    (real / "bots" / "coder" / "skills" / "mine" / "SKILL.md").write_text(
        _skill_body("mine", "own private"), encoding="utf-8")
    (real / "skills-team" / "weather" / "SKILL.md").write_text(
        _skill_body("weather", "shared body"), encoding="utf-8")
    (real / "skills-team" / "lurker" / "SKILL.md").write_text(
        _skill_body("lurker", "unshared"), encoding="utf-8")
    BotRegistry(real).ensure_thyca()
    SharingService(real).share("weather")
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported here")

    guard = PathGuard(real)
    token = bind_bot_id("coder")
    try:
        # Own private and shared reads pass through the root symlink.
        assert guard.deny_read(
            str(link / "bots" / "coder" / "skills" / "mine" / "SKILL.md")).is_file()
        assert guard.deny_read(
            str(link / "skills-team" / "weather" / "SKILL.md")).is_file()
        # Unshared team and foreign private stay denied through the alias.
        with pytest.raises(PathDenied):
            guard.deny_read(str(link / "skills-team" / "lurker" / "SKILL.md"))
        with pytest.raises(PathDenied):
            guard.deny_read(str(link / "bots" / "thyca" / "skills"))
        # Own-private write passes, shared write stays denied.
        assert guard.deny_write(
            str(link / "bots" / "coder" / "skills" / "mine" / "SKILL.md"))
        with pytest.raises(PathDenied):
            guard.deny_write(str(link / "skills-team" / "weather" / "SKILL.md"))
        # A symlink under the root stays denied even through the alias.
        outside = tmp_path / "outside" / "SKILL.md"
        outside.parent.mkdir(parents=True)
        outside.write_text(_skill_body("evil", "evil"), encoding="utf-8")
        os.symlink(tmp_path / "outside", real / "skills-team" / "evildir")
        SharingService(real).share("evildir")
        with pytest.raises(PathDenied):
            guard.deny_read(str(link / "skills-team" / "evildir" / "SKILL.md"))
    finally:
        reset_bot_id(token)

    # End-to-end read through the real file-tool handler and checked reader.
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(real), None)
    gateway = ToolGateway(registry, TaskStore())
    token = bind_bot_id("coder")
    try:
        result = asyncio.run(gateway.submit(ToolCall(
            id="r1", name="read",
            arguments={"path": str(link / "skills-team" / "weather" / "SKILL.md")})))
    finally:
        reset_bot_id(token)
    assert not result.is_error and "shared body" in result.content


def test_bot_policy_skill_matrix_matches_path_guard(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.bots.context import (BotTurnContext, bind_bot_turn_context,
                                    reset_bot_turn_context)
    from thyca.bots.policy import BotPolicy
    from thyca.core.protocol import ToolCall
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.gateway.policy import PolicyDenied
    from thyca.tools.path_guard import PathDenied, PathGuard

    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C",
                       description="D", persona="P"))
    reg.create(BotCard(bot_id="helper", name="helper", role="H",
                       description="E", persona="Q"))
    own = _write_private(tmp_path, "coder", "mine", "own private")
    foreign = _write_private(tmp_path, "helper", "secret", "foreign private")
    shared_md = _write_team(tmp_path, "weather", "shared body")
    unshared_md = _write_team(tmp_path, "lurker", "unshared")
    SharingService(tmp_path).share("weather")
    glob = tmp_path / "skills" / "gskill"
    glob.mkdir(parents=True, exist_ok=True)
    (glob / "SKILL.md").write_text(
        _skill_body("gskill", "global"), encoding="utf-8")

    policy = BotPolicy()
    guard = PathGuard(tmp_path)
    ctx_token = bind_bot_turn_context(
        BotTurnContext(sender_id="coder", room_id="r", thyca_dir=tmp_path))
    bot_token = bind_bot_id("coder")
    try:
        def _check(name: str, args: dict, allowed: bool) -> None:
            if allowed:
                policy.check(ToolCall(id="t", name=name, arguments=args))
            else:
                with pytest.raises(PolicyDenied):
                    policy.check(ToolCall(id="t", name=name, arguments=args))
            # The handler guard agrees with the gateway policy.
            if name == "read":
                if allowed:
                    assert guard.deny_read(args["path"]).is_file()
                else:
                    with pytest.raises(PathDenied):
                        guard.deny_read(args["path"])
            else:
                if allowed:
                    assert guard.deny_write(args["path"]) is not None
                else:
                    with pytest.raises(PathDenied):
                        guard.deny_write(args["path"])

        _check("read", {"path": str(own)}, True)
        _check("read", {"path": str(shared_md)}, True)
        _check("read", {"path": str(unshared_md)}, False)
        _check("read", {"path": str(foreign)}, False)
        _check("read", {"path": str(glob / "SKILL.md")}, False)
        _check("write", {"path": str(own),
                         "content": _skill_body("mine", "v2")}, True)
        _check("write", {"path": str(shared_md), "content": "pwned"}, False)
    finally:
        reset_bot_id(bot_token)
        reset_bot_turn_context(ctx_token)


def test_share_rejects_regular_file(tmp_path: Path) -> None:
    from thyca.skills.sharing import SharingService

    _registry(tmp_path)
    # A plain file where a skill dir should be is a typo, not a skill.
    (tmp_path / "skills-team").mkdir(parents=True, exist_ok=True)
    (tmp_path / "skills-team" / "flat").write_text("not a dir", encoding="utf-8")
    with pytest.raises(ValueError, match="no such team skill"):
        SharingService(tmp_path).share("flat")
    # A symlinked skill dir still grants by name; the gates stay closed.
    target = tmp_path / "real-skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        _skill_body("linked", "real"), encoding="utf-8")
    try:
        (tmp_path / "skills-team" / "linked").symlink_to(
            target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported here")
    assert SharingService(tmp_path).share("linked") is True


def test_hardlink_inside_skill_root_denied(tmp_path: Path) -> None:
    import os

    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.path_guard import PathDenied, PathGuard

    _registry(tmp_path)
    shared_md = _write_team(tmp_path, "weather", "shared body")
    SharingService(tmp_path).share("weather")
    alias = tmp_path / "skills-team" / "weather" / "alias.md"
    try:
        os.link(shared_md, alias)
    except OSError:
        pytest.skip("hardlinks unsupported here")
    guard = PathGuard(tmp_path)
    token = bind_bot_id("coder")
    try:
        with pytest.raises(PathDenied):
            guard.deny_read(str(alias))
    finally:
        reset_bot_id(token)
    # Thyca is denied too: link safety applies to every reader.
    token = bind_bot_id("thyca")
    try:
        with pytest.raises(PathDenied):
            guard.deny_read(str(alias))
    finally:
        reset_bot_id(token)


def test_special_file_inside_skill_root_denied(tmp_path: Path) -> None:
    import os

    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.tools.path_guard import PathDenied, PathGuard

    _registry(tmp_path)
    _write_team(tmp_path, "weather", "shared body")
    fifo = tmp_path / "skills-team" / "weather" / "pipe.md"
    try:
        os.mkfifo(fifo)
    except (OSError, AttributeError):
        pytest.skip("fifo unsupported here")
    guard = PathGuard(tmp_path)
    token = bind_bot_id("thyca")
    try:
        with pytest.raises(PathDenied):
            guard.deny_read(str(fifo))
    finally:
        reset_bot_id(token)
