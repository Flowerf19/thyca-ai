"""Node tests for traceScoreFromEvents — webui/js/trace/score.js."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "trace" / "score.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ skillNameForRead, traceScoreFromEvents }} from '{SCRIPT.as_posix()}';\n"
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


def _seq(node: str, messages: list[dict]) -> list[dict]:
    return _eval(node, f"traceScoreFromEvents({json.dumps(messages)})")


def test_skill_read_replays_as_skill_events(node: str) -> None:
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "name": "read", "skill": "codereview"},
                {"id": "c2", "name": "read"},
            ],
            "meta": {"round": 1},
        },
        {"role": "tool", "tool_call_id": "c1", "meta": {"round": 1}},
        {"role": "tool", "tool_call_id": "c2", "meta": {"round": 1}},
        {"role": "assistant", "content": "done", "meta": {"round": 2}},
    ]
    seq = _seq(node, messages)
    assert seq[3]["type"] == "skill.started" and seq[3]["name"] == "codereview"
    assert seq[4]["type"] == "skill.finished" and seq[4]["ok"] is True
    assert seq[5]["type"] == "tool.started" and seq[5]["name"] == "read"
    assert seq[6]["type"] == "tool.finished" and seq[6]["name"] == "read"
    assert seq[-1]["type"] == "turn.completed"


def test_skill_read_outside_thyca_skills_stays_tool(node: str) -> None:
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "name": "read"},
                {"id": "c2", "name": "bash"},
            ],
            "meta": {"round": 1},
        },
        {"role": "tool", "tool_call_id": "c1", "meta": {"round": 1}},
        {"role": "tool", "tool_call_id": "c2", "meta": {"round": 1}},
        {"role": "assistant", "content": "done", "meta": {"round": 2}},
    ]
    seq = _seq(node, messages)
    kinds = [item["type"] for item in seq if item["type"].startswith(("skill.", "tool."))]
    assert kinds == [
        "tool.started",
        "tool.finished",
        "tool.started",
        "tool.finished",
    ]


def test_incomplete_turn_is_failed(node: str) -> None:
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "name": "echo"}],
            "meta": {"round": 1},
        },
        {"role": "tool", "tool_call_id": "c1", "meta": {"is_error": True}},
    ]
    seq = _seq(node, messages)
    assert seq[-1]["type"] == "turn.failed"


def test_skill_replay_direct(node: str) -> None:
    result = _eval(
        node,
        """[
          skillNameForRead({name: "read", skill: "codereview"}),
          skillNameForRead({name: "read", skill: "create-mcp-tool"}),
          skillNameForRead({name: "read", skill: "Not Valid!"}),
          skillNameForRead({name: "read"}),
          skillNameForRead({name: "bash", skill: "codereview"}),
          skillNameForRead({skill: "codereview"}),
          skillNameForRead(null),
        ]""",
    )
    assert result == ["codereview", "create-mcp-tool", "Not Valid!", None, "codereview", "codereview", None]
