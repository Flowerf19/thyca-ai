"""Durable hardening regressions for approved two-doors plan T0-T7.

Each test is deterministic (events/barriers, no sleeps for sync) and runs
in an isolated tmp root with synthetic HOME. No real ~/.thyca, no installs.
Fail-first: every test fails on the unfixed behavior and passes after repair.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import threading
from pathlib import Path

import pytest


def _skill_body(name: str, desc: str) -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\nbody {name}\n"


def _write_team(root: Path, name: str, desc: str = "team") -> Path:
    d = root / "skills-team" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_skill_body(name, desc), encoding="utf-8")
    return d / "SKILL.md"


def _registry(tmp_path: Path):
    from thyca.bots.registry import BotRegistry
    reg = BotRegistry(tmp_path)
    reg.ensure_thyca()
    return reg


def _gateway_with_file_tools(tmp_path: Path):
    from thyca.tools.builtin import register_file_tools
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.path_guard import PathGuard
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path), None)
    return ToolGateway(registry, TaskStore())


# T1: missing identity denied, state unchanged.
def test_t1_missing_identity_denied_state_unchanged(tmp_path: Path) -> None:
    import asyncio as _aio
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore
    from thyca.core.protocol import ToolCall
    _registry(tmp_path)
    _write_team(tmp_path, "weather")
    from thyca.bots.admin_tools.skill_share import register_skill_share_tools
    registry = ToolRegistry()
    register_skill_share_tools(registry, SharingService(tmp_path))
    gateway = ToolGateway(registry, TaskStore())

    async def _call(bot, args):
        tok = bind_bot_id(bot)
        try:
            return await gateway.submit(ToolCall(id="t1", name="bot_skill_manage", arguments=args))
        finally:
            reset_bot_id(tok)

    for action, args in [("list", {"action": "list"}), ("share", {"action": "share", "name": "weather"}), ("unshare", {"action": "unshare", "name": "weather"})]:
        denied = _aio.run(_call(None, args))
        assert "denied" in denied.content.lower(), f"missing identity must be denied for {action}"
        assert denied.is_error or "denied" in denied.content.lower()
    assert SharingService(tmp_path).list_shared() == []
    denied_child = _aio.run(_call("coder", {"action": "share", "name": "weather"}))
    assert "denied" in denied_child.content.lower()
    assert SharingService(tmp_path).list_shared() == []
    ok = _aio.run(_call("thyca", {"action": "share", "name": "weather"}))
    assert not ok.is_error
    assert SharingService(tmp_path).list_shared() == ["weather"]


# T2: reserved tmp denied lexically and via alias.
def test_t2_reserved_tmp_denied_lexical_and_alias(tmp_path: Path) -> None:
    import tempfile
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.tools.path_guard import PathDenied, PathGuard
    guard = PathGuard(tmp_path)
    for name in ("skill-sharing.json", "skill-sharing.lock", "skill-sharing.json.tmp"):
        with pytest.raises(PathDenied):
            guard.deny_write(str(tmp_path / name))
        tok = bind_bot_id("coder")
        try:
            with pytest.raises(PathDenied):
                guard.deny_write(str(tmp_path / name))
        finally:
            reset_bot_id(tok)
    outside = Path(tempfile.mkdtemp())
    link = outside / "alias.json"
    try:
        link.symlink_to(tmp_path / "skill-sharing.json")
    except OSError:
        pytest.skip("symlinks unsupported")
    with pytest.raises(PathDenied):
        guard.deny_write(str(link))
    link_tmp = outside / "alias-tmp"
    try:
        link_tmp.symlink_to(tmp_path / "skill-sharing.json.tmp")
    except OSError:
        pytest.skip("symlinks unsupported")
    with pytest.raises(PathDenied):
        guard.deny_write(str(link_tmp))


# T2 exploit: ordinary write's implicit temp must not follow a symlink.
def test_t2_ordinary_tmp_symlink_cannot_overwrite_grants(tmp_path: Path) -> None:
    import asyncio as _aio
    from thyca.core.protocol import ToolCall
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    _registry(tmp_path)
    _write_team(tmp_path, "weather")
    SharingService(tmp_path).share("weather")
    assert SharingService(tmp_path).list_shared() == ["weather"]
    # Attacker plants note.tmp -> skill-sharing.json, then a child writes note.
    note_tmp = tmp_path / "note.tmp"
    try:
        note_tmp.symlink_to(tmp_path / "skill-sharing.json")
    except OSError:
        pytest.skip("symlinks unsupported")
    gateway = _gateway_with_file_tools(tmp_path)

    async def _child_write():
        tok = bind_bot_id("coder")
        try:
            return await gateway.submit(ToolCall(
                id="t1", name="write",
                arguments={"path": str(tmp_path / "note"),
                           "content": json.dumps({"version": 1, "shared": ["evil"]})}))
        finally:
            reset_bot_id(tok)

    res = _aio.run(_child_write())
    assert res.is_error, f"temp-alias write must fail, got {res.content!r}"
    assert SharingService(tmp_path).list_shared() == ["weather"]
    assert not (tmp_path / "note").exists()
    # The preexisting temp is never deleted or followed.
    assert note_tmp.is_symlink(), "preexisting user temp must be preserved"


# T2 ordinary edit uses the same safe temp.
def test_t2_ordinary_edit_tmp_symlink_blocked(tmp_path: Path) -> None:
    import asyncio as _aio
    from thyca.core.protocol import ToolCall
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    (tmp_path / "note").write_text("hello world", encoding="utf-8")
    note_tmp = tmp_path / "note.tmp"
    try:
        note_tmp.symlink_to(tmp_path / "skill-sharing.json")
    except OSError:
        pytest.skip("symlinks unsupported")
    gateway = _gateway_with_file_tools(tmp_path)

    async def _child_edit():
        tok = bind_bot_id("coder")
        try:
            return await gateway.submit(ToolCall(
                id="t1", name="edit",
                arguments={"path": str(tmp_path / "note"),
                           "edits": [{"oldText": "hello", "newText": "bye"}]}))
        finally:
            reset_bot_id(tok)

    res = _aio.run(_child_edit())
    assert res.is_error, f"temp-alias edit must fail, got {res.content!r}"
    assert (tmp_path / "note").read_text(encoding="utf-8") == "hello world"
    assert note_tmp.is_symlink(), "preexisting user temp must be preserved"


# T2: preexisting ordinary temp is never deleted or overwritten.
def test_t2_preexisting_tmp_never_deleted(tmp_path: Path) -> None:
    import asyncio as _aio
    from thyca.core.protocol import ToolCall
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    (tmp_path / "note.tmp").write_text("user temp bytes", encoding="utf-8")
    gateway = _gateway_with_file_tools(tmp_path)

    async def _child_write():
        tok = bind_bot_id("thyca")
        try:
            return await gateway.submit(ToolCall(
                id="t1", name="write",
                arguments={"path": str(tmp_path / "note"), "content": "new"}))
        finally:
            reset_bot_id(tok)

    res = _aio.run(_child_write())
    assert res.is_error
    assert (tmp_path / "note.tmp").read_text(encoding="utf-8") == "user temp bytes"
    assert not (tmp_path / "note").exists()


# T2 publication interleaving: hook after the real tmp is prepared,
# immediately before replace; child write/edit attempts must all fail.
def test_t2_publication_interleaving_tmp_not_substituted(tmp_path: Path, monkeypatch) -> None:
    import asyncio as _aio
    from thyca.core.protocol import ToolCall
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    _registry(tmp_path)
    _write_team(tmp_path, "weather")
    _write_team(tmp_path, "evil", "evil")
    svc = SharingService(tmp_path)
    assert svc.list_shared() == []

    entered = threading.Event()
    release = threading.Event()
    orig_replace = pathlib.Path.replace

    def _hooked_replace(self, target):
        if self.name == "skill-sharing.json.tmp" and Path(target).name == "skill-sharing.json":
            entered.set()
            assert release.wait(timeout=15), "writer hook timed out"
        return orig_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", _hooked_replace)
    gateway = _gateway_with_file_tools(tmp_path)
    result: dict = {}

    def _writer():
        try:
            result["ok"] = svc.share("weather")
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    th = threading.Thread(target=_writer)
    th.start()
    try:
        assert entered.wait(timeout=15), "writer never reached pre-replace hook"
        # The real tmp is already prepared and fsynced; all three child
        # substitution attempts must fail.
        async def _child_write(path, content):
            tok = bind_bot_id("coder")
            try:
                return await gateway.submit(ToolCall(
                    id="t1", name="write",
                    arguments={"path": str(path), "content": content}))
            finally:
                reset_bot_id(tok)

        async def _child_edit(path):
            tok = bind_bot_id("coder")
            try:
                return await gateway.submit(ToolCall(
                    id="t1", name="edit",
                    arguments={"path": str(path),
                               "edits": [{"oldText": "weather", "newText": "evil"}]}))
            finally:
                reset_bot_id(tok)

        denied_state = _aio.run(_child_write(
            tmp_path / "skill-sharing.json", '{"version":1,"shared":["evil"]}\n'))
        assert denied_state.is_error, "state overwrite must be denied"
        denied_tmp = _aio.run(_child_write(
            tmp_path / "skill-sharing.json.tmp", '{"version":1,"shared":["evil"]}\n'))
        assert denied_tmp.is_error, "tmp substitution must be denied"
        denied_edit = _aio.run(_child_edit(tmp_path / "skill-sharing.json"))
        assert denied_edit.is_error, "edit substitution must be denied"
    finally:
        release.set()
        th.join(timeout=15)
    assert not th.is_alive(), "writer thread must finish"
    assert result.get("ok") is True, f"writer failed: {result.get('error')!r}"
    assert SharingService(tmp_path).list_shared() == ["weather"]


# T3: outer alias into protected skill denied for Thyca; inner alias denied.
def test_t3_outer_alias_denied_for_thyca(tmp_path: Path) -> None:
    import tempfile
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.skills.sharing import SharingService
    from thyca.tools.path_guard import PathDenied, PathGuard
    (tmp_path / "skills-team" / "weather").mkdir(parents=True)
    (tmp_path / "skills-team" / "weather" / "SKILL.md").write_text(_skill_body("weather", "shared"), encoding="utf-8")
    SharingService(tmp_path).share("weather")
    outside = Path(tempfile.mkdtemp())
    alias = outside / "aliasdir"
    try:
        alias.symlink_to(tmp_path / "skills-team", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported")
    guard = PathGuard(tmp_path)
    for bot in ("thyca", None):
        tok = bind_bot_id(bot)
        try:
            with pytest.raises(PathDenied):
                guard.deny_read(str(alias / "weather" / "SKILL.md"))
            with pytest.raises(PathDenied):
                guard.deny_write(str(alias / "weather" / "SKILL.md"))
        finally:
            reset_bot_id(tok)
    file_alias = outside / "evil.md"
    try:
        file_alias.symlink_to(tmp_path / "skills-team" / "weather" / "SKILL.md")
    except OSError:
        pytest.skip("symlinks unsupported")
    tok = bind_bot_id("thyca")
    try:
        with pytest.raises(PathDenied):
            guard.deny_read(str(file_alias))
        with pytest.raises(PathDenied):
            guard.deny_write(str(file_alias))
    finally:
        reset_bot_id(tok)


def test_t3_inner_symlink_still_denied_and_sentinel_kept(tmp_path: Path) -> None:
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.tools.path_guard import PathDenied, PathGuard
    _registry(tmp_path)
    _write_team(tmp_path, "weather", "real")
    sentinel = tmp_path / "sentinel.md"
    sentinel.write_text("keep", encoding="utf-8")
    try:
        (tmp_path / "skills-team" / "evildir").symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported")
    (tmp_path / "outside").mkdir(exist_ok=True)
    (tmp_path / "outside" / "SKILL.md").write_text(_skill_body("evil", "evil"), encoding="utf-8")
    from thyca.skills.sharing import SharingService
    try:
        SharingService(tmp_path).share("evildir")
    except ValueError:
        pass
    guard = PathGuard(tmp_path)
    tok = bind_bot_id("thyca")
    try:
        with pytest.raises(PathDenied):
            guard.deny_read(str(tmp_path / "skills-team" / "evildir" / "SKILL.md"))
    finally:
        reset_bot_id(tok)
    assert sentinel.read_text() == "keep"


# T4: FIFO final open must fail promptly with a precise error (bounded).
def test_t4_fifo_checked_read_fails_promptly(tmp_path: Path) -> None:
    import os
    fifo = tmp_path / "skills-team" / "weather" / "SKILL.md"
    fifo.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkfifo(fifo)
    except (OSError, AttributeError):
        pytest.skip("fifo unsupported")
    code = (
        "from pathlib import Path; "
        f"from thyca.skills.sharing import read_skill_checked; "
        f"read_skill_checked(Path({str(tmp_path)!r}), Path({str(fifo)!r}))"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("checked read blocked on FIFO instead of failing promptly")
    assert proc.returncode != 0, f"fifo must fail, got {proc.stdout!r}"
    assert "not a regular file" in proc.stderr.lower(), (
        f"fifo must be rejected as non-regular, got stderr: {proc.stderr!r}")


# T4: validation bypass must not help — the pinned open still fails closed.
def test_t4_parent_swap_still_fails_with_bypassed_validation(tmp_path: Path, monkeypatch) -> None:
    _write_team(tmp_path, "weather", "real body")
    link_path = tmp_path / "skills-team" / "weather" / "SKILL.md"
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "SKILL.md").write_text(_skill_body("weather", "evil"), encoding="utf-8")
    weather_dir = tmp_path / "skills-team" / "weather"
    real_dir = tmp_path / "real-weather"
    weather_dir.rename(real_dir)
    try:
        weather_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        real_dir.rename(weather_dir)
        pytest.skip("symlinks unsupported")
    try:
        import thyca.skills.sharing as _sharing
        monkeypatch.setattr(_sharing, "_has_symlink_component", lambda *a, **k: False)
        with pytest.raises(ValueError):
            _sharing.read_skill_checked(tmp_path, link_path)
    finally:
        try:
            weather_dir.unlink()
        except OSError:
            pass
        real_dir.rename(weather_dir)


def test_t4_symlink_swap_before_open_denied(tmp_path: Path) -> None:
    _write_team(tmp_path, "weather", "real body")
    link_path = tmp_path / "skills-team" / "weather" / "SKILL.md"
    outside = tmp_path / "outside.md"
    outside.write_text("evil", encoding="utf-8")
    link_path.unlink()
    try:
        link_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unsupported")
    from thyca.skills.sharing import read_skill_checked
    with pytest.raises(ValueError):
        read_skill_checked(tmp_path, link_path)


# T4: missing SKILL.md builds its diagnostic without reopening the pathname.
def test_t4_missing_diagnostic_without_reopen(tmp_path: Path, monkeypatch) -> None:
    from thyca.skills.sharing import SharingService
    from thyca.skills.store import SkillStore
    _registry(tmp_path)
    _write_team(tmp_path, "weather", "public team")
    SharingService(tmp_path).share("weather")
    (tmp_path / "skills-team" / "weather" / "SKILL.md").unlink()
    calls: list = []
    orig_meta = SkillStore._meta

    def _boom(self, skill_dir):
        calls.append(skill_dir.name)
        raise AssertionError("missing path must not be reopened")

    monkeypatch.setattr(SkillStore, "_meta", _boom)
    from thyca.bots.registry import BotRegistry
    index = BotRegistry(tmp_path).skill_index_text("coder")
    assert calls == [], f"_meta reopened for missing file: {calls}"
    assert "weather (SKILL.md invalid: missing SKILL.md)" in index


# T4: dangling symlink at the missing path stays excluded, never injected.
def test_t4_dangling_symlink_missing_excluded(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.skills.sharing import SharingService
    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
    reg.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
    priv = tmp_path / "bots" / "helper" / "skills" / "secret"
    priv.mkdir(parents=True, exist_ok=True)
    (priv / "SKILL.md").write_text(_skill_body("secret", "SECRET-PRIV-9X"), encoding="utf-8")
    d = tmp_path / "skills-team" / "weather"
    d.mkdir(parents=True, exist_ok=True)
    try:
        (d / "SKILL.md").symlink_to(tmp_path / "does-not-exist-target")
    except OSError:
        pytest.skip("symlinks unsupported")
    try:
        SharingService(tmp_path).share("weather")
    except ValueError:
        pass
    index = reg.skill_index_text("coder")
    assert "SECRET-PRIV-9X" not in index
    assert "weather" not in index, f"dangling link must be excluded, got: {index!r}"


# T4: undecodable private skill keeps its diagnostic and its precedence.
def test_t4_undecodable_private_keeps_diagnostic_and_precedence(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.skills.sharing import SharingService
    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
    reg.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
    bad = tmp_path / "bots" / "coder" / "skills" / "badpriv"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "SKILL.md").write_bytes(b"\xff\xfe invalid \xff")
    index = reg.skill_index_text("coder")
    assert any("badpriv" in line and "invalid" in line for line in index.splitlines()), (
        f"undecodable private must stay a diagnostic, got: {index!r}")
    s = tmp_path / "skills-team" / "badpriv"
    s.mkdir(parents=True, exist_ok=True)
    (s / "SKILL.md").write_text(_skill_body("badpriv", "shared-desc"), encoding="utf-8")
    SharingService(tmp_path).share("badpriv")
    index2 = reg.skill_index_text("coder")
    assert any("badpriv" in line and "invalid" in line for line in index2.splitlines()), (
        f"private diagnostic must win over shared same-name, got: {index2!r}")
    assert "shared-desc" not in index2


# T4 boundary: swap exactly between validation and descriptor open.
# Skill names match, so only the checked open (not a name mismatch) stops
# the injection. Both era hooks are armed: the checked-tree hook fires in
# the validation-to-open window (excluded fail-closed); the legacy hook
# fires in the legacy check-to-read window and injects on old code (red).
def test_t4_validation_open_swap_matching_name_excluded(tmp_path: Path, monkeypatch) -> None:
    from thyca.bots.card import BotCard
    from thyca.skills.sharing import SharingService
    import thyca.skills.sharing as _sharing
    from thyca.skills.store import SkillStore
    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
    reg.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
    # Private skill whose frontmatter name MATCHES the team folder: no
    # name-mismatch rejection can save an unchecked read here.
    priv = tmp_path / "bots" / "helper" / "skills" / "secret"
    priv.mkdir(parents=True, exist_ok=True)
    (priv / "SKILL.md").write_text(_skill_body("weather", "MARKER-EVIL-7Z"), encoding="utf-8")
    _write_team(tmp_path, "weather", "public team")
    SharingService(tmp_path).share("weather")
    assert "MARKER-EVIL-7Z" not in reg.skill_index_text("coder")
    team_md = tmp_path / "skills-team" / "weather" / "SKILL.md"
    real_read = _sharing.read_skill_checked
    real_meta = SkillStore._meta
    swapped = {"done": False}

    def _do_swap():
        team_md.unlink()
        team_md.symlink_to(priv / "SKILL.md")

    def _hooked_read(root, lexical):
        # Fires after _metas_for validation (lstat) and before the real
        # descriptor open: the exact validation-to-open window.
        if not swapped["done"] and Path(lexical) == team_md:
            swapped["done"] = "checked"
            _do_swap()
        return real_read(root, lexical)

    def _hooked_meta(self, skill_dir):
        # Legacy check-to-read window (old code reads via _meta after its
        # own listing validation): swapping here injects on old code.
        if (not swapped["done"] and skill_dir.name == "weather"
                and skill_dir.parent.name == "skills-team"):
            swapped["done"] = "legacy"
            _do_swap()
        return real_meta(self, skill_dir)

    monkeypatch.setattr(_sharing, "read_skill_checked", _hooked_read)
    monkeypatch.setattr(SkillStore, "_meta", _hooked_meta)
    index = reg.skill_index_text("coder")
    assert swapped["done"], "a hook must fire in a validation-to-open window"
    assert "MARKER-EVIL-7Z" not in index, "swapped link must not inject"
    assert "weather" not in index, f"swapped door must be excluded fail-closed, got: {index!r}"
    # Old-HEAD oracle: the plain reopening reader follows the swapped link
    # and, with matching names, indexes the private marker. Uses the real
    # _meta (unhooked) to document the prevented leak mechanism.
    monkeypatch.undo()
    oracle = SkillStore(tmp_path)._meta(team_md.parent)
    assert oracle.ok and "MARKER-EVIL-7Z" in oracle.description, (
        "unchecked read must demonstrate the prevented injection")


# T4 boundary: swap the pathname after the descriptor is already open.
# The pinned descriptor still yields the pre-open allowed bytes (or the
# open itself fails safe); swapped evil bytes must never surface.
def test_t4_after_open_swap_reads_pinned_content(tmp_path: Path, monkeypatch) -> None:
    import os as _os
    import thyca.skills.sharing as _sharing
    d = tmp_path / "skills-team" / "weather"
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    allowed = _skill_body("weather", "allowed-team-desc")
    md.write_text(allowed, encoding="utf-8")
    evil = tmp_path / "evil-weather.md"
    evil.write_text(_skill_body("weather", "EVIL-SWAPPED-9Q"), encoding="utf-8")
    real_open, real_fstat = _os.open, _os.fstat
    state = {"leaf_open": False, "swapped": False}

    def _open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if isinstance(path, str) and path == "SKILL.md":
            state["leaf_open"] = True
        return fd

    def _fstat(fd):
        if state["leaf_open"] and not state["swapped"]:
            state["swapped"] = True
            # Rename-swap AFTER open, BEFORE validation/read: new inode.
            _os.replace(str(evil), str(md))
        return real_fstat(fd)

    monkeypatch.setattr(_sharing.os, "open", _open)
    monkeypatch.setattr(_sharing.os, "fstat", _fstat)
    try:
        text = _sharing.read_skill_checked(tmp_path, md)
    except ValueError:
        text = None
    assert state["swapped"], "swap must land after open, before read"
    # Fail safe: the replaced (unlinked) descriptor must either yield the
    # pinned pre-open bytes or raise; the swapped evil bytes must never
    # surface through the pinned descriptor.
    assert text is None or text == allowed, (
        f"must return pinned bytes or fail safe, got: {text!r}")
    if text is not None:
        assert "EVIL-SWAPPED-9Q" not in text
    # Control: swapping an unrelated sibling after open leaves the pinned
    # read of the real target intact.
    md.write_text(allowed, encoding="utf-8")
    sib = d / "other.md"
    sib.write_text("sibling", encoding="utf-8")
    assert _sharing.read_skill_checked(tmp_path, md) == allowed


# T4: BOM bytes survive native read/edit; the index still validates.
def test_t4_bom_native_preserved_but_index_valid(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.skills.sharing import SharingService, read_skill_checked
    reg = _registry(tmp_path)
    reg.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
    d = tmp_path / "skills-team" / "weather"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_bytes(b"\xef\xbb\xbf" + _skill_body("weather", "withbom").encode())
    SharingService(tmp_path).share("weather")
    text = read_skill_checked(tmp_path, d / "SKILL.md")
    assert text.startswith("\ufeff"), "native read must preserve BOM bytes"
    index = reg.skill_index_text("coder")
    assert "withbom" in index, f"BOM-headed skill must still validate, got: {index!r}"


# T4/T3 skill write: encode-first, own-temp cleanup, preexisting temp kept.
def test_skill_write_encode_first_and_cleanup(tmp_path: Path) -> None:
    from thyca.skills.sharing import write_skill_checked
    own = tmp_path / "bots" / "coder" / "skills" / "mine"
    own.mkdir(parents=True, exist_ok=True)
    (own / "SKILL.md").write_text(_skill_body("mine", "v1"), encoding="utf-8")
    with pytest.raises(ValueError):
        write_skill_checked(tmp_path, own / "SKILL.md", "a\ud800b")
    assert not (own / "SKILL.md.tmp").exists(), "failed op must not leave its tmp"
    assert "v1" in (own / "SKILL.md").read_text(encoding="utf-8")
    write_skill_checked(tmp_path, own / "SKILL.md", _skill_body("mine", "v2"))
    assert "v2" in (own / "SKILL.md").read_text(encoding="utf-8")
    # Preexisting user temp blocks and is preserved.
    (own / "SKILL.md.tmp").write_text("user temp", encoding="utf-8")
    with pytest.raises(ValueError):
        write_skill_checked(tmp_path, own / "SKILL.md", _skill_body("mine", "v3"))
    assert (own / "SKILL.md.tmp").read_text(encoding="utf-8") == "user temp"
    assert "v2" in (own / "SKILL.md").read_text(encoding="utf-8")


def test_skill_write_fault_injection_cleans_own_tmp(tmp_path: Path, monkeypatch) -> None:
    import os as _os
    from thyca.skills import sharing as _sharing
    own = tmp_path / "bots" / "coder" / "skills" / "mine"
    own.mkdir(parents=True, exist_ok=True)
    (own / "SKILL.md").write_text(_skill_body("mine", "v1"), encoding="utf-8")
    real_write = _os.write
    calls = {"n": 0}

    def _flaky(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected write fault")
        return real_write(fd, data)

    monkeypatch.setattr(_sharing.os, "write", _flaky)
    with pytest.raises(ValueError):
        _sharing.write_skill_checked(tmp_path, own / "SKILL.md", _skill_body("mine", "v2"))
    assert not (own / "SKILL.md.tmp").exists(), "injected failure must clean own tmp"
    assert "v1" in (own / "SKILL.md").read_text(encoding="utf-8")
    monkeypatch.undo()
    _sharing.write_skill_checked(tmp_path, own / "SKILL.md", _skill_body("mine", "v2"))
    assert "v2" in (own / "SKILL.md").read_text(encoding="utf-8")


# T5: skip flag avoids the global scan; standalone default still scans.
def test_t5_skip_flag_avoids_global_scan(tmp_path: Path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from thyca.memories.active import ActiveMemory
    mem = ActiveMemory(tmp_path)
    mem.ensure_files(datetime.now(ZoneInfo("UTC")))
    state = mem.open_session(datetime.now(ZoneInfo("UTC")))
    calls: list[int] = []
    orig = mem._skills.index_text

    def _counted():
        calls.append(1)
        return orig()

    mem._skills.index_text = _counted  # type: ignore[method-assign]
    try:
        snap = mem.refresh(state, datetime.now(ZoneInfo("UTC")), conversation_id=None, reader="thyca", skip_skills=True)
        assert snap.skills == ""
        assert calls == []
        snap2 = mem.peek(state, datetime.now(ZoneInfo("UTC")), conversation_id=None, reader="thyca", skip_skills=True)
        assert snap2.skills == ""
        assert calls == []
        snap3 = mem.refresh(state, datetime.now(ZoneInfo("UTC")), conversation_id=None, reader="thyca")
        assert calls == [1]
        assert isinstance(snap3.skills, str)
    finally:
        mem._skills.index_text = orig  # type: ignore[method-assign]


def _echo_app(tmp_path: Path, reply_text: str = "ECHO-REPLY"):
    from seeded_config import seeded_config
    from thyca.configs import load, save
    from thyca.app.chat_app import ChatApp
    from thyca.llms.llm_base import ChatReply

    class _Echo:
        async def chat(self, messages, tools=None):
            return ChatReply(content=reply_text, model="m",
                             usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=_Echo())


# T5: the live runtime turn skips the global scan (behavioral, via spy).
def test_t5_runtime_turn_skips_global_scan(tmp_path: Path, monkeypatch) -> None:
    from thyca.bots.card import BotCard
    from thyca.skills.store import SkillStore
    app = _echo_app(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
        dm = app.bot_dm("coder")["conversation_id"]
        refresh_kwargs: list = []
        orig_refresh = app._memory.refresh

        def _spy_refresh(state, now, conversation_id=None, reader=None, **kw):
            refresh_kwargs.append(kw)
            return orig_refresh(state, now, conversation_id=conversation_id, reader=reader, **kw)

        monkeypatch.setattr(app._memory, "refresh", _spy_refresh)
        index_calls: list = []
        orig_index = SkillStore.index_text

        def _spy_index(self):
            index_calls.append(1)
            return orig_index(self)

        monkeypatch.setattr(SkillStore, "index_text", _spy_index)
        out = app.turn(dm, "hello echo")
        assert "ECHO-REPLY" in str(out.get("reply", "")), f"turn must answer, got {out!r}"
        assert refresh_kwargs, "runtime turn must call ActiveMemory.refresh"
        assert any(kw.get("skip_skills") is True for kw in refresh_kwargs), (
            f"runtime must skip the obsolete global scan, got {refresh_kwargs!r}")
        assert index_calls == [], "global scan must not run during a runtime turn"
    finally:
        app.shutdown()


# T5: the live context meter skips the global scan (behavioral, via spy).
def test_t5_meter_skips_global_scan(tmp_path: Path, monkeypatch) -> None:
    from thyca.bots.card import BotCard
    from thyca.skills.store import SkillStore
    app = _echo_app(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
        grp = app.create(bots=["coder", "helper"])["conversation_id"]
        peek_kwargs: list = []
        orig_peek = app._memory.peek

        def _spy_peek(state, now, conversation_id=None, reader=None, **kw):
            peek_kwargs.append(kw)
            return orig_peek(state, now, conversation_id=conversation_id, reader=reader, **kw)

        monkeypatch.setattr(app._memory, "peek", _spy_peek)
        index_calls: list = []
        orig_index = SkillStore.index_text

        def _spy_index(self):
            index_calls.append(1)
            return orig_index(self)

        monkeypatch.setattr(SkillStore, "index_text", _spy_index)
        app.bot_operations(grp, "coder")
        assert peek_kwargs, "meter must call ActiveMemory.peek"
        assert any(kw.get("skip_skills") is True for kw in peek_kwargs), (
            f"meter must skip the obsolete global scan, got {peek_kwargs!r}")
        assert index_calls == [], "global scan must not run during metering"
    finally:
        app.shutdown()


# T6: nonmember sender and unknown destinations fail closed.
def test_t6_delegate_rejects_nonmember_sender(tmp_path: Path) -> None:
    from seeded_config import seeded_config
    from thyca.configs import load, save
    from thyca.app.chat_app import ChatApp
    from thyca.bots.card import BotCard
    save(seeded_config(), tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"))
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
        room = app.create(bots=["coder", "helper"])["conversation_id"]
        from thyca.bots.admin_tools import register_admin_tools
        from thyca.tools.registry import ToolRegistry
        from thyca.memories.identity import bind_bot_id, reset_bot_id
        from thyca.bots.admin_tools import AdminDeps as _AD
        deps = _AD(app._bots, app._runtime, app.job_facade, room_bots=app.room_bots, room_dm_owner=app._room_dm_owner)
        tool_reg = ToolRegistry()
        register_admin_tools(tool_reg, deps)
        spec = tool_reg.get("bot_delegate")
        assert spec is not None
        tok = bind_bot_id("thyca")
        try:
            out = asyncio.run(spec.handler({"bot_id": "coder", "task": "do work", "room": room}))
        finally:
            reset_bot_id(tok)
        assert "member" in out.lower() and "sender" in out.lower(), (
            f"nonmember sender must be rejected as nonmember, got {out!r}")
        room2 = app.create(bots=["coder", "thyca"])["conversation_id"]
        deps2 = _AD(app._bots, app._runtime, app.job_facade, room_bots=app.room_bots, room_dm_owner=app._room_dm_owner)
        tool_reg2 = ToolRegistry()
        register_admin_tools(tool_reg2, deps2)
        spec2 = tool_reg2.get("bot_delegate")
        tok = bind_bot_id("thyca")
        try:
            out2 = asyncio.run(spec2.handler({"bot_id": "coder", "task": "do work", "room": room2}))
        finally:
            reset_bot_id(tok)
        assert "not a member" not in out2.lower(), f"member sender must pass pre-enqueue, got {out2!r}"
        tok = bind_bot_id("thyca")
        try:
            out3 = asyncio.run(spec2.handler({"bot_id": "coder", "task": "do work", "room": "group-" + "0" * 32}))
        finally:
            reset_bot_id(tok)
        assert "unknown" in out3.lower() or "failed" in out3.lower(), f"unknown must fail closed, got {out3!r}"
    finally:
        app.shutdown()


# T6 positive: an earlier real DM turn persists a unique private marker in
# Thyca's canonical WorkspaceTimeline; a later real DM turn delegates a
# chosen payload to a separate member group. The marker must reach the
# sender's later model input (positive control: canonical history feeds the
# prompt, so this fails if the marker was never persisted), must stay out
# of the recipient's model input (no automatic DM forwarding), and must
# stay out of the group transcript while the chosen request/reply publish.
# Driven entirely through app.turn on the DM with a recording fake
# provider: no direct _claimed call, no stubs bypassing model input.
def test_t6_dm_turn_delegate_to_group_publishes_only_chosen(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.conversations.runtime.manager import ConversationManager
    from thyca.core.protocol import Message, ToolCall
    from thyca.llms.llm_base import ChatReply
    from seeded_config import seeded_config
    from thyca.configs import load, save
    from thyca.app.chat_app import ChatApp

    CHOSEN = "chosen delegation payload DM2G-5K"
    DM_MARKER = "DM-PRIVATE-MARKER DM2G-8M"
    ESTABLISH = f"please remember this private note {DM_MARKER} for later"
    REPLY = "ECHO-DM2G-DONE"

    class _DmDelegateLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def chat(self, messages, tools=None):
            text = "\n".join(str(m.content or "") for m in messages)
            tool_text = "\n".join(
                str(m.content or "") for m in messages if m.role == "tool")
            seen = text + "\n---TOOL---\n" + tool_text
            self.prompts.append(seen)
            # The delegation tool result ends the sender flow exactly once:
            # check it BEFORE the trigger (which persists in history) so the
            # sender does not re-delegate in a loop.
            if any(getattr(m, "role", None) == "tool" for m in messages):
                return ChatReply(content="OUTER-DONE", model="m",
                                 usage={"prompt_tokens": 1, "completion_tokens": 1,
                                        "total_tokens": 2})
            if "please delegate chosen work to the group" in text:
                return ChatReply(content=None, model="m", tool_calls=[
                    ToolCall(id="t-delegate", name="bot_delegate",
                             arguments={"bot_id": "coder", "task": CHOSEN,
                                        "room": self.group_id}),
                ])
            if ESTABLISH in text:
                return ChatReply(content="MARKER-NOTED", model="m",
                                 usage={"prompt_tokens": 1, "completion_tokens": 1,
                                        "total_tokens": 2})
            if CHOSEN in text:
                return ChatReply(content=REPLY, model="m",
                                 usage={"prompt_tokens": 1, "completion_tokens": 1,
                                        "total_tokens": 2})
            return ChatReply(content="OUTER-DONE", model="m",
                             usage={"prompt_tokens": 1, "completion_tokens": 1,
                                    "total_tokens": 2})

    llm = _DmDelegateLLM()
    save(seeded_config(), tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
        # Resolve the DM first: bot_dm prunes blank conversations, so the group
        # must be created afterwards (and seeded non-blank).
        dm = app.bot_dm("thyca")["conversation_id"]
        room = app.create(bots=["coder", "thyca"])["conversation_id"]
        llm.group_id = room
        mgr = ConversationManager(store=app._conversations.store)
        mgr.load(room)
        mgr.append(Message(role="user", content="group seed", ts="2026-10-26T09:00:00Z"))
        # Earlier real DM turn: persist the unique private marker in
        # Thyca's canonical WorkspaceTimeline (not the public projection).
        first = app.turn(dm, ESTABLISH)
        assert "MARKER-NOTED" in str(first.get("reply", "")), (
            f"establishing turn must run, got {first!r}")
        # Later real DM turn: delegate the chosen payload to the group.
        detail = app.turn(dm, "please delegate chosen work to the group")
        assert detail.get("reply") == "OUTER-DONE", (
            f"sender turn must finish exactly once, got {detail!r}")
        # Sender positive control: the marker persisted canonically must
        # reach Thyca's later model input. Fails if the marker was absent.
        sender_prompts = [pr for pr in llm.prompts
                          if DM_MARKER in pr
                          and "please delegate chosen work to the group" in pr
                          and CHOSEN not in pr]
        assert sender_prompts, (
            "marker must reach the sender's later model input via history")
        # Recipient runs: exactly the prompt carrying the chosen payload
        # (and no DM marker) must exist; the flow cannot pass without it.
        recipient_prompts = [pr for pr in llm.prompts
                             if CHOSEN in pr and REPLY not in pr]
        assert recipient_prompts, "recipient turn must run with the chosen payload"
        assert all(DM_MARKER not in pr for pr in recipient_prompts), (
            "recipient model input must not contain the DM marker")
        # Group membership is valid and unchanged.
        assert sorted(app.room_bots(room)) == ["coder", "thyca"]
        mgr.load(room)
        grows = list(mgr.current.messages)
        requests = [m for m in grows
                    if (m.meta or {}).get("bus_event") is True and m.content == CHOSEN]
        assert len(requests) == 1, (
            f"exactly one chosen request must publish, got {len(requests)}")
        results = [m for m in grows
                   if (m.meta or {}).get("kind") == "result" and m.content == REPLY]
        assert len(results) == 1, (
            f"exactly one delegate reply must publish, got {len(results)}")
        assert all(DM_MARKER not in (m.content or "") for m in grows), (
            "private DM marker must never reach the group")
        # The DM transcript keeps its private marker.
        mgr.load(dm)
        drows = list(mgr.current.messages)
        assert any(DM_MARKER in (m.content or "") for m in drows), (
            "DM transcript must keep its private marker")
    finally:
        app.shutdown()


# T7: request rows need sender+recipient same-transaction; result stays recipient.
def test_t7_request_needs_sender_and_recipient_same_txn(tmp_path: Path) -> None:
    import uuid
    from thyca.conversations.store import AdminStore, RootInitializationIntent, RuntimeWriteIntent
    from thyca.conversations.store.contracts import ConversationStoreError
    from thyca.core.protocol import Message
    root_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(tmp_path.absolute())))
    AdminStore(tmp_path).initialize(RootInitializationIntent(root_id))
    group = AdminStore(tmp_path).create_group(["alice", "bob"]).ref
    from thyca.conversations.store import write_adapter as _wa
    import inspect as _inspect
    sig = _inspect.signature(_wa.append_message)
    assert "require_participant" in sig.parameters, "append path must expose same-txn extra participant check"
    with pytest.raises(ConversationStoreError):
        _wa.append_message(tmp_path, group, "mallory", Message(role="assistant", content="intruder", meta={"bot_id": "mallory"}),
                           intent=RuntimeWriteIntent("t"), require_participant="bob")
    AdminStore(tmp_path).delete(group)
    group2 = AdminStore(tmp_path).create_group(["alice", "bob"]).ref
    from thyca.conversations.store.write_adapter import set_conversation_metadata
    set_conversation_metadata(tmp_path, group2, intent=RuntimeWriteIntent("t"), actor="alice",
                              participants=["alice"])
    with pytest.raises(ConversationStoreError):
        _wa.append_message(tmp_path, group2, "alice", Message(role="assistant", content="hi", meta={"bot_id": "alice"}),
                           intent=RuntimeWriteIntent("t"), require_participant="bob")
    group3 = AdminStore(tmp_path).create_group(["alice", "bob"]).ref
    seq = _wa.append_message(tmp_path, group3, "alice", Message(role="assistant", content="req", meta={"bot_id": "alice"}),
                             intent=RuntimeWriteIntent("t"), require_participant="bob")
    assert isinstance(seq, int)
    seq2 = _wa.append_message(tmp_path, group3, "bob", Message(role="assistant", content="res", meta={"bot_id": "bob"}),
                              intent=RuntimeWriteIntent("t"))
    assert isinstance(seq2, int)


def _deliver_claimed(app, envelope, sender: str, room: str):
    # Leased entry point: holds the recipient workspace lease, then records
    # and runs the recipient turn. Direct claimed calls bypass the lease and
    # cannot run a real turn.
    import asyncio as _aio
    from thyca.bots.context import BotTurnContext, BotTurnScope
    scope = BotTurnScope(store=app._conversations.store, act=app._act, tools=app._tools,
                         memory=app._memory, state=app._state, zone=app._zone)
    ctx = BotTurnContext(sender_id=sender, room_id=room, scope=scope, turn_cfg=app._current_cfg())
    return _aio.run(app._deliver_envelope(envelope, ctx))


# T7 positive: real member publication records sender-stamped request + result.
def test_t7_real_publication_records_request_and_result(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.conversations.runtime.manager import ConversationManager
    app = _echo_app(tmp_path, reply_text="ECHO-7-DONE")
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
        room = app.create(bots=["coder", "helper"])["conversation_id"]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            conversation_id=room, payload="live request 7")
        reply = _deliver_claimed(app, envelope, "coder", room)
        assert reply == "ECHO-7-DONE", f"member delivery must reply, got {reply!r}"
        mgr = ConversationManager(store=app._conversations.store)
        mgr.load(room)
        rows = list(mgr.current.messages)
        req = [m for m in rows if (m.meta or {}).get("bus_event") is True and m.content == "live request 7"]
        assert len(req) == 1 and req[0].meta.get("bot_id") == "coder", (
            "request row must carry the actual sender stamp")
        assert any((m.meta or {}).get("kind") == "result" and m.content == "ECHO-7-DONE" for m in rows), (
            "result row must publish")
    finally:
        app.shutdown()


# T7 race: sender removed after enqueue but before request insert fails the row, reply still returns.
def test_t7_sender_removal_race_skips_request_but_returns_reply(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.conversations.runtime.manager import ConversationManager
    import thyca.conversations.store.write_adapter as _wa
    app = _echo_app(tmp_path, reply_text="ECHO-RACE-DONE")
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
        room = app.create(bots=["coder", "helper"])["conversation_id"]
        real_append = _wa.append_message
        removed: list = []

        def _removing_append(root, ref, actor, message, *, intent, **kw):
            meta = getattr(message, "meta", None) or {}
            if not removed and meta.get("bus_event") is True and message.content == "doomed request":
                removed.append(True)
                assert app.set_room_bots(room, "coder", "remove") == ["helper"]
            return real_append(root, ref, actor, message, intent=intent, **kw)

        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            conversation_id=room, payload="doomed request")
        import unittest.mock as _mock
        with _mock.patch.object(_wa, "append_message", _removing_append):
            reply = _deliver_claimed(app, envelope, "coder", room)
        assert reply == "ECHO-RACE-DONE", f"produced reply must still return, got {reply!r}"
        assert removed, "removal must win the race in this test"
        mgr = ConversationManager(store=app._conversations.store)
        mgr.load(room)
        rows = list(mgr.current.messages)
        assert all("doomed request" not in (m.content or "") for m in rows), "doomed row must be absent"
        assert [m for m in rows if (m.meta or {}).get("kind") == "result"] == [], (
            "no result without an eligible request row")
    finally:
        app.shutdown()


# T7 race: recipient removed after enqueue but before request insert fails the row.
def test_t7_recipient_removal_race_skips_request(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.conversations.runtime.manager import ConversationManager
    import thyca.conversations.store.write_adapter as _wa
    app = _echo_app(tmp_path, reply_text="ECHO-RACE2-DONE")
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C", description="D", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="H", description="E", persona="Q"))
        room = app.create(bots=["coder", "helper"])["conversation_id"]
        real_append = _wa.append_message
        removed: list = []

        def _removing_append(root, ref, actor, message, *, intent, **kw):
            meta = getattr(message, "meta", None) or {}
            if not removed and meta.get("bus_event") is True and message.content == "doomed request 2":
                removed.append(True)
                assert app.set_room_bots(room, "helper", "remove") == ["coder"]
            return real_append(root, ref, actor, message, intent=intent, **kw)

        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            conversation_id=room, payload="doomed request 2")
        import unittest.mock as _mock
        with _mock.patch.object(_wa, "append_message", _removing_append):
            reply = _deliver_claimed(app, envelope, "coder", room)
        assert reply == "ECHO-RACE2-DONE", f"produced reply must still return, got {reply!r}"
        assert removed, "removal must win the race in this test"
        mgr = ConversationManager(store=app._conversations.store)
        mgr.load(room)
        rows = list(mgr.current.messages)
        assert all("doomed request 2" not in (m.content or "") for m in rows), "doomed row must be absent"
    finally:
        app.shutdown()
