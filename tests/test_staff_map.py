"""Node tests for the pure event -> score mapper — webui/js/staff-map.js.

Runs in Node with --input-type=module so no DOM is needed; mirrors the
eval helper style of tests/test_ndjson.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "staff-map.js"

ACCEPTED = {"type": "turn.accepted"}
COMPLETED = {"type": "turn.completed"}
FAILED = {"type": "turn.failed"}

CHORDS = {
    "I": ["C5", "E5", "G5"],
    "vi": ["C5", "E5", "A5"],
    "IV": ["C5", "F5", "A5"],
    "V": ["B4", "D5", "G5"],
    "vii°": ["B4", "D5", "F5"],
}


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ scoreFromEvents }} from '{SCRIPT.as_posix()}';\n"
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


def _score(node: str, *events: dict) -> dict:
    return _eval(node, f"scoreFromEvents({json.dumps(list(events))})")


def _slots(measure: dict) -> list[tuple[int, int, list[str] | None]]:
    """[(offset, duration, pitches-or-None-for-rest)] in order, no overlap."""
    slots = [(e["offset"], e["duration"], list(e["pitches"])) for e in measure["events"]]
    slots += [(r["offset"], r["duration"], None) for r in measure["rests"]]
    return sorted(slots)


def test_integer_ticks_and_full_measure_union(node: str) -> None:
    events = [ACCEPTED, {"type": "tool.finished", "ok": True}]
    score = _score(node, *events, COMPLETED)
    assert score["key"] == "C"
    assert score["meter"] == {"beats": 4, "beatType": 4, "ticksPerQuarter": 4}
    for measure in score["measures"]:
        for item in measure["events"] + measure["rests"]:
            assert isinstance(item["offset"], int) and not isinstance(item["offset"], bool)
            assert isinstance(item["duration"], int) and not isinstance(item["duration"], bool)
            assert 0 <= item["offset"] < 16
            assert item["duration"] in (4, 8, 16)
            assert item["offset"] + item["duration"] <= 16
            if item["duration"] > 4 and item["offset"] != 0:
                # No duration may cross beat 3 (tick 8) except a whole rest at 0.
                assert not (item["offset"] < 8 < item["offset"] + item["duration"])
        slots = _slots(measure)
        assert slots[0][0] == 0
        assert slots[-1][0] + slots[-1][1] == 16
        assert all(a[0] + a[1] == b[0] for a, b in zip(slots, slots[1:]))


def test_empty_input_is_whole_rest_measure(node: str) -> None:
    score = _score(node)
    assert len(score["measures"]) == 1
    measure = score["measures"][0]
    assert measure["harmony"] == "I"
    assert measure["terminal"] is None
    assert measure["finalBarline"] is False
    assert measure["events"] == []
    assert measure["rests"] == [{"offset": 0, "duration": 16}]


def test_accepted_at_beat_one_no_pickup(node: str) -> None:
    score = _score(node, ACCEPTED)
    assert len(score["measures"]) == 1
    measure = score["measures"][0]
    assert measure["harmony"] == "I"
    assert measure["events"] == [{"offset": 0, "duration": 4, "pitches": ["C5"]}]
    assert measure["rests"] == [
        {"offset": 4, "duration": 4},
        {"offset": 8, "duration": 8},
    ]


def test_harmony_cycle_across_measures(node: str) -> None:
    events = []
    for _ in range(16):
        events.append({"type": "tool.finished", "ok": True})
    score = _score(node, *events)
    harmonies = [m["harmony"] for m in score["measures"]]
    assert harmonies == ["I", "vi", "IV", "V"]


def test_pitches_are_subset_of_measure_chord(node: str) -> None:
    events = [
        ACCEPTED,
        {"type": "llm.finished"},
        {"type": "tool.started"},
        {"type": "tool.finished", "ok": False},
        {"type": "tool.finished", "ok": True},
        {"type": "session.naming.finished"},
    ]
    score = _score(node, *events)
    assert len(score["measures"]) == 2
    vii_measures = 0
    for measure in score["measures"]:
        chord = CHORDS[measure["harmony"]]
        for event in measure["events"]:
            pitches = event["pitches"]
            if sorted(pitches) == sorted(CHORDS["vii°"]):
                vii_measures += 1
                continue
            if measure["harmony"] is not None:
                assert set(pitches) <= set(chord), (measure["harmony"], pitches)
    assert vii_measures == 1  # only the failed tool may use vii°


def test_unknown_event_type_is_noop(node: str) -> None:
    score = _score(node, ACCEPTED, {"type": "no.such.event"}, {"type": ""}, {"type": "tool.finished", "ok": True})
    assert len(score["measures"]) == 1
    measure = score["measures"][0]
    assert len(measure["events"]) == 2
    assert measure["events"][0] == {"offset": 0, "duration": 4, "pitches": ["C5"]}
    assert measure["events"][1] == {"offset": 4, "duration": 4, "pitches": ["C5", "E5", "G5"]}
    assert measure["rests"] == [{"offset": 8, "duration": 8}]


def test_no_consecutive_diminished(node: str) -> None:
    fail = {"type": "tool.finished", "ok": False}
    score = _score(node, ACCEPTED, fail, fail)
    assert len(score["measures"]) == 1
    events = score["measures"][0]["events"]
    assert [e["pitches"] for e in events] == [
        ["C5"],
        ["B4", "D5", "F5"],
        ["C5", "E5", "G5"],
    ]
    assert sorted(events[1]["pitches"]) == sorted(CHORDS["vii°"])
    assert sorted(events[2]["pitches"]) == sorted(CHORDS["I"])


def test_completed_terminal_measure(node: str) -> None:
    score = _score(node, ACCEPTED, {"type": "tool.finished", "ok": True}, COMPLETED)
    assert len(score["measures"]) == 2
    activity, terminal = score["measures"]
    assert activity["terminal"] is None
    assert activity["harmony"] == "I"
    assert activity["events"] == [
        {"offset": 0, "duration": 4, "pitches": ["C5"]},
        {"offset": 4, "duration": 4, "pitches": ["C5", "E5", "G5"]},
    ]
    assert activity["rests"] == [{"offset": 8, "duration": 8}]  # still 16 ticks
    assert terminal["harmony"] is None
    assert terminal["terminal"] == "completed"
    assert terminal["finalBarline"] is True
    assert terminal["rests"] == []
    assert terminal["events"] == [
        {"offset": 0, "duration": 8, "pitches": ["G4", "B4", "D5"]},
        {"offset": 8, "duration": 8, "pitches": ["C5", "E5", "G5"]},
    ]


def test_failed_terminal_measure(node: str) -> None:
    score = _score(node, ACCEPTED, FAILED)
    assert len(score["measures"]) == 2
    activity, terminal = score["measures"]
    assert activity["rests"] == [
        {"offset": 4, "duration": 4},
        {"offset": 8, "duration": 8},
    ]
    assert terminal["harmony"] is None
    assert terminal["terminal"] == "failed"
    assert terminal["finalBarline"] is False
    assert terminal["events"] == [{"offset": 0, "duration": 16, "pitches": ["G4", "B4", "D5"]}]
    assert terminal["rests"] == []


def test_completed_requires_event_in_input(node: str) -> None:
    plain = _score(node, ACCEPTED)
    assert all(m["terminal"] is None for m in plain["measures"])
    assert all(m["finalBarline"] is False for m in plain["measures"])
    score = _score(node, ACCEPTED, {"type": "turn.completed", "detail": {"id": "x"}})
    assert score["measures"][-1]["terminal"] == "completed"


def test_window_keeps_terminal_and_original_harmony(node: str) -> None:
    events = []
    for _ in range(120):
        events.append({"type": "tool.finished", "ok": True})
    events.append(COMPLETED)
    score = _score(node, *events)
    assert len(score["measures"]) <= 16
    assert score["measures"][-1]["terminal"] == "completed"
    assert score["measures"][-1]["finalBarline"] is True
    first = score["measures"][0]
    assert first["terminal"] is None
    assert first["events"][0]["offset"] == 0  # excerpt starts at a barline
    # 120 quarters = activity 0..29 + terminal. Window keeps 15..29 + terminal.
    # HARMONY_ORDER[15 % 4] == V.
    assert first["harmony"] == "V"


def test_naming_started_is_quarter_rest_not_note(node: str) -> None:
    score = _score(node, ACCEPTED, {"type": "session.naming.started"})
    measure = score["measures"][0]
    assert measure["harmony"] == "I"
    assert measure["events"] == [{"offset": 0, "duration": 4, "pitches": ["C5"]}]
    assert measure["rests"] == [
        {"offset": 4, "duration": 4},
        {"offset": 8, "duration": 8},
    ]
    assert all(event["pitches"] for event in measure["events"])


def test_same_input_is_deterministic(node: str) -> None:
    events = [
        ACCEPTED,
        {"type": "llm.finished"},
        {"type": "tool.started"},
        {"type": "tool.finished", "ok": False},
        {"type": "tool.finished", "ok": True},
        {"type": "session.naming.started"},
        {"type": "session.naming.finished"},
        COMPLETED,
    ]
    assert _score(node, *events) == _score(node, *events)
