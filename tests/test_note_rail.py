from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "note-rail.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ gapsFromBlocked, placeNotes, KINDS }} from '{SCRIPT.as_posix()}';\n"
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


def test_kinds_ratio(node: str) -> None:
    kinds = _eval(node, "KINDS")
    assert kinds.count("b") == 10
    assert kinds.count("s") == 5
    assert kinds.count("r") == 3
    assert kinds.count("d") == 2


def test_gaps_skip_blocked(node: str) -> None:
    gaps = _eval(node, "gapsFromBlocked([[40, 90]], 200)")
    assert gaps == [[0, 40], [90, 200]]


def test_place_notes_count_and_wobble(node: str) -> None:
    notes = _eval(node, "placeNotes([[0, 320]], 320)")
    assert len(notes) == 9
    kinds = [item["kind"] for item in notes]
    assert set(kinds) <= {"b", "s", "r", "d"}
    assert notes[0]["y"] >= 16
