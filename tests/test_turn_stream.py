"""Lifecycle: chunked NDJSON → status text. No jsdom.

Drives webui/backend/api.js (postNdjson) with stubbed fetch and maps the
decoded events through webui/backend/chat-status.js (statusTextForEvent).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "thyca" / "webui" / "backend" / "api.js"
STATUS = ROOT / "thyca" / "webui" / "backend" / "chat-status.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


_PREAMBLE = f"""
import {{ postNdjson }} from '{API.as_posix()}';
import {{ statusTextForEvent }} from '{STATUS.as_posix()}';
async function decode(raw, splitAt = -1) {{
  const bytes = new TextEncoder().encode(raw);
  const parts = splitAt >= 0 ? [bytes.slice(0, splitAt), bytes.slice(splitAt)] : [bytes];
  let i = 0;
  globalThis.fetch = async () => ({{
    ok: true,
    body: {{ getReader: () => ({{
      read: async () => (i < parts.length ? {{ done: false, value: parts[i++] }} : {{ done: true, value: undefined }}),
    }}) }},
  }});
  const events = [];
  await postNdjson('/api/x', {{}}, (e) => events.push(e));
  return events;
}}
"""


def _run(node: str, expression: str) -> object:
    source = _PREAMBLE + f"console.log(JSON.stringify(await {expression}));\n"
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
        '{"type":"turn.accepted"}\n'
        '{"type":"llm.started","round":1}\n'
        '{"type":"llm.finished","round":1,"tool_count":0}\n'
        '{"type":"turn.completed","detail":{"id":"s"}}\n'
    )
    payload = json.dumps(raw)
    result = _run(
        node,
        "(async () => {"
        + f" const mid = Math.floor(new TextEncoder().encode({payload}).length / 2);"
        + f" const events = await decode({payload}, mid);"
        + " return { types: events.map((e) => e.type),"
        + " status: events.map((e) => statusTextForEvent(e)) }; })()",
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
    raw = '{"type":"turn.accepted"}\n{"type":"turn.failed","code":"llm_error","message":"x"}\n'
    payload = json.dumps(raw)
    result = _run(
        node,
        "(async () => { try {"
        + f" await decode({payload});"
        + " return 'no-throw'; } catch (error) {"
        + " return { message: error.message }; } })()",
    )
    assert result["message"] == "x"


def test_failed_stream_events_map_to_stopped(node: str) -> None:
    result = _run(
        node,
        """(async () => {
          const events = [
            { type: 'turn.accepted' },
            { type: 'turn.failed', code: 'llm_error', message: 'x' },
          ];
          return { status: events.map((e) => statusTextForEvent(e)) };
        })()""",
    )
    assert result["status"][-1] == "Lượt đã dừng."


def test_skill_events_change_status(node: str) -> None:
    raw = (
        '{"type":"turn.accepted"}\n'
        '{"type":"skill.started","round":1,"call_id":"call-1","name":"create-skill"}\n'
        '{"type":"skill.finished","round":1,"call_id":"call-1","name":"create-skill","ok":true}\n'
        '{"type":"turn.completed","detail":{"id":"s"}}\n'
    )
    payload = json.dumps(raw)
    result = _run(
        node,
        "(async () => {"
        + f" const events = await decode({payload});"
        + " return { status: events.map((e) => statusTextForEvent(e)) }; })()",
    )
    assert result["status"] == [
        "Đã nhận lượt…",
        "Đang mở skill create-skill…",
        "Đã mở skill create-skill…",
        "Đã xong.",
    ]
