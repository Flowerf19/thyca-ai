"""Staff playback — whole passage via Web Audio, not per-note taps."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLAY = ROOT / "webui" / "js" / "staff" / "play.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = f"""
    const started = [];
    class Osc {{
      constructor() {{ this.type = ""; this.frequency = {{ value: 0 }}; }}
      connect() {{}}
      start() {{ started.push(this.frequency.value); }}
      stop() {{}}
    }}
    class Gain {{
      constructor() {{
        this.gain = {{
          setValueAtTime() {{}},
          linearRampToValueAtTime() {{}},
        }};
      }}
      connect() {{}}
    }}
    const fake = {{
      currentTime: 0,
      destination: {{}},
      state: "running",
      createOscillator() {{ return new Osc(); }},
      createGain() {{ return new Gain(); }},
    }};
    globalThis.AudioContext = class {{ constructor() {{ throw new Error("use injected context"); }} }};
    import {{ playPitches, playScore }} from {json.dumps(PLAY.as_uri())};
    const result = await (async () => {{ return {expression}; }})();
    console.log(JSON.stringify({{ ...result, started }}));
    """
    completed = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(completed.stdout)


def test_play_score_schedules_whole_passage(node: str) -> None:
    score = {
        "measures": [
            {
                "events": [
                    {"offset": 0, "duration": 4, "pitches": ["C5"]},
                    {"offset": 4, "duration": 4, "pitches": ["E5", "G5"]},
                ],
                "rests": [{"offset": 8, "duration": 8}],
            },
            {
                "events": [{"offset": 0, "duration": 8, "pitches": ["C5"]}],
                "rests": [{"offset": 8, "duration": 8}],
            },
        ]
    }
    result = _eval(node, f"({{ ok: await playScore({json.dumps(score)}, {{ context: fake }}) }})")
    assert result["ok"] is True
    # C5 + (E5,G5) + C5 — rests add no oscillators (no decodeAudioData → triangle)
    assert len(result["started"]) == 4
    assert result["started"][0] == pytest.approx(523.25)


def test_play_score_empty_or_rests_only_is_silent(node: str) -> None:
    empty = _eval(node, "({ ok: await playScore({ measures: [] }, { context: fake }) })")
    rests = _eval(
        node,
        "({ ok: await playScore({ measures: [{ events: [], rests: [{ offset: 0, duration: 16 }] }] }, { context: fake }) })",
    )
    assert empty["ok"] is False
    assert empty["started"] == []
    assert rests["ok"] is False
    assert rests["started"] == []


def test_play_chord_starts_one_oscillator_per_pitch(node: str) -> None:
    result = _eval(
        node,
        "({ ok: playPitches(['C5', 'E5', 'G5'], 4, { context: fake }) })",
    )
    assert result["ok"] is True
    assert len(result["started"]) == 3
    assert result["started"][0] == pytest.approx(523.25)


def test_play_score_prefers_sound_over_pitches(node: str) -> None:
    score = {
        "measures": [
            {
                "events": [
                    {
                        "offset": 0,
                        "duration": 4,
                        "pitches": ["C5"],
                        "sound": ["C4", "C5"],
                    }
                ],
                "rests": [],
            }
        ]
    }
    result = _eval(node, f"({{ ok: await playScore({json.dumps(score)}, {{ context: fake }}) }})")
    assert result["ok"] is True
    assert len(result["started"]) == 2
    assert result["started"][0] == pytest.approx(261.63)
