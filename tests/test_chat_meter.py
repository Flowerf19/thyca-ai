"""Number formatting for the new UI — webui/backend/format.js.

The old composer usage meter (webui/js/chat/meter.js: sumLastTurnUsage,
meterText, lastTurnTools, #meter/#tool-meter DOM) was deliberately dropped in
the new-UI migration: usage now lives on the dashboard/usage screen backed
by /api/traces aggregation (see tests/test_webui_markdown.py). This file pins
that decision and covers the replacement formatters (formatCompact,
formatInteger, formatCost) plus the backend usage-meta contract that feeds
them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NEW_UI = ROOT / "thyca" / "webui"
FORMAT_JS = NEW_UI / "backend" / "format.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import * as m from '{FORMAT_JS.as_posix()}';\n"
        f"console.log(JSON.stringify({expression}));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_composer_meter_is_dropped_in_new_ui() -> None:
    haystacks = [
        (NEW_UI / name).read_text(encoding="utf-8")
        for name in ("app.js", "index.html", "cost.js", "usage.js")
    ] + [
        (NEW_UI / "backend" / name).read_text(encoding="utf-8")
        for name in ("api.js", "chat-view.js", "analytics-data.js", "format.js")
    ]
    blob = "\n".join(haystacks)
    assert "sumLastTurnUsage" not in blob
    assert "lastTurnTools" not in blob
    assert "renderComposerMeter" not in blob
    assert 'id="meter"' not in blob
    assert 'id="tool-meter"' not in blob


def test_format_compact_matches_old_meter_scale(node: str) -> None:
    assert _eval(node, "m.formatCompact(70000000)") == "70M"
    assert _eval(node, "m.formatCompact(128000)") == "128K"
    assert _eval(node, "m.formatCompact(5000)") == "5.000"
    assert _eval(node, "m.formatCompact(0)") == "0"
    assert _eval(node, "m.formatCompact('x')") == "—"


def test_format_cost_keeps_usd_precision(node: str) -> None:
    assert _eval(node, "m.formatCost(0.012345)") == "$0.012345"
    assert _eval(node, "m.formatCost(0)") == "$0.0000"
    assert _eval(node, "m.formatCost(null)") == "—"
    assert _eval(node, "m.formatCost('')") == "—"


def test_format_integer_uses_vi_locale(node: str) -> None:
    assert _eval(node, "m.formatInteger(70607522)") == "70.607.522"
    assert _eval(node, "m.formatInteger(5000)") == "5.000"
    assert _eval(node, "m.formatInteger('x')") == "—"


def test_session_detail_carries_meta_for_meter(tmp_path: Path) -> None:
    from test_serve_chat import FakeLLM, _chat

    from thyca.llm.llm_base import ChatReply

    llm = FakeLLM(
        ChatReply(
            content="pong",
            usage={"prompt_tokens": 100, "cached_tokens": 20, "completion_tokens": 5},
        )
    )
    app = _chat(tmp_path, llm)
    try:
        created = app.create()
        turned = app.turn(created["id"], "ping")
        assistants = [m for m in turned["messages"] if m["role"] == "assistant"]
        assert assistants and assistants[0]["meta"]["usage"]["prompt_tokens"] == 100
        loaded = app.get_payload(created["id"])
        assert loaded["messages"] == turned["messages"]
        assert loaded["messages"][1]["meta"]["usage"]["cached_tokens"] == 20
    finally:
        app.shutdown()
