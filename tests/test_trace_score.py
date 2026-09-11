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
        f"import {{ toolsFromDetail, groupToolCalls, groupTraceTurns, selectedModelConfig,"
        f" tokenCost, firstUserText, finalAssistantText, formatRecordText, asArguments }}"
        f" from '{TRACE_DATA.as_posix()}';\n"
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


def test_skill_call_keeps_raw_name_and_skill(node: str) -> None:
    detail = {
        "messages": [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "name": "read", "skill": "codereview",
                     "arguments": {"path": "SKILL.md"}},
                    {"id": "c2", "name": "read", "arguments": {"path": "notes.md"}},
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
        {"id": "c1", "name": "read", "skill": "codereview",
         "arguments": {"path": "SKILL.md"}, "output": "out1", "latencyMs": 120},
        {"id": "c2", "name": "read", "skill": None,
         "arguments": {"path": "notes.md"}, "output": "out2", "latencyMs": 5},
    ]


def test_string_arguments_are_parsed(node: str) -> None:
    detail = {
        "messages": [
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "name": "bash",
                             "arguments": "{\"command\": \"ls\"}"}]},
        ]
    }
    tools = _eval(node, f"toolsFromDetail({json.dumps(detail)})")
    assert tools[0]["arguments"] == {"command": "ls"}


def test_format_record_text_shows_tool_input(node: str) -> None:
    assert _eval(node, 'formatRecordText({limit: 3})') == "limit: 3"
    assert _eval(
        node, "formatRecordText({command: 'echo ok'})"
    ) == "command: echo ok"
    assert _eval(node, 'formatRecordText({})') == "—"
    assert _eval(node, 'asArguments("{\\"limit\\": 3}")') == {"limit": 3}
    detail = {
        "messages": [
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "name": "memory_recent",
                             "arguments": {"limit": 3}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
    }
    tools = _eval(node, f"toolsFromDetail({json.dumps(detail)})")
    assert _eval(
        node, f"formatRecordText({json.dumps(tools[0]['arguments'])})"
    ) == "limit: 3"
    assert tools[0]["arguments"]["limit"] == 3


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
    assert tools == [
        {"id": "c1", "name": "bash", "skill": None,
         "arguments": {}, "output": None, "latencyMs": None}
    ]


def test_missing_tool_name_falls_back_to_tool(node: str) -> None:
    detail = {
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        ]
    }
    tools = _eval(node, f"toolsFromDetail({json.dumps(detail)})")
    assert tools[0]["name"] == "tool"


def test_group_tool_calls_keeps_first_seen_order(node: str) -> None:
    tools = [
        {"id": "a1", "name": "memory_recent", "skill": None,
         "arguments": {"limit": 3}, "output": "m1", "latencyMs": 4},
        {"id": "b1", "name": "bash", "skill": None,
         "arguments": {"command": "ls"}, "output": "o1", "latencyMs": 10},
        {"id": "a2", "name": "memory_recent", "skill": None,
         "arguments": {"limit": 5}, "output": "m2", "latencyMs": 6},
        {"id": "b2", "name": "bash", "skill": None,
         "arguments": {"command": "pwd"}, "output": "o2", "latencyMs": None},
    ]
    groups = _eval(node, f"groupToolCalls({json.dumps(tools)})")
    assert [g["name"] for g in groups] == ["memory_recent", "bash"]
    assert [g["kind"] for g in groups] == ["tool", "tool"]
    assert [g["count"] for g in groups] == [2, 2]
    assert groups[0]["latencyMs"] == 10
    assert groups[1]["latencyMs"] == 10
    assert [c["order"] for c in groups[0]["calls"]] == [1, 2]
    assert groups[1]["calls"][1] == {
        "order": 2, "id": "b2", "arguments": {"command": "pwd"},
        "output": "o2", "latencyMs": None,
    }


def test_group_tool_calls_separates_skill_loads(node: str) -> None:
    tools = [
        {"id": "s1", "name": "read", "skill": "create-skill",
         "arguments": {"path": "SKILL.md"}, "output": "x", "latencyMs": 2},
        {"id": "r1", "name": "read", "skill": None,
         "arguments": {"path": "notes.md"}, "output": "y", "latencyMs": 3},
        {"id": "s2", "name": "read", "skill": "code-reviewer",
         "arguments": {"path": "SKILL.md"}, "output": "z", "latencyMs": 1},
    ]
    groups = _eval(node, f"groupToolCalls({json.dumps(tools)})")
    assert [(g["name"], g["kind"], g["count"]) for g in groups] == [
        ("create-skill", "skill", 1),
        ("read", "tool", 1),
        ("code-reviewer", "skill", 1),
    ]
    assert groups[0]["calls"][0]["order"] == 1


def test_group_tool_calls_without_rows_or_latency(node: str) -> None:
    assert _eval(node, "groupToolCalls([])") == []
    assert _eval(node, "groupToolCalls(undefined)") == []
    groups = _eval(node, "groupToolCalls([{id: 'b1', name: 'bash', latencyMs: null}])")
    assert groups[0]["latencyMs"] is None
    assert groups[0]["calls"][0]["arguments"] == {}


def test_missing_latency_is_null_but_zero_stays_zero(node: str) -> None:
    """A call without latency_ms must not print "0ms" (Number(null) === 0)."""

    def detail(meta: dict) -> dict:
        return {
            "messages": [
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "name": "bash"}]},
                {"role": "tool", "tool_call_id": "c1", "content": "ok", "meta": meta},
            ]
        }

    assert _eval(node, f"toolsFromDetail({json.dumps(detail({'latency_ms': 0}))})")[0][
        "latencyMs"
    ] == 0
    assert _eval(node, f"toolsFromDetail({json.dumps(detail({}))})")[0]["latencyMs"] is None
    assert _eval(node, f"toolsFromDetail({json.dumps(detail({'latency_ms': -5}))})")[0][
        "latencyMs"
    ] is None
    groups = _eval(node, f"groupToolCalls(toolsFromDetail({json.dumps(detail({}))}))")
    assert groups[0]["latencyMs"] is None
    zero = _eval(node, f"groupToolCalls(toolsFromDetail({json.dumps(detail({'latency_ms': 0}))}))")
    assert zero[0]["latencyMs"] == 0


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
