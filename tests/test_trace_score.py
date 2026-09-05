"""Node tests for traceScoreFromMessages — webui/js/trace/score.js."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "trace" / "score.js"
REPLAY = ROOT / "webui" / "js" / "staff" / "replay.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _score(node: str, messages: list[dict]) -> dict:
    return json.loads(_node_eval(node, messages))


def _seq(node: str, messages: list[dict]) -> list[dict]:
    """The replayed event sequence before scoring (pins skill.*/tool.* wiring)."""
    return json.loads(
        _node_eval(
            node,
            messages,
            extra_import="traceScoreFromEvents",
        )
    )


def _node_eval(node: str, messages: list[dict], extra_import: str | None = None) -> str:
    imports = f"import {{ traceScoreFromMessages }} from '{SCRIPT.as_posix()}';\n"
    call = "traceScoreFromMessages"
    if extra_import:
        imports += f"import {{ {extra_import} }} from '{SCRIPT.as_posix()}';\n"
        call = extra_import
    source = imports + f"console.log(JSON.stringify({call}({json.dumps(messages)})));\n"
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return result.stdout


def _ticks(measure: dict) -> int:
    return sum(item["duration"] for item in measure["events"] + measure["rests"])


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ skillNameForRead }} from '{REPLAY.as_posix()}';\n"
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


def test_skill_read_replays_as_skill_events(node: str) -> None:
    """Server marks skill loads; replay emits skill.* — matching live."""
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
    # Wiring must be pinned at the event level: sonority alone cannot tell
    # skill.* from tool.* (same densities by design).
    assert seq[3]["type"] == "skill.started" and seq[3]["name"] == "codereview"
    assert seq[4]["type"] == "skill.finished" and seq[4]["ok"] is True
    assert seq[5]["type"] == "tool.started" and seq[5]["name"] == "read"
    assert seq[6]["type"] == "tool.finished" and seq[6]["name"] == "read"
    score = _score(node, messages)
    assert score["measures"][-1]["terminal"] == "completed"
    pitches = [
        item["pitches"]
        for measure in score["measures"]
        if not measure["terminal"]
        for item in measure["events"]
    ]
    # The skill read must replay as a cue (single high note) followed by a
    # full triad — the same sonority tool.* would give, so assert the pair
    # appears anywhere (harmony depends on measure position).
    pair_found = any(
        len(pitches[i]) == 1 and len(pitches[i + 1]) == 3
        for i in range(len(pitches) - 1)
    )
    assert pair_found and len(pitches) == 9
    for measure in score["measures"]:
        if measure["terminal"]:
            continue
        assert _ticks(measure) == 16


def test_skill_read_outside_thyca_skills_stays_tool(node: str) -> None:
    """Calls without a server skill marker replay as plain tool.*."""
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
    score = _score(node, messages)
    pitches = [
        item["pitches"]
        for measure in score["measures"]
        if not measure["terminal"]
        for item in measure["events"]
    ]
    # Neither call is a skill load. Activity count = accepted + 2 llm pairs
    # (tool round + final text round) + 2 tool pairs = 9 slots; the assert
    # pins the count so a replay wrongly emitting skill.* (same sonority)
    # or extra events cannot hide — voicings vary by measure harmony.
    assert len(pitches) == 9
    for measure in score["measures"]:
        if measure["terminal"]:
            continue
        assert _ticks(measure) == 16


def test_skill_replay_direct(node: str) -> None:
    """The replay layer reads the server's skill marker without re-validating.

    The name is sanitized server-side at payload build (trace_tool_call), so
    the browser layer stays a dumb reader: any string marker passes, absence
    or non-string -> null (plain tool.*).
    """
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
    # The name is server-sanitized at payload build; non-string or absent
    # marker -> null. The browser layer stays a dumb reader by design.
    assert result == ["codereview", "create-mcp-tool", "Not Valid!", None, "codereview", "codereview", None]
