"""NDJSON stream decoding — thyca-css/backend/api.js (postNdjson).

The old standalone decoder (webui/js/shared/ndjson.js) no longer exists: the
new UI decodes NDJSON inline inside postNdjson. These tests drive postNdjson
with a stubbed fetch whose body yields byte chunks, so chunk-split lines,
multibyte UTF-8 splits, empty lines and malformed JSON are covered without a
browser or server.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "thyca-css" / "backend" / "api.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


_PREAMBLE = f"""
import {{ postNdjson }} from '{API.as_posix()}';
const enc = (s) => new TextEncoder().encode(s);
const mkResp = (chunks, ok = true) => ({{
  ok,
  status: ok ? 200 : 500,
  json: async () => (ok ? null : {{ error: 'boom' }}),
  body: ok ? {{ getReader: () => {{
    let i = 0;
    return {{ read: async () => (i < chunks.length ? {{ done: false, value: chunks[i++] }} : {{ done: true, value: undefined }}) }};
  }} }} : null,
}});
async function run(chunks) {{
  const events = [];
  globalThis.fetch = async () => mkResp(chunks);
  const detail = await postNdjson('/api/x', {{}}, (e) => events.push(e));
  return {{ events, detail }};
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


def _eval_err(node: str, expression: str) -> str:
    source = _PREAMBLE + (
        "try { await %s; console.log(JSON.stringify('no-throw')); }\n"
        "catch (error) { console.log(JSON.stringify(error.message)); }\n" % expression
    )
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
    assert out["detail"] == {"id": "x"}


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


def test_malformed_json_line_throws_public_error(node: str) -> None:
    assert (
        _eval_err(node, "run([enc('{\"type\": broken}\\n')])")
        == "Luồng trả lời từ backend không hợp lệ."
    )


def test_incomplete_garbage_on_flush_throws(node: str) -> None:
    assert (
        _eval_err(node, "run([enc('not-json')])")
        == "Luồng trả lời từ backend không hợp lệ."
    )


def test_missing_terminal_event_throws(node: str) -> None:
    assert (
        _eval_err(node, "run([enc('{\"type\":\"turn.accepted\"}\\n')])")
        == "Luồng trả lời kết thúc quá sớm."
    )


def test_failed_terminal_throws_public_message(node: str) -> None:
    assert (
        _eval_err(
            node,
            "run([enc('{\"type\":\"turn.accepted\"}\\n"
            "{\"type\":\"turn.failed\",\"message\":\"hết hạn mức\"}\\n')])",
        )
        == "hết hạn mức"
    )
