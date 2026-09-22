"""Lifecycle: chunked NDJSON decode. No jsdom.

Drives webui/shared/js/api.js (postNdjson) with stubbed fetch.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "thyca" / "webui" / "shared" / "js" / "api.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


_PREAMBLE = f"""
import {{ postNdjson }} from '{API.as_posix()}';
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
        + " return { types: events.map((e) => e.type) }; })()",
    )
    assert result["types"] == [
        "turn.accepted",
        "llm.started",
        "llm.finished",
        "turn.completed",
    ]


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


def test_skill_events_pass_through(node: str) -> None:
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
        + " return { types: events.map((e) => e.type) }; })()",
    )
    assert result["types"] == [
        "turn.accepted",
        "skill.started",
        "skill.finished",
        "turn.completed",
    ]


def test_thinking_deltas_pass_through_ndjson(node: str) -> None:
    raw = (
        '{"type":"turn.accepted"}\n'
        '{"type":"llm.started","round":1}\n'
        '{"type":"llm.thinking","round":1,"delta":"First I check"}\n'
        '{"type":"llm.finished","round":1,"tool_count":0}\n'
        '{"type":"turn.completed","detail":{"id":"s"}}\n'
    )
    payload = json.dumps(raw)
    result = _run(
        node,
        "(async () => {"
        + f" const events = await decode({payload});"
        + " return { types: events.map((e) => e.type), deltas: events.filter((e) => e.type === 'llm.thinking').map((e) => e.delta) }; })()",
    )
    assert result["types"] == [
        "turn.accepted",
        "llm.started",
        "llm.thinking",
        "llm.finished",
        "turn.completed",
    ]
    assert result["deltas"] == ["First I check"]
