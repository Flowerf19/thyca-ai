"""WS-4 private-followup consent card wiring — real questionCard +
real initComposer/sendChoiceText under a fake DOM, node subprocess."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "thyca" / "webui" / "pages" / "chat" / "team-cards.js"
COMPOSER = ROOT / "thyca" / "webui" / "pages" / "chat" / "composer.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if binary is None:
        pytest.skip("node not installed")
    return binary


SCRIPT = f"""
import {{ questionCard }} from '{CARDS.as_posix()}';
import {{ initComposer, sendChoiceText }} from '{COMPOSER.as_posix()}';

class El {{
  constructor(tag) {{
    this.tag = tag; this.children = []; this.listeners = {{}};
    this.className = ''; this.textContent = ''; this.type = '';
    this.hidden = false; this.attrs = {{}}; this.value = '';
  }}
  append(...kids) {{ this.children.push(...kids); }}
  setAttribute(k, v) {{ this.attrs[k] = v; }}
  addEventListener(ev, fn) {{ (this.listeners[ev] ||= []).push(fn); }}
  click() {{ for (const fn of this.listeners.click || []) fn(); }}
  focus() {{}}
}}
const walk = (el) => [el, ...el.children.flatMap(walk)];

globalThis.document = {{ createElement: (t) => new El(t) }};
globalThis.window = {{ clearTimeout: () => {{}} }};

const calls = [];
let resOk = true;
globalThis.fetch = async (url, opts) => {{
  calls.push({{ url, method: opts?.method, body: opts?.body }});
  return {{ ok: resOk, status: resOk ? 200 : 409, json: async () => ({{}}) }};
}};
const sends = []; const loaded = [];
const input = new El('input'); const status = new El('p');
initComposer({{
  state: {{ activeId: 'SESS-1', detail: null, sessionPage: 0 }},
  el: {{ input, status, idleNudge: new El('div'), contextNudge: null,
        messageList: new El('div') }},
  composerBusy: () => sends[sends.length - 1] === true,
  setSending: (v) => sends.push(v),
  loadSession: async (id) => loaded.push(id),
  sessionKey: () => 'SESS-1',
  scrollToBottom: () => {{}},
  syncComposer: () => {{}},
  rememberActiveSession: () => {{}},
  renderDetail: () => {{}},
  newSession: () => {{}},
}});

const card = {{
  type: 'question',
  title: 'Cho phép gửi bản này về nhóm?',
  private_followup: {{ pending_id: 'pf-9', revision: 2 }},
  options: [
    {{ id: 'approve', label: 'Cho phép gửi bản này' }},
    {{ id: 'keep_private', label: 'Giữ riêng' }},
    {{ id: 'cancel', label: 'Huỷ yêu cầu' }},
  ],
}};
let chosen = null;
const section = questionCard(card, (label, meta) => {{
  chosen = {{ label, meta }};
  sendChoiceText(label, meta);
}});
const chips = walk(section).filter(
  (e) => e.tag === 'button' && e.className.includes('team-choice'));
chips[0].click();
chips[0].click();
const busyBlocked = calls.length;
await new Promise((r) => setImmediate(r));
const result = {{
  chosen,
  fetchCalls: calls,
  sends,
  loaded,
  afterDoubleClick: busyBlocked,
}};

resOk = false;
sends.length = 0;
chips[1].click();
await new Promise((r) => setImmediate(r));
result.non2xx = {{ statusText: status.textContent, sends: [...sends],
                  lastCall: calls[calls.length - 1] }};

const plainCard = {{ type: 'question', title: 'Pick',
                    options: ['Yes', 'No'] }};
let plainChosen = null;
const plain = questionCard(plainCard, (label, meta) => {{
  plainChosen = {{ label, meta }};
}});
const plainChips = walk(plain).filter(
  (e) => e.tag === 'button' && e.className.includes('team-choice'));
const before = calls.length;
plainChips[0].click();
await new Promise((r) => setImmediate(r));
result.ordinary = {{ chosen: plainChosen,
                    newFetches: calls.length - before,
                    consentHits: calls.slice(before).filter(
                      (c) => c.url.includes('private-followup')).length }};
console.log(JSON.stringify(result));
"""


def _run(node: str) -> dict:
    result = subprocess.run(
        [node, "--input-type=module", "-e", SCRIPT],
        check=True, capture_output=True, text=True, cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_consent_card_posts_exact_option_id(node: str) -> None:
    out = _run(node)
    chosen = out["chosen"]
    assert chosen["meta"]["choice"] == "approve"
    assert chosen["meta"]["pending_id"] == "pf-9"
    assert chosen["meta"]["revision"] == 2
    first = out["fetchCalls"][0]
    assert first["url"] == "/api/sessions/SESS-1/private-followup"
    body = json.loads(first["body"])
    assert body == {
        "pending_id": "pf-9", "revision": 2, "choice": "approve"}
    assert out["loaded"] == ["SESS-1"]


def test_busy_blocks_double_click(node: str) -> None:
    out = _run(node)
    assert out["fetchCalls"][0]["url"].endswith("private-followup")
    assert out["afterDoubleClick"] == 1


def test_non2xx_surfaces_error_and_resets(node: str) -> None:
    out = _run(node)
    assert out["non2xx"]["statusText"]
    assert out["non2xx"]["sends"][-1] is False
    body = json.loads(out["non2xx"]["lastCall"]["body"])
    assert body["choice"] == "keep_private"


def test_ordinary_card_passes_label_without_metadata(node: str) -> None:
    out = _run(node)
    assert out["ordinary"]["chosen"]["label"] == "Yes"
    assert out["ordinary"]["chosen"]["meta"] is None
    assert out["ordinary"]["consentHits"] == 0
    assert out["ordinary"]["newFetches"] == 0
