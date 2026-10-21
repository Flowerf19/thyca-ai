"""NDJSON stream decoding — webui/ui/chat/js/chat.js (readNdjson).

The old decoder (webui/shared/js/streams.js -> api.js) is gone with the UI
rebuild. The new classic-script UI decodes inline: sendTurn/followRunningTurn
own the fetch + !ok check, and ``readNdjson(stream, onEvent)`` consumes the
response *body* — split lines, UTF-8 chunks, trailing flush. Per-line JSON
errors are skipped (one bad line must not kill a turn), and terminal-event
tracking moved up a layer: an unsettled stream is recovered by finishTurn(),
a turn.failed event is handled by handleStreamEvent (see test_turn_status.py).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHAT_JS = ROOT / "thyca" / "webui" / "ui" / "chat" / "js" / "chat.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


_PREAMBLE = f"""
import {{ loadScripts }} from './tests/webui_dom.mjs';
const {{ readNdjson }} = loadScripts(['{CHAT_JS.as_posix()}'], 'return {{ readNdjson }};');
const enc = (s) => new TextEncoder().encode(s);
const mkStream = (chunks) => {{
  let i = 0;
  return {{ getReader: () => ({{
    read: async () => (i < chunks.length ? {{ done: false, value: chunks[i++] }} : {{ done: true, value: undefined }}),
  }}) }};
}};
async function run(chunks) {{
  const events = [];
  await readNdjson(mkStream(chunks), (e) => events.push(e));
  return {{ events }};
}}
"""


def _eval(node: str, expression: str) -> object:
    source = _PREAMBLE + f"console.log(JSON.stringify(await {expression}));\n"
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_one_chunk_two_full_lines(node: str) -> None:
    out = _eval(
        node,
        "run([enc('{\"type\":\"turn.accepted\"}\\n{\"type\":\"llm.started\",\"round\":1}\\n'),"
        " enc('{\"type\":\"turn.completed\",\"detail\":{\"id\":\"x\"}}\\n')])",
    )
    assert [e["type"] for e in out["events"]] == [
        "turn.accepted",
        "llm.started",
        "turn.completed",
    ]
    # The terminal event's detail rides along for finishTurn(token, detail).
    assert out["events"][-1]["detail"] == {"id": "x"}


def test_line_split_across_two_chunks(node: str) -> None:
    out = _eval(
        node,
        "run([enc('{\"type\":\"turn.acc'),"
        " enc('epted\"}\\n{\"type\":\"turn.completed\",\"detail\":{\"id\":\"x\"}}\\n')])",
    )
    assert [e["type"] for e in out["events"]] == ["turn.accepted", "turn.completed"]


def test_utf8_split_across_multiple_bytes(node: str) -> None:
    out = _eval(
        node,
        """(async () => {
          const line = '{"type":"turn.accepted","note":"Đã nhận"}\\n';
          const tail = '{"type":"turn.completed","detail":{"id":"x"}}\\n';
          const bytes = new TextEncoder().encode(line);
          const cut = bytes.indexOf(new TextEncoder().encode('ậ')) + 1;
          return run([bytes.slice(0, cut), bytes.slice(cut), enc(tail)]);
        })()""",
    )
    assert out["events"][0] == {"type": "turn.accepted", "note": "Đã nhận"}


def test_flush_complete_line_without_trailing_newline(node: str) -> None:
    out = _eval(
        node,
        "run([enc('{\"type\":\"turn.completed\",\"detail\":{\"id\":\"x\"}}')])",
    )
    assert out["events"] == [{"type": "turn.completed", "detail": {"id": "x"}}]


def test_empty_lines_ignored(node: str) -> None:
    out = _eval(
        node,
        "run([enc('\\n  \\n{\"type\":\"turn.accepted\"}\\n\\n'),"
        " enc('{\"type\":\"turn.completed\",\"detail\":{\"id\":\"x\"}}\\n')])",
    )
    assert [e["type"] for e in out["events"]] == ["turn.accepted", "turn.completed"]


def test_malformed_json_line_is_skipped(node: str) -> None:
    """A bad line no longer kills the turn: the decoder skips it and keeps
    consuming — the stream stays live for the events that follow."""
    out = _eval(
        node,
        "run([enc('{\"type\":\"turn.accepted\"}\\n{\"type\": broken}\\n"
        "{\"type\":\"turn.completed\",\"detail\":{\"id\":\"x\"}}\\n')])",
    )
    assert [e["type"] for e in out["events"]] == ["turn.accepted", "turn.completed"]


def test_garbage_on_flush_yields_no_events(node: str) -> None:
    assert _eval(node, "run([enc('not-json')])") == {"events": []}


def test_stream_end_without_terminal_resolves(node: str) -> None:
    """readNdjson no longer throws on a missing terminal event — the caller
    settles the turn instead (sendTurn/followRunningTurn call finishTurn when
    token.phase never reached 'settled')."""
    assert _eval(node, "run([enc('{\"type\":\"turn.accepted\"}\\n')])") == {
        "events": [{"type": "turn.accepted"}]
    }
    source = CHAT_JS.read_text(encoding="utf-8")
    assert "token.phase !== 'settled'" in source
    assert source.count("finishTurn(token)") >= 2


def test_failed_terminal_passes_through_as_event(node: str) -> None:
    """turn.failed is data for handleStreamEvent (cancelTurn + error state),
    not a thrown error at the decode layer."""
    out = _eval(
        node,
        "run([enc('{\"type\":\"turn.accepted\"}\\n"
        "{\"type\":\"turn.failed\",\"message\":\"hết hạn mức\"}\\n')])",
    )
    assert out["events"][-1] == {"type": "turn.failed", "message": "hết hạn mức"}
