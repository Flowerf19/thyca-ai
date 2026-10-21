"""profile_update tool: Thyca-only write side of the aggregation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from thyca.memory.facade import MemoryFacade
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.memory.profile import profile_block_lines
from thyca.tools.profile_tools import profile_update_spec, register_profile_tools
from thyca.tools.registry import ToolRegistry


def test_profile_update_denied_for_child_bot(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path)
    spec = profile_update_spec(facade)
    token = bind_bot_id("coder")
    try:
        out = asyncio.run(spec.handler({"lines": ["x"]}))
    finally:
        reset_bot_id(token)
    assert "denied" in out


def test_profile_update_rejects_bad_args(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path)
    spec = profile_update_spec(facade)
    assert "invalid lines" in asyncio.run(spec.handler({}))
    assert "invalid lines" in asyncio.run(spec.handler({"lines": "not a list"}))
    assert "invalid lines" in asyncio.run(spec.handler({"lines": [1, 2]}))


def test_profile_update_writes_block_keeps_manual(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path)
    (tmp_path / "USER.md").write_text("# User\n- manual line\n", encoding="utf-8")
    spec = profile_update_spec(facade)
    out = asyncio.run(spec.handler({"lines": ["uong ca phe den"]}))
    payload = json.loads(out)
    assert payload["ok"] is True
    text = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert profile_block_lines(text) == ["uong ca phe den"]
    assert "- manual line" in text


def test_register_profile_tools_idempotent(tmp_path: Path) -> None:
    registry = ToolRegistry()
    facade = MemoryFacade(tmp_path)
    register_profile_tools(registry, facade)
    register_profile_tools(registry, facade)
    assert registry.get("profile_update") is not None
    assert len(registry.to_openai_schema()) == 1