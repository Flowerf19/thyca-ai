"""Composer usage meter — last-turn fresh/cache/out/ctx/cost under the chat box."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"
METER_JS = WEBUI / "js" / "chat" / "meter.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expr: str) -> object:
    script = f"""
    import * as m from {json.dumps(METER_JS.as_uri())};
    process.stdout.write(JSON.stringify({expr}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_sum_last_turn_takes_final_slice_only(node: str) -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "yo",
            "meta": {
                "usage": {"prompt_tokens": 1000, "cached_tokens": 990},
                "cost_usd": 0.0001,
            },
        },
        {"role": "user", "content": "hi2"},
        {
            "role": "assistant",
            "content": "yo2",
            "meta": {
                "usage": {"prompt_tokens": 70607522, "cached_tokens": 70000000},
                "cost_usd": 0.012345,
            },
        },
    ]
    summary = _eval(node, f"m.sumLastTurnUsage({json.dumps(messages)})")
    assert summary == {
        "prompt": 70607522,
        "cached": 70000000,
        "fresh": 607522,
        "completion": 0,
        "ctx": 70607522,
        "cost": pytest.approx(0.012345),
    }


def test_sum_last_turn_empty_or_no_usage_is_null(node: str) -> None:
    assert _eval(node, "m.sumLastTurnUsage([])") == {
        "prompt": None,
        "cached": None,
        "fresh": None,
        "completion": None,
        "ctx": None,
        "cost": None,
    }
    only_user = _eval(node, 'm.sumLastTurnUsage([{role:"user",content:"x"}])')
    assert only_user["fresh"] is None
    no_meta = _eval(
        node, 'm.sumLastTurnUsage([{role:"user",content:"x"},{role:"assistant",content:"y"}])'
    )
    assert no_meta["fresh"] is None
    assert no_meta["completion"] is None
    assert no_meta["ctx"] is None


def test_sum_last_turn_out_and_ctx_skips_naming(node: str) -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "r1",
            "meta": {
                "kind": "llm",
                "usage": {
                    "prompt_tokens": 1000,
                    "cached_tokens": 100,
                    "completion_tokens": 50,
                },
                "cost_usd": 0.001,
            },
        },
        {
            "role": "assistant",
            "content": "r2",
            "meta": {
                "kind": "llm",
                "usage": {
                    "prompt_tokens": 128000,
                    "cached_tokens": 120000,
                    "completion_tokens": 30,
                },
                "cost_usd": 0.002,
            },
        },
        {
            "role": "assistant",
            "content": None,
            "meta": {
                "kind": "naming",
                "usage": {
                    "prompt_tokens": 200,
                    "cached_tokens": 0,
                    "completion_tokens": 8,
                },
                "cost_usd": 0.0001,
            },
        },
    ]
    summary = _eval(node, f"m.sumLastTurnUsage({json.dumps(messages)})")
    assert summary == {
        "prompt": 129200,
        "cached": 120100,
        "fresh": 9100,
        "completion": 88,
        "ctx": 128000,
        "cost": pytest.approx(0.0031),
    }


def test_meter_text_compact_and_title_full(node: str) -> None:
    summary = {
        "prompt": 70607522,
        "cached": 70000000,
        "fresh": 607522,
        "completion": 82200,
        "ctx": 128000,
        "cost": 0.012345,
    }
    assert _eval(node, f"m.meterText({json.dumps(summary)})") == (
        "input 607.5K · cache 70M · output 82.2K · context 128K · cost $0,0123"
    )
    assert _eval(node, f"m.meterTitle({json.dumps(summary)})") == (
        "lượt vừa rồi — input 607.522 · cache 70.000.000 · output 82.200 · context 128.000 · cost $0,0123"
    )
    assert _eval(node, "m.meterText({fresh: null})") == ""


def test_meter_text_hides_cache_badge_when_zero(node: str) -> None:
    summary = {
        "prompt": 5000,
        "cached": 0,
        "fresh": 5000,
        "completion": 10,
        "ctx": 5000,
        "cost": 0.001,
    }
    assert _eval(node, f"m.meterText({json.dumps(summary)})") == (
        "input 5.000 · output 10 · context 5.000 · cost $0,0010"
    )
    assert _eval(node, f"m.meterTitle({json.dumps(summary)})") == (
        "lượt vừa rồi — input 5.000 · output 10 · context 5.000 · cost $0,0010"
    )
    zero_out = {**summary, "completion": 0}
    assert _eval(node, f"m.meterText({json.dumps(zero_out)})") == (
        "input 5.000 · context 5.000 · cost $0,0010"
    )
    assert _eval(node, f"m.meterTitle({json.dumps(zero_out)})") == (
        "lượt vừa rồi — input 5.000 · context 5.000 · cost $0,0010"
    )


def test_meter_wired_in_dom_and_composer_meta() -> None:
    dom = (WEBUI / "js" / "shared" / "dom.js").read_text(encoding="utf-8")
    assert 'meter: document.getElementById("meter")' in dom
    html = (WEBUI / "index.html").read_text(encoding="utf-8")
    assert 'id="meter"' in html
    assert 'id="tool-meter"' not in html
    assert 'id="new-page"' in html
    assert "composer-meta" in html
    # meter reuses .hint — no new design system
    assert 'class="hint" id="meter"' in html
    chat_index = (WEBUI / "js" / "chat" / "index.js").read_text(encoding="utf-8")
    assert "renderComposerMeter" in chat_index
    render = (WEBUI / "js" / "render.js").read_text(encoding="utf-8")
    assert "renderComposerMeter(el.meter" in render
    turn = (WEBUI / "js" / "chat" / "turn.js").read_text(encoding="utf-8")
    assert "renderComposerMeter(el.meter, completed.messages)" in turn


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
