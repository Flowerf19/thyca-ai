from __future__ import annotations

from pathlib import Path

import pytest

from thyca.protocol import ToolCall
from thyca.tools.builtin import register_file_tools
from thyca.tools.path_guard import PathDenied, PathGuard
from thyca.tools.registry import ToolRegistry


def _registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root))
    return registry


async def _call(registry: ToolRegistry, name: str, **arguments):
    return await registry.dispatch(ToolCall(id="t1", name=name, arguments=arguments))


@pytest.mark.asyncio
async def test_write_read_allow_persona_and_outside(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    outside = tmp_path / "work" / "note.txt"
    soul = tmp_path / "SOUL.md"
    ok = await _call(registry, "write", path=str(soul), content="# Soul\nThyca\n")
    assert not ok.is_error
    assert (await _call(registry, "read", path=str(soul))).content == "# Soul\nThyca\n"
    written = await _call(registry, "write", path=str(outside), content="hi")
    assert not written.is_error
    assert outside.read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_write_denies_l2_session_config_sqlite(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
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
        result = await _call(registry, "write", path=str(path), content="x")
        assert result.is_error, path
        assert "write denied" in result.content
        assert not path.exists()


@pytest.mark.asyncio
async def test_write_allows_config_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    result = await _call(_registry(tmp_path), "write", path=str(path), content='{"ok":true}\n')
    assert not result.is_error
    assert path.read_text(encoding="utf-8") == '{"ok":true}\n'


@pytest.mark.asyncio
async def test_read_can_open_l2(tmp_path: Path) -> None:
    daily = tmp_path / "memory" / "2026-08-20.md"
    daily.parent.mkdir()
    daily.write_text("leaf\n", encoding="utf-8")
    result = await _call(_registry(tmp_path), "read", path=str(daily))
    assert not result.is_error
    assert result.content == "leaf\n"


@pytest.mark.asyncio
async def test_symlink_into_l2_is_denied(tmp_path: Path) -> None:
    daily = tmp_path / "memory" / "2026-08-20.md"
    daily.parent.mkdir()
    daily.write_text("secret\n", encoding="utf-8")
    link = tmp_path / "escape.md"
    link.symlink_to(daily)
    result = await _call(_registry(tmp_path), "write", path=str(link), content="hack")
    assert result.is_error
    assert daily.read_text(encoding="utf-8") == "secret\n"


@pytest.mark.asyncio
async def test_edit_unique_ok_mismatch_and_overlap_do_not_write(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    path = tmp_path / "USER.md"
    path.write_text("alpha beta alpha\n", encoding="utf-8")
    multi = await _call(
        registry,
        "edit",
        path=str(path),
        edits=[{"oldText": "alpha", "newText": "A"}],
    )
    assert multi.is_error
    assert path.read_text(encoding="utf-8") == "alpha beta alpha\n"

    missing = await _call(
        registry,
        "edit",
        path=str(path),
        edits=[{"oldText": "zzz", "newText": "Z"}],
    )
    assert missing.is_error

    path.write_text("abcdef\n", encoding="utf-8")
    overlap = await _call(
        registry,
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
        registry,
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
