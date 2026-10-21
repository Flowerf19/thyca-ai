"""Execute the browser stream reader in Node, including paused rendering.

Drives ``readNdjson`` (webui/ui/chat/js/chat.js) exactly the way its callers
do: a stubbed fetch whose ``res.body`` is a chunked ReadableStream. Hidden or
background pages can suspend animation frames indefinitely — consumption must
never gate on paint (the new reader doesn't touch rAF at all).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHAT_JS = ROOT / "thyca" / "webui" / "ui" / "chat" / "js" / "chat.js"


@pytest.mark.parametrize("terminal", ["turn.completed", "turn.cancelled"])
@pytest.mark.parametrize("chunk_size", [7, 65536])
def test_stream_consumes_reasoning_without_animation_frames(
    terminal: str, chunk_size: int,
) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = f"""
import {{ loadScripts }} from './tests/webui_dom.mjs';
const {{ readNdjson }} = loadScripts(['{CHAT_JS.as_posix()}'], 'return {{ readNdjson }};');
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
const body = new ReadableStream({{
  start(controller) {{
    for (let i = 0; i < bytes.length; i += {chunk_size}) controller.enqueue(bytes.slice(i, i + {chunk_size}));
    controller.close();
  }},
}});
const seen = [];
let watchdog;
const timeout = new Promise(resolve => {{ watchdog = setTimeout(() => resolve('stalled'), 1000); }});
const pending = readNdjson(body, event => seen.push(event));
const outcome = await Promise.race([pending.then(() => 'done'), timeout]);
clearTimeout(watchdog);
console.log(JSON.stringify({{ outcome, seen }}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True, cwd=ROOT, timeout=5,
    )
    data = json.loads(result.stdout)
    assert data["outcome"] == "done"
    assert [e["type"] for e in data["seen"]] == [
        "turn.accepted", "llm.thinking", "tool.started", "llm.thinking", terminal,
    ]
    assert "".join(e["delta"] for e in data["seen"] if "delta" in e) == "Suy nghĩ đầy đủ — tiếp tục"
    # The terminal event arrives whole — callers read .detail off it.
    assert data["seen"][-1]["detail"] == {"running": False}
