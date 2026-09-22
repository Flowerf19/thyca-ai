"""Execute the browser stream reader in Node, including paused rendering."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "thyca/webui/shared/js/api.js"


@pytest.mark.parametrize("method", ["getNdjson", "postNdjson"])
@pytest.mark.parametrize("terminal", ["turn.completed", "turn.cancelled"])
@pytest.mark.parametrize("chunk_size", [7, 65536])
def test_stream_consumes_reasoning_without_animation_frames(
    method: str, terminal: str, chunk_size: int,
) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = f"""
import {{ getNdjson, postNdjson }} from {json.dumps(API.as_uri())};
// Hidden/background pages can suspend animation callbacks indefinitely.
globalThis.requestAnimationFrame = () => 1;
const events = [
  {{ type: 'turn.accepted' }},
  {{ type: 'llm.thinking', delta: 'Suy nghĩ đầy đủ' }},
  {{ type: 'tool.started', name: 'bash', call_id: '1' }},
  {{ type: 'llm.thinking', delta: ' — tiếp tục' }},
  {{ type: {json.dumps(terminal)}, detail: {{ running: false }} }},
];
const bytes = new TextEncoder().encode(events.map(JSON.stringify).join('\\n'));
// Split inside UTF-8 text, with the last line lacking a newline.
globalThis.fetch = async () => new Response(new ReadableStream({{
  start(controller) {{
    for (let i = 0; i < bytes.length; i += {chunk_size}) controller.enqueue(bytes.slice(i, i + {chunk_size}));
    controller.close();
  }},
}}));
const seen = [];
let watchdog;
const timeout = new Promise(resolve => {{ watchdog = setTimeout(() => resolve('stalled'), 1000); }});
const pending = {method}('mock', {"{}, " if method == "postNdjson" else ""}event => seen.push(event));
const detail = await Promise.race([pending, timeout]);
clearTimeout(watchdog);
console.log(JSON.stringify({{ detail, seen }}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True, cwd=ROOT, timeout=5,
    )
    data = json.loads(result.stdout)
    assert data["detail"] == {"running": False}
    assert [e["type"] for e in data["seen"]] == [
        "turn.accepted", "llm.thinking", "tool.started", "llm.thinking", terminal,
    ]
    assert "".join(e["delta"] for e in data["seen"] if "delta" in e) == "Suy nghĩ đầy đủ — tiếp tục"
