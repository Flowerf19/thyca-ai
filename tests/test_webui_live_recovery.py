"""Execute app.js follow/poll recovery with real view and thinking modules.

Only the browser environment and transport are mocked; no source-text behavior
assertions and no DOM dependency. The clock is deterministic, not wall-time.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = r"""
import fs from 'node:fs';
import assert from 'node:assert/strict';

// Minimal DOM surface used by the production chat view and thinking panel.
class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = {};
    this.className = '';
    this.value = '';
    this.text = '';
    this.classList = {
      contains: name => this.className.split(' ').includes(name),
      add: (...names) => {
        this.className = [...new Set([...this.className.split(' '), ...names])].join(' ');
      },
      remove: (...names) => {
        this.className = this.className.split(' ').filter(name => !names.includes(name)).join(' ');
      },
      toggle: (name, force) => {
        if (force ?? !this.classList.contains(name)) this.classList.add(name);
        else this.classList.remove(name);
      },
    };
  }
  get isConnected() { return Boolean(this.root || this.parentNode?.isConnected); }
  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter(node => node !== this);
    }
    this.parentNode = null;
  }
  append(...nodes) {
    for (const node of nodes) {
      node.remove();
      node.parentNode = this;
      this.children.push(node);
    }
  }
  prepend(...nodes) {
    for (const node of [...nodes].reverse()) {
      node.remove();
      node.parentNode = this;
      this.children.unshift(node);
    }
  }
  after(...nodes) {
    const parent = this.parentNode;
    if (!parent) return;
    const index = parent.children.indexOf(this);
    for (const node of [...nodes].reverse()) {
      node.remove();
      node.parentNode = parent;
      parent.children.splice(index + 1, 0, node);
    }
  }
  replaceChildren(...nodes) {
    for (const node of [...this.children]) node.remove();
    this.text = '';
    this.append(...nodes);
  }
  set textContent(value) { this.replaceChildren(); this.text = String(value); }
  get textContent() { return this.text + this.children.map(node => node.textContent).join(''); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  querySelectorAll(selector) {
    const matches = node => selector.startsWith('.')
      ? node.classList.contains(selector.slice(1)) : node.tag === selector;
    return this.children.flatMap(node => [
      ...(matches(node) ? [node] : []), ...node.querySelectorAll(selector),
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  addEventListener() {}
  focus() {}
  scrollTo() {}
}
const roots = new Map();
globalThis.document = {
  createElement: tag => new Element(tag),
  createElementNS: (_namespace, tag) => new Element(tag),
  querySelector(selector) {
    if (!roots.has(selector)) {
      const node = new Element();
      node.root = true;
      roots.set(selector, node);
    }
    return roots.get(selector);
  },
};
globalThis.sessionStorage = { setItem() {}, removeItem() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = callback => callback();
let now = Date.parse('2026-09-18T00:00:00Z');
Date.now = () => now;
let serial = 0;
const intervals = new Map();
const timeouts = new Map();
globalThis.setInterval = callback => { intervals.set(++serial, callback); return serial; };
globalThis.clearInterval = id => intervals.delete(id);
globalThis.window = {
  setTimeout(callback) { timeouts.set(++serial, callback); return serial; },
  clearTimeout: id => timeouts.delete(id),
};
const settle = () => new Promise(resolve => setImmediate(resolve));
function tickClock() {
  now += 2000;
  for (const callback of [...intervals.values()]) callback();
}
async function poll() {
  assert.equal(timeouts.size, 1, 'exactly one fallback poll must be scheduled');
  const [id, callback] = timeouts.entries().next().value;
  timeouts.delete(id);
  callback();
  await settle();
}

const view = await import('./thyca/webui/pages/chat/chat-view.js');
const format = await import('./thyca/webui/shared/js/format.js');
let detail = {
  id: 'A', title: 'A', running: true,
  started_at: new Date(now).toISOString(), messages: [],
};
const cards = [];
let followFailures = 0;
const dependencies = {
  ...view, ...format,
  createLiveStatus(...args) {
    const live = view.createLiveStatus(...args);
    cards.push(live);
    return live;
  },
  async getJson(url) {
    if (url === '/api/sessions') return { sessions: [] };
    if (url === '/api/sessions/A') return detail;
    if (url === '/api/sessions/B') return { id: 'B', running: false, messages: [] };
    throw new Error(`Unexpected GET: ${url}`);
  },
  async getNdjson() {
    followFailures++;
    throw new Error('follow connection interrupted');
  },
};
// Inject transport in place of imports, suppress startup I/O, and expose the
// existing lifecycle entry points. Their production function bodies are intact.
const source = fs.readFileSync('./thyca/webui/pages/chat/app.js', 'utf8')
  .replace(/^import[\s\S]*?from .*?;\n/gm, '')
  .replace('void boot();', '');
const app = new Function(...Object.keys(dependencies), source + `
  return { loadSession, state, el, liveTurns };
`)(...Object.values(dependencies));
const visibleReasoning = () => app.el.messageList.querySelectorAll('.thought-output')
  .map(node => node.textContent).join('|');
const result = {};

await app.loadSession('A');
await settle();
const original = cards[0];
result.initial = {
  followFailures, running: app.state.running, reasoning: visibleReasoning(),
  tracked: app.liveTurns.get('A') === original,
};

const assistant = reasoning => ({ role: 'assistant', reasoning });
detail = { ...detail, messages: [assistant('saved first round')] };
await poll();
result.firstPoll = { reasoning: visibleReasoning(), running: app.state.running };
detail = { ...detail, messages: [...detail.messages, assistant('saved second round')] };
await poll();
result.secondPoll = {
  reasoning: visibleReasoning(), running: app.state.running,
  tracked: app.liveTurns.get('A') === original,
};

await app.loadSession('B');
await settle();
tickClock();
result.away = { connected: original.article.isConnected, timers: intervals.size };
await app.loadSession('A');
await settle();
const before = original.article.querySelector('.chat-brand-status').textContent;
tickClock();
const after = original.article.querySelector('.chat-brand-status').textContent;
result.returned = {
  sameCard: app.liveTurns.get('A') === original,
  connected: original.article.isConnected, before, after,
};

// The backend marks the turn stopped; its final transcript is authoritative.
detail = { ...detail, running: false };
await poll();
tickClock();
result.stopped = {
  running: app.state.running, reasoning: visibleReasoning(),
  tracked: app.liveTurns.has('A'), liveCards: app.el.messageList.querySelectorAll('.live-status').length,
  timers: intervals.size, polls: timeouts.size,
};
console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def recovery() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, "--input-type=module", "-e", _SCRIPT],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_running_polls_render_saved_reasoning_before_stop(recovery: dict) -> None:
    assert recovery["initial"]["followFailures"] == 1
    assert recovery["initial"]["running"] is True
    assert recovery["initial"]["reasoning"] == ""
    assert recovery["firstPoll"]["running"] is True
    assert "saved first round" in recovery["firstPoll"]["reasoning"]
    assert recovery["secondPoll"]["running"] is True
    assert "saved first round" in recovery["secondPoll"]["reasoning"]
    assert "saved second round" in recovery["secondPoll"]["reasoning"]


def test_fallback_keeps_live_card_and_resumes_clock_on_return(recovery: dict) -> None:
    assert recovery["initial"]["tracked"] is True
    assert recovery["secondPoll"]["tracked"] is True
    assert recovery["away"] == {"connected": False, "timers": 0}
    assert recovery["returned"]["sameCard"] is True
    assert recovery["returned"]["connected"] is True
    assert recovery["returned"]["after"] != recovery["returned"]["before"]
    assert "4 giây" in recovery["returned"]["after"]


def test_terminal_poll_renders_transcript_and_releases_live_card(recovery: dict) -> None:
    assert recovery["stopped"] == {
        "running": False,
        "reasoning": "saved first round|saved second round",
        "tracked": False,
        "liveCards": 0,
        "timers": 0,
        "polls": 0,
    }
