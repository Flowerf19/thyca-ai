"""Node tests for traceScoreFromMessages — webui/js/trace-score.js."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "trace-score.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _score(node: str, messages: list[dict]) -> dict:
    source = (
        f"import {{ traceScoreFromMessages }} from '{SCRIPT.as_posix()}';\n"
        f"console.log(JSON.stringify(traceScoreFromMessages({json.dumps(messages)})));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def _ticks(measure: dict) -> int:
    return sum(item["duration"] for item in measure["events"] + measure["rests"])


def test_two_rounds_and_tools_complete_with_v_to_i(node: str) -> None:
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "name": "echo"}, {"id": "c2", "name": "files_read"}],
            "meta": {"kind": "llm", "round": 1},
        },
        {"role": "tool", "tool_call_id": "c1", "meta": {"round": 1}},
        {"role": "tool", "tool_call_id": "c2", "meta": {"round": 1, "is_error": True}},
        {"role": "assistant", "content": "done", "meta": {"kind": "llm", "round": 2}},
    ]
    score = _score(node, messages)
    assert score["key"] == "C"
    assert score["measures"][-1]["terminal"] == "completed"
    events = score["measures"][-1]["events"]
    assert [item["duration"] for item in events] == [8, 8]
    for measure in score["measures"]:
        assert _ticks(measure) == 16
    # recovered tool error still uses vii° on that slot, not failed cadence
    activity = [item["pitches"] for measure in score["measures"] if not measure["terminal"] for item in measure["events"]]
    assert ["B4", "D5", "F5"] in activity


def test_incomplete_turn_uses_failed_cadence(node: str) -> None:
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
    score = _score(node, messages)
    last = score["measures"][-1]
    assert last["terminal"] == "failed"
    assert last["events"] == [{"offset": 0, "duration": 16, "pitches": ["G4", "B4", "D5"]}]
