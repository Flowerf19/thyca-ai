"""Node tests for the hash reader inside ui/profile/js/profile.js.

The old shared/js/format.js exported decodeHash(hash); the rebuilt UI keeps
the same job inline in profile.js as a no-arg reader of location.hash. The
test lifts the real function out of the page source and evals it in Node —
no module import needed (profile.js is a classic script), a Function
wrapper plus a stubbed `location` is enough.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "thyca" / "webui" / "ui" / "profile" / "js" / "profile.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, hash_value: str | None) -> object:
    source = """
import fs from 'node:fs';
const src = fs.readFileSync(process.env.PROFILE_JS, 'utf8');
const match = src.match(/function decodeHash\\(\\) \\{[\\s\\S]*?\\n\\}/);
if (!match) throw new Error('decodeHash() not found in profile.js');
const decodeHash = new Function('location', match[0] + '; return decodeHash();');
console.log(JSON.stringify(decodeHash({ hash: JSON.parse(process.env.HASH) })));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PROFILE_JS": str(SCRIPT), "HASH": json.dumps(hash_value)},
    )
    return json.loads(result.stdout)


def test_decode_hash_reads_a_file_name(node: str) -> None:
    assert _eval(node, "#USER.md") == "USER.md"
    assert _eval(node, "SOUL.md") == "SOUL.md"
    assert _eval(node, "") == ""
    assert _eval(node, "#") == ""
    assert _eval(node, "#%23bang") == "#bang"


def test_decode_hash_survives_a_stray_escape(node: str) -> None:
    """The hash is user-controlled: a malformed escape must not throw.

    A throw here used to escape the hashchange handler, so a bad hash left the
    page half-updated with an uncaught URIError. The rebuilt page answers ""
    instead of echoing the raw hash — an undecodable hash matches no file, so
    loadFiles() falls back to the first profile file.
    """
    assert _eval(node, "#%") == ""
    assert _eval(node, "#%E0%A4%A") == ""
