"""Trace detail mapping — webui/backend/trace-data.js.

The old replay helper (webui/js/trace/score.js::traceScoreFromEvents) was not
ported: the new trace screen reads tool calls straight from the trace detail
payload via toolsFromDetail, groups session turns via groupTraceTurns, and
prices tokens via tokenCost. These tests cover that replacement contract.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRACE_DATA = ROOT / "thyca" / "webui" / "backend" / "trace-data.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ toolsFromDetail, groupTraceTurns, selectedModelConfig, tokenCost,"
        f" firstUserText, finalAssistantText }} from '{TRACE_DATA.as_posix()}';\n"
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


def test_skill_call_keeps_skill_prefix(node: str) -> None:
    detail = {
        "messages": [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "name": "read", "skill": "codereview"},
                    {"id": "c2", "name": "read"},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "out1",
             "meta": {"latency_ms": 120}},
            {"role": "tool", "tool_call_id": "c2", "content": "out2",
             "meta": {"latency_ms": 5}},
            {"role": "assistant", "content": "done"},
        ]
    }
    tools = _eval(node, f"toolsFromDetail({json.dumps(detail)})")
    assert tools == [
        {"id": "c1", "name": "skill:codereview", "output": "out1", "latencyMs": 120},
        {"id": "c2", "name": "read", "output": "out2", "latencyMs": 5},
    ]


def test_tool_without_result_has_null_output(node: str) -> None:
    detail = {
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "name": "bash"}]},
            {"role": "assistant", "content": "done"},
        ]
    }
    tools = _eval(node, f"toolsFromDetail({json.dumps(detail)})")
    assert tools == [{"id": "c1", "name": "bash", "output": None, "latencyMs": None}]


def test_missing_tool_name_falls_back_to_tool(node: str) -> None:
    detail = {
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        ]
    }
    tools = _eval(node, f"toolsFromDetail({json.dumps(detail)})")
    assert tools[0]["name"] == "tool"


def test_group_trace_turns_sorts_and_sums(node: str) -> None:
    rows = [
        {"session_id": "s1", "title": "  Demo  ", "turn_index": 1,
         "started_at": "2026-09-08T10:01:00", "ended_at": "2026-09-08T10:01:05",
         "total_tokens": 30, "latency_ms": 50, "cost_usd": 0.00001},
        {"session_id": "s1", "title": "Demo", "turn_index": 0,
         "started_at": "2026-09-08T10:00:00", "ended_at": "2026-09-08T10:00:04",
         "total_tokens": 24, "latency_ms": 40, "cost_usd": 0.000006},
        {"session_id": "s2", "title": "Older", "turn_index": 0,
         "started_at": "2026-09-07T09:00:00", "ended_at": "2026-09-07T09:00:02",
         "total_tokens": 10, "latency_ms": 20, "cost_usd": None},
        {"title": "no session", "turn_index": 0},
    ]
    groups = _eval(node, f"groupTraceTurns({json.dumps(rows)})")
    assert [g["sessionId"] for g in groups] == ["s1", "s2"]
    first = groups[0]
    assert first["title"] == "Demo"
    assert [t["turn_index"] for t in first["turns"]] == [0, 1]
    assert first["totalTokens"] == 54
    assert first["latencyMs"] == 90
    assert first["costUsd"] == pytest.approx(0.000016)
    assert groups[1]["costUsd"] is None


def test_token_cost_prices_per_million(node: str) -> None:
    assert _eval(node, "tokenCost(1000000, 2.5)") == pytest.approx(2.5)
    assert _eval(node, "tokenCost(500000, 10)") == pytest.approx(5.0)
    assert _eval(node, "tokenCost('x', 2.5)") is None
    assert _eval(node, "tokenCost(100, undefined)") is None
    # NOTE: Number(null) === 0 nên rate null cho ra 0, không phải null.
    # Trường hợp này không xảy ra thực tế (thiếu giá → undefined → null).


def test_selected_model_config_prefers_models_over_pricing(node: str) -> None:
    values = {
        "models": {"gpt-4o-mini": {"input": 1, "baseUrl": "https://x"}},
        "pricing": {"gpt-4o-mini": {"input": 2}},
    }
    assert _eval(
        node, f"selectedModelConfig({json.dumps(values)}, 'gpt-4o-mini')"
    ) == {"input": 1, "baseUrl": "https://x"}
    assert (
        _eval(node, "selectedModelConfig({pricing: {m: {input: 2}}}, 'm')")
        == {"input": 2}
    )
    assert _eval(node, "selectedModelConfig({}, 'unknown')") is None


def test_first_and_final_text(node: str) -> None:
    detail = {
        "messages": [
            {"role": "assistant", "content": "first assistant"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "  "},
            {"role": "assistant", "content": "final answer"},
        ]
    }
    assert _eval(node, f"firstUserText({json.dumps(detail)})") == "hello"
    assert _eval(node, f"finalAssistantText({json.dumps(detail)})") == "final answer"
    assert _eval(node, "firstUserText({messages: []})") == (
        "Không có nội dung user trong trace."
    )
