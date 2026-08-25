from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "staff-map.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ keyForMode, classifyThink, thinkingEvent, createThinkCycle, "
        f"THINK_PHASES, THINK_BREATH, CLOSE_LINE, STEP, KEYS }} from '{SCRIPT.as_posix()}';\n"
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


def test_key_for_mode(node: str) -> None:
    assert _eval(node, 'keyForMode("chat")') == "C"
    assert _eval(node, 'keyForMode("memories")') == "G"
    assert _eval(node, 'keyForMode("poetry")') == "Am"


def test_classify_think_categories(node: str) -> None:
    assert _eval(node, 'classifyThink("Lắng nghe khoảng lặng…")') == "rest"
    assert _eval(node, 'classifyThink("Hmm…")') == "rest"
    assert _eval(node, 'classifyThink("Đang lắng nghe nhịp…")') == "root"
    assert _eval(node, 'classifyThink("Đang tìm tứ thơ…")') == "walk"
    assert _eval(node, 'classifyThink("Đang tìm vần…")') == "dyad"
    assert _eval(node, 'classifyThink("Đang buộc câu thơ…")') == "triad"
    assert _eval(node, 'classifyThink("Đang làm thơ…")') == "whole"
    assert _eval(node, 'classifyThink("Sắp xong rồi…")') == "tonic"


def test_thinking_event_rest_and_walk(node: str) -> None:
    rest = _eval(node, 'thinkingEvent("Hmm…", 0, "C")')
    assert rest["kind"] == "rest"
    assert rest["steps"] == []
    walk0 = _eval(node, 'thinkingEvent("Đang tìm tứ thơ…", 0, "C")')
    walk1 = _eval(node, 'thinkingEvent("Đang đợi cảm hứng…", 1, "C")')
    walk2 = _eval(node, 'thinkingEvent("Đang tìm hình ảnh…", 2, "C")')
    c5 = _eval(node, "STEP.C5")
    assert walk0["steps"] == [c5]
    assert walk0["chord"] == "I"
    assert walk1["steps"] == [c5]
    assert walk1["chord"] == "vi"
    assert walk2["chord"] == "IV"
    assert walk2["steps"] == [c5]


def test_thinking_event_deterministic(node: str) -> None:
    a = _eval(node, 'thinkingEvent("Đang chọn từ…", 2, "C")')
    b = _eval(node, 'thinkingEvent("Đang chọn từ…", 2, "C")')
    assert a == b
    assert a["kind"] == "dyad"
    assert a["steps"] == [_eval(node, "STEP.F4"), _eval(node, "STEP.C5")]


def test_close_is_tonic_whole(node: str) -> None:
    event = _eval(node, 'thinkingEvent("Sắp xong rồi…", 7, "C")')
    assert event["kind"] == "triad"
    assert event["duration"] == "w"
    assert event["chord"] == "I"
    assert event["steps"] == [_eval(node, "STEP.C5"), _eval(node, "STEP.E5"), _eval(node, "STEP.G5")]


def test_g_dominant_has_sharp(node: str) -> None:
    event = _eval(node, 'thinkingEvent("Đang buộc câu thơ…", 3, "G")')
    assert event["chord"] == "V"
    assert _eval(node, "STEP.Fs5") in event["steps"]
    assert event["sharps"] == [_eval(node, "STEP.Fs5")]


def test_think_cycle_no_repeat_three_no_adjacent_breath(node: str) -> None:
    lines = _eval(
        node,
        "(function () { const c = createThinkCycle(() => 0); return Array.from({length: 12}, () => c.nextLine()); })()",
    )
    assert CLOSE_LINE_VALUE not in lines
    for index, line in enumerate(lines):
        assert line not in lines[max(0, index - 3) : index]
    for index in range(1, len(lines)):
        assert not (lines[index] in BREATH and lines[index - 1] in BREATH)


def test_think_cycle_stays_in_phases(node: str) -> None:
    lines = _eval(
        node,
        "(function () { const c = createThinkCycle(() => 0); return Array.from({length: 6}, () => c.nextLine()); })()",
    )
    assert lines[0] in PHASE0
    assert lines[1] in PHASE0
    assert lines[2] in BREATH


CLOSE_LINE_VALUE = "Sắp xong rồi…"
BREATH = ["Hmm…", "Đang suy nghĩ…", "Tiếp tục suy nghĩ…", "Đang để cảm xúc lắng…"]
PHASE0 = ["Đang lắng nghe nhịp…", "Nghe nhịp trong đầu…", "Lắng nghe khoảng lặng…"]
