"""Lifecycle: chunked NDJSON → status text. No jsdom."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NDJSON = ROOT / "webui" / "js" / "shared" / "ndjson.js"
STATUS = ROOT / "webui" / "js" / "chat" / "status.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _run(node: str, expression: str) -> object:
    source = (
        f"import {{ createNdjsonDecoder }} from '{NDJSON.as_posix()}';\n"
        f"import {{ statusTextForEvent }} from '{STATUS.as_posix()}';\n"
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


def test_chunked_stream_status(node: str) -> None:
    raw = (
        '{"type":"turn.accepted"}\\n'
        '{"type":"llm.started","round":1}\\n'
        '{"type":"llm.finished","round":1,"tool_count":0}\\n'
        '{"type":"turn.completed","detail":{"id":"s"}}\\n'
    )
    result = _run(
        node,
        """(() => {
          const bytes = new TextEncoder().encode(%s);
          const d = createNdjsonDecoder();
          const mid = Math.floor(bytes.length / 2);
          const events = [...d.push(bytes.slice(0, mid)), ...d.push(bytes.slice(mid)), ...d.flush()];
          const status = events.map((e) => statusTextForEvent(e));
          return {
            types: events.map((e) => e.type),
            status,
          };
        })()"""
        % json.dumps(raw.replace("\\n", "\n")),
    )
    assert result["types"] == [
        "turn.accepted",
        "llm.started",
        "llm.finished",
        "turn.completed",
    ]
    assert result["status"][0] == "Đã nhận lượt…"
    assert result["status"][1] == "Đang xử lý vòng 1…"
    assert result["status"][-1] == "Đã xong."


def test_failed_stream_status(node: str) -> None:
    raw = '{"type":"turn.accepted"}\\n{"type":"turn.failed","code":"llm_error","message":"x"}\\n'
    result = _run(
        node,
        """(() => {
          const d = createNdjsonDecoder();
          const events = [...d.push(new TextEncoder().encode(%s)), ...d.flush()];
          return {
            status: events.map((e) => statusTextForEvent(e)),
          };
        })()"""
        % json.dumps(raw.replace("\\n", "\n")),
    )
    assert result["status"][-1] == "Lượt đã dừng."


def test_skill_events_change_status(node: str) -> None:
    raw = (
        '{"type":"turn.accepted"}\n'
        '{"type":"skill.started","round":1,"call_id":"call-1","name":"create-skill"}\n'
        '{"type":"skill.finished","round":1,"call_id":"call-1","name":"create-skill","ok":true}\n'
    )
    result = _run(
        node,
        """(() => {
          const d = createNdjsonDecoder();
          const events = [...d.push(new TextEncoder().encode(%s)), ...d.flush()];
          return {
            status: events.map((e) => statusTextForEvent(e)),
          };
        })()"""
        % json.dumps(raw),
    )
    assert result["status"] == [
        "Đã nhận lượt…",
        "Đang mở skill create-skill…",
        "Đã mở skill create-skill…",
    ]
