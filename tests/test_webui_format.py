"""Node tests for the pure display helpers — webui/backend/format.js.

Runs in Node with --input-type=module so no DOM is needed; mirrors the eval
helper style of tests/test_turn_status.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "thyca" / "webui" / "backend" / "format.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ decodeHash }} from '{SCRIPT.as_posix()}';\n"
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


def test_decode_hash_reads_a_file_name(node: str) -> None:
    assert _eval(node, "decodeHash('#USER.md')") == "USER.md"
    assert _eval(node, "decodeHash('SOUL.md')") == "SOUL.md"
    assert _eval(node, "decodeHash('')") == ""
    assert _eval(node, "decodeHash(null)") == ""
    assert _eval(node, "decodeHash('#%23bang')") == "#bang"


def test_decode_hash_survives_a_stray_escape(node: str) -> None:
    """The hash is user-controlled: a malformed escape must not throw.

    A throw here used to escape the hashchange handler, so a bad hash left the
    page half-updated with an uncaught URIError.
    """
    assert _eval(node, "decodeHash('#%')") == "%"
    assert _eval(node, "decodeHash('#%E0%A4%A')") == "%E0%A4%A"
