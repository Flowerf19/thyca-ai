"""Node tests for the NDJSON decoder — webui/js/shared/ndjson.js.

Runs in Node with --input-type=module so no DOM is needed; mirrors the
eval helper style of tests/test_turn_status.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "shared" / "ndjson.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ createNdjsonDecoder }} from '{SCRIPT.as_posix()}';\n"
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


def _encode(chunks: list[str]) -> list[list[int]]:
    """Encode string chunks as byte arrays, one list per stream chunk."""
    return [list(chunk.encode("utf-8")) for chunk in chunks]


def test_one_chunk_two_full_lines(node: str) -> None:
    expression = """(() => {
      const d = createNdjsonDecoder();
      return d.push(new Uint8Array([97,98,99]));  // does not reject plain bytes
    })()"""
    assert _eval(node, expression) == []
    expression = """(() => {
      const d = createNdjsonDecoder();
      const out = d.push(Uint8Array.from(%s));
      return out;
    })()""" % json.dumps(
        _encode(['{"type":"turn.accepted"}\n{"type":"llm.started","round":1}\n'])[0]
    )
    assert _eval(node, expression) == [
        {"type": "turn.accepted"},
        {"type": "llm.started", "round": 1},
    ]


def test_line_split_across_two_chunks(node: str) -> None:
    first = '{"type":"turn.acc'
    second = 'epted"}\n'
    expression = """(() => {
      const d = createNdjsonDecoder();
      const a = d.push(Uint8Array.from(%s));
      const b = d.push(Uint8Array.from(%s));
      return [a, b];
    })()""" % (json.dumps(_encode([first])[0]), json.dumps(_encode([second])[0]))
    assert _eval(node, expression) == [[], [{"type": "turn.accepted"}]]


def test_utf8_split_across_multiple_bytes(node: str) -> None:
    line = '{"type":"turn.accepted","note":"Đã nhận"}\n'
    assert '"Đã nhận"' in line
    encoded = line.encode("utf-8")
    # Cut inside the "ậ" sequence (0xC3 0xBA 0xE1 0xBA 0xAD: ả + ậ) and again
    # inside the trailing ậ so the decoder must survive two partial bytes.
    first = encoded[: encoded.index("ậ".encode("utf-8")) + 1]
    middle = encoded[len(first) : len(first) + 2]
    last = encoded[len(first) + 2 :]
    assert b"\xc3" in first  # first chunk ends mid-multibyte
    assert b"\xba" not in first
    expression = """(() => {
      const d = createNdjsonDecoder();
      const a = d.push(Uint8Array.from(%s));
      const b = d.push(Uint8Array.from(%s));
      const c = d.push(Uint8Array.from(%s));
      return [a, b, c];
    })()""" % (json.dumps(list(first)), json.dumps(list(middle)), json.dumps(list(last)))
    assert _eval(node, expression) == [[], [], [{"type": "turn.accepted", "note": "Đã nhận"}]]


def test_flush_complete_line_without_trailing_newline(node: str) -> None:
    expression = """(() => {
      const d = createNdjsonDecoder();
      const a = d.push(Uint8Array.from(%s));
      const b = d.flush();
      return [a, b];
    })()""" % json.dumps(
        _encode(['{"type":"turn.completed","detail":{"id":"x"}}'])[0]
    )
    assert _eval(node, expression) == [
        [],
        [{"type": "turn.completed", "detail": {"id": "x"}}],
    ]


def test_empty_lines_ignored(node: str) -> None:
    expression = """(() => {
      const d = createNdjsonDecoder();
      const out = d.push(Uint8Array.from(%s));
      out.push(...d.flush());
      return out;
    })()""" % json.dumps(
        _encode(['\n  \n{"type":"turn.accepted"}\n\n'])[0]
    )
    assert _eval(node, expression) == [{"type": "turn.accepted"}]


def test_malformed_json_line_throws_public_error(node: str) -> None:
    expression = """(() => {
      const d = createNdjsonDecoder();
      try {
        d.push(Uint8Array.from(%s));
        return "no-throw";
      } catch (error) {
        return error.message;
      }
    })()""" % json.dumps(_encode(['{"type": broken}\n'])[0])
    assert _eval(node, expression) == "Phản hồi từ Thyca không hợp lệ."


def test_incomplete_garbage_on_flush_throws(node: str) -> None:
    expression = """(() => {
      const d = createNdjsonDecoder();
      d.push(Uint8Array.from(%s));
      try {
        d.flush();
        return "no-throw";
      } catch (error) {
        return error.message;
      }
    })()""" % json.dumps(_encode(["not-json"])[0])
    assert _eval(node, expression) == "Phản hồi từ Thyca không hợp lệ."
