from __future__ import annotations

from pathlib import Path

import pytest

from thyca.core.protocol import ToolCall
from thyca.tools.builtin import register_file_tools
from thyca.tools.path_guard import PathDenied, PathGuard
from thyca.tools.gateway import ToolGateway
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def _gateway(root: Path) -> ToolGateway:
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root))
    return ToolGateway(registry, TaskStore())


async def _call(gateway: ToolGateway, name: str, **arguments):
    return await gateway.submit(ToolCall(id="t1", name=name, arguments=arguments))


@pytest.mark.asyncio
async def test_write_read_allow_persona_and_outside(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    outside = tmp_path / "work" / "note.txt"
    soul = tmp_path / "SOUL.md"
    ok = await _call(gateway, "write", path=str(soul), content="# Soul\nThyca\n")
    assert not ok.is_error
    assert (await _call(gateway, "read", path=str(soul))).content == "# Soul\nThyca\n"
    written = await _call(gateway, "write", path=str(outside), content="hi")
    assert not written.is_error
    assert outside.read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_write_denies_l2_session_config_sqlite(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "sessions").mkdir()
    denied = [
        tmp_path / "memory" / "2026-08-20.md",
        tmp_path / "MEMORY.md",
        tmp_path / "sessions" / "s.jsonl",
        tmp_path / "memory.sqlite",
        tmp_path / "memory.sqlite-wal",
    ]
    for path in denied:
        result = await _call(gateway, "write", path=str(path), content="x")
        assert result.is_error, path
        assert "write denied" in result.content
        assert not path.exists()


@pytest.mark.asyncio
async def test_write_allows_config_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    result = await _call(_gateway(tmp_path), "write", path=str(path), content='{"ok":true}\n')
    assert not result.is_error
    assert path.read_text(encoding="utf-8") == '{"ok":true}\n'


@pytest.mark.asyncio
async def test_read_can_open_l2(tmp_path: Path) -> None:
    daily = tmp_path / "memory" / "2026-08-20.md"
    daily.parent.mkdir()
    daily.write_text("leaf\n", encoding="utf-8")
    result = await _call(_gateway(tmp_path), "read", path=str(daily))
    assert not result.is_error
    assert result.content == "leaf\n"


@pytest.mark.asyncio
async def test_symlink_into_l2_is_denied(tmp_path: Path) -> None:
    daily = tmp_path / "memory" / "2026-08-20.md"
    daily.parent.mkdir()
    daily.write_text("secret\n", encoding="utf-8")
    link = tmp_path / "escape.md"
    link.symlink_to(daily)
    result = await _call(_gateway(tmp_path), "write", path=str(link), content="hack")
    assert result.is_error
    assert daily.read_text(encoding="utf-8") == "secret\n"


@pytest.mark.asyncio
async def test_edit_unique_ok_mismatch_and_overlap_do_not_write(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    path = tmp_path / "USER.md"
    path.write_text("alpha beta alpha\n", encoding="utf-8")
    multi = await _call(
        gateway,
        "edit",
        path=str(path),
        edits=[{"oldText": "alpha", "newText": "A"}],
    )
    assert multi.is_error
    assert path.read_text(encoding="utf-8") == "alpha beta alpha\n"

    missing = await _call(
        gateway,
        "edit",
        path=str(path),
        edits=[{"oldText": "zzz", "newText": "Z"}],
    )
    assert missing.is_error

    path.write_text("abcdef\n", encoding="utf-8")
    overlap = await _call(
        gateway,
        "edit",
        path=str(path),
        edits=[
            {"oldText": "abc", "newText": "X"},
            {"oldText": "bcd", "newText": "Y"},
        ],
    )
    assert overlap.is_error
    assert path.read_text(encoding="utf-8") == "abcdef\n"

    ok = await _call(
        gateway,
        "edit",
        path=str(path),
        edits=[{"oldText": "abc", "newText": "X"}],
    )
    assert not ok.is_error
    assert path.read_text(encoding="utf-8") == "Xdef\n"


def test_path_guard_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    guard = PathGuard(tmp_path / ".thyca")
    (tmp_path / ".thyca" / "memory").mkdir(parents=True)
    with pytest.raises(PathDenied):
        guard.deny_write("~/.thyca/memory/2026-08-20.md")
    assert guard.deny_write("~/.thyca/SOUL.md") == (tmp_path / ".thyca" / "SOUL.md").resolve()


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import pytest
from pathlib import Path

def test_x14_absolutize_unifies_identities(tmp_path: Path) -> None:
    from thyca.tools.path_guard import PathGuard, absolutize

    assert absolutize("/a/../b") == Path("/b")
    assert absolutize("/a/") == Path("/a")
    assert str(absolutize("~")).startswith("/")
    guard = PathGuard(tmp_path)
    assert guard.resolve("/a/../b") == Path("/b")
    assert guard.resolve("rel/x") == (Path.cwd() / "rel/x").resolve()


def test_m3_empty_old_text_rejected() -> None:
    from thyca.tools.builtin.edit import apply_edits

    with pytest.raises(ValueError, match="oldText must be non-empty"):
        apply_edits("", [{"oldText": "", "newText": "x"}])
    with pytest.raises(ValueError, match="oldText must be non-empty"):
        apply_edits("abc", [{"oldText": "", "newText": "x"}])


# Moved from tests/test_b2_contracts.py (B2 batch).
from thyca.tools.builtin.write import write_spec

def test_f36_write_description_matches_allow_behavior(tmp_path: Path) -> None:
    """Fails pre-fix: description claimed config was denied (it is allowed)."""
    denied, _, allowed = write_spec(PathGuard(tmp_path)).description.partition(
        "Allowed:"
    )
    assert "config" not in denied
    assert "config.json" in allowed
    assert "Denied: L2 daily, leftover MEMORY.md, sessions, sqlite." in denied


def test_file_specs_share_guard_key(tmp_path: Path) -> None:
    """read/write/edit lock on one identity: PathGuard.key of the same path."""
    registry = ToolRegistry()
    guard = PathGuard(tmp_path)
    register_file_tools(registry, guard)
    args = {"path": str(tmp_path / "note.txt")}
    keys = []
    for name in ("read", "write", "edit"):
        spec = registry.get(name)
        assert spec is not None and spec.resource_key is not None
        keys.append(spec.resource_key(args))
    assert keys[0] == keys[1] == keys[2] == guard.key(args)
    assert guard.key(args) == str(guard.resolve(args["path"]))
