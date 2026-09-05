"""Formula class — many charts, one mapper."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "webui" / "js" / "staff" / "formula.js"
MAP = ROOT / "webui" / "js" / "staff" / "map.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ Formula, defaultFormula, getFormula, pickFormula, pickBpm, listFormulas }} from '{FORMULA.as_posix()}';\n"
        f"import {{ scoreFromEvents }} from '{MAP.as_posix()}';\n"
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


def test_default_is_am_8(node: str) -> None:
    assert _eval(node, "defaultFormula().id") == "am-8"
    assert _eval(node, "getFormula('nope').id") == "am-8"
    assert _eval(node, "defaultFormula().degreeAt(0)") == "i"
    assert _eval(node, "defaultFormula().degreeAt(5)") == "VII7"
    assert _eval(node, "defaultFormula().degreeAt(8)") == "i"


def test_c_doo_wop_is_registered_not_default(node: str) -> None:
    assert _eval(node, "getFormula('c-doo-wop').key") == "C"
    assert _eval(node, "getFormula('c-doo-wop').bars") == [
        "I", "vi", "IV", "V", "ii", "V7", "I", "I",
    ]


def test_score_from_events_uses_passed_formula(node: str) -> None:
    tools = [{"type": "tool.finished", "ok": True}] * 32
    expr = (
        f"({{ am: scoreFromEvents({json.dumps(tools)}).formula, "
        f"c: scoreFromEvents({json.dumps(tools)}, undefined, getFormula('c-doo-wop')).formula, "
        f"amH: scoreFromEvents({json.dumps(tools)}).measures.map((m) => m.harmony), "
        f"cH: scoreFromEvents({json.dumps(tools)}, undefined, getFormula('c-doo-wop')).measures.map((m) => m.harmony) }})"
    )
    result = _eval(node, expr)
    assert result["am"] == "am-8"
    assert result["c"] == "c-doo-wop"
    assert result["amH"] == ["i", "VI", "III", "VII", "iv", "VII7", "i", "i"]
    assert result["cH"] == ["I", "vi", "IV", "V", "ii", "V7", "I", "I"]


def test_pick_formula_uses_registry(node: str) -> None:
    ids = _eval(node, "listFormulas().map((f) => f.id).sort()")
    assert ids == ["am-8", "c-doo-wop"]
    assert _eval(node, "pickFormula(() => 0).id") == "am-8"
    assert _eval(node, "pickFormula(() => 0.99).id") == "c-doo-wop"


def test_pick_bpm_range_and_fixed(node: str) -> None:
    assert _eval(node, "pickBpm({ bpm: 65 })") == 65
    assert _eval(node, "pickBpm({ bpm: [70, 70] })") == 70
    lo = _eval(node, "pickBpm({ bpm: [58, 72] }, () => 0)")
    hi = _eval(node, "pickBpm({ bpm: [58, 72] }, () => 0.999)")
    assert lo == 58
    assert hi == 72
