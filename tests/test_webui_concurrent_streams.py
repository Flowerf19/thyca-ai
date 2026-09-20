"""Execute app.js concurrent-send ownership with real view and thinking modules.

Two turns can stream at once (send in A, switch to B, send there). Returning
to A must reuse its live card without opening a second reader for a stream
this tab already owns — a second reader replays the same deltas and duplicates
every one of them on the card.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Shared minimal DOM surface used by the production chat view and thinking
# panel, plus the settle helper; prepended to every scenario script.
_HARNESS = r"""
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
Date.now = () => Date.parse('2026-09-18T00:00:00Z');
const intervals = new Map();
let serial = 0;
globalThis.setInterval = callback => { intervals.set(++serial, callback); return serial; };
globalThis.clearInterval = id => intervals.delete(id);
globalThis.window = {
  setTimeout(callback) { intervals.set(++serial, callback); return serial; },
  clearTimeout: id => intervals.delete(id),
};
const settle = async () => {
  for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve));
};
"""

_SCRIPT = r"""
import fs from 'node:fs';
""" + _HARNESS + r"""
const view = await import('./thyca/webui/backend/chat-view.js');
const format = await import('./thyca/webui/backend/format.js');
const followCalls = [];
const posts = [];
let resolveA;
let aRunning = false;
const doneB = { id: 'B', running: false, messages: [{ role: 'assistant', content: 'done B' }] };
const dependencies = {
  ...view, ...format,
  SEND_ERROR_STATUS: 'Không gửi được.',
  ApiError: class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  },
  async getJson(url) {
    if (url === '/api/sessions') return { sessions: [] };
    const id = url.split('/').pop();
    return { id, running: id === 'A' && aRunning, messages: [] };
  },
  async getNdjson(url) {
    followCalls.push(url);
    throw new Error('follow must not open for a stream this tab owns');
  },
  async postJson() { return {}; },
  async postNdjson(url, _body, onEvent) {
    posts.push(url);
    if (url.includes('/A/')) {
      onEvent({ type: 'turn.accepted' });
      onEvent({ type: 'llm.thinking', round: 1, delta: 'A-one ' });
      onEvent({ type: 'tool.started', name: 'bash', call_id: 't1' });
      return new Promise(resolve => { resolveA = resolve; });
    }
    onEvent({ type: 'turn.accepted' });
    onEvent({ type: 'llm.thinking', round: 1, delta: 'B-one' });
    onEvent({ type: 'turn.completed', detail: doneB });
    return doneB;
  },
};
const source = fs.readFileSync('./thyca/webui/app.js', 'utf8')
  .replace(/^import[\s\S]*?from .*?;\n/gm, '')
  .replace('void boot();', '');
const app = new Function(...Object.keys(dependencies), source + `
  return { loadSession, sendMessage, state, el, liveTurns, streamingSessions };
`)(...Object.values(dependencies));
const result = {};
const cardText = () => [...app.liveTurns.values()]
  .map(live => live.thinking?.body.textContent).join('|');

await app.loadSession('A');
await settle();
app.el.input.value = 'hi A';
const pendingA = app.sendMessage();
await settle();
result.a = {
  posts: posts.length, text: cardText(),
  tracked: app.streamingSessions.has('A'),
};

await app.loadSession('B');
await settle();
app.el.input.value = 'hi B';
await app.sendMessage();
await settle();
result.b = { posts: posts.length, tracked: [...app.streamingSessions] };

await app.loadSession('A');
await settle();
result.backToA = {
  followCalls: followCalls.length,
  text: cardText(),
  tracked: app.streamingSessions.has('A'),
};

aRunning = true;
resolveA({
  id: 'A', running: false,
  messages: [{ role: 'assistant', content: 'done A', reasoning: 'A-thought' }],
});
await pendingA;
await settle();
result.doneA = {
  followCalls: followCalls.length,
  reasoning: app.el.messageList.querySelectorAll('.thought-output')
    .map(node => node.textContent).join('|'),
  tracked: [...app.streamingSessions],
};
console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def concurrent() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, "--input-type=module", "-e", _SCRIPT],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_second_send_keeps_first_stream_ownership(concurrent: dict) -> None:
    assert concurrent["a"] == {"posts": 1, "text": "A-one Đang dùng: bash", "tracked": True}
    assert concurrent["b"]["posts"] == 2
    assert concurrent["b"]["tracked"] == ["A"]


def test_returning_to_streaming_session_reuses_card_without_second_reader(
    concurrent: dict,
) -> None:
    assert concurrent["backToA"]["followCalls"] == 0
    assert concurrent["backToA"]["text"] == "A-one Đang dùng: bash"
    assert concurrent["backToA"]["tracked"] is True


def test_finishing_the_first_stream_renders_once_and_cleans_up(
    concurrent: dict,
) -> None:
    assert concurrent["doneA"]["followCalls"] == 0
    assert concurrent["doneA"]["reasoning"] == "A-thought"
    assert concurrent["doneA"]["tracked"] == []


# A background turn completion refreshes the sidebar; the reveal of the
# session's row must respect what the user did to the pager meanwhile.
_REVEAL_SCRIPT = r"""
import fs from 'node:fs';
""" + _HARNESS + r"""
const view = await import('./thyca/webui/backend/chat-view.js');
const format = await import('./thyca/webui/backend/format.js');
// 25 sessions fill three 12-per-page pages; a session created by the turn
// lands at the end (page 3) only once the turn completes.
const sessions = Array.from({ length: 25 }, (_, i) => ({
  id: `s${i}`, title: `Phiên ${i}`, updated_at: '', turns: 1,
}));
const created = { id: 'new', title: 'Phiên mới', updated_at: '', turns: 1 };

function makeApp(turnId) {
  let turnDone = false;
  let release;
  const deps = {
    ...view, ...format,
    SEND_ERROR_STATUS: 'Không gửi được.',
    ApiError: class ApiError extends Error {
      constructor(message, status) { super(message); this.status = status; }
    },
    async getJson(url) {
      if (url === '/api/sessions') {
        return { sessions: turnDone ? [...sessions, created] : sessions };
      }
      return { id: url.split('/').pop(), running: false, messages: [] };
    },
    async getNdjson() { throw new Error('follow must not run here'); },
    async postJson() { return { id: turnId }; },
    async postNdjson(_url, _body, onEvent) {
      onEvent({ type: 'turn.accepted' });
      return new Promise(resolve => {
        release = () => resolve({ id: turnId, running: false, messages: [] });
      });
    },
    async patchJson() { return {}; },
    async deleteJson() { return {}; },
  };
  const source = fs.readFileSync('./thyca/webui/app.js', 'utf8')
    .replace(/^import[\s\S]*?from .*?;\n/gm, '')
    .replace('void boot();', '');
  const app = new Function(...Object.keys(deps), source + `
    return { state, el, loadSession, sendMessage, refreshSessions };
  `)(...Object.values(deps));
  return { app, finish: () => { turnDone = true; release(); } };
}

const result = {};

// The user pages to the last page mid-turn: completion must leave the pager
// where they put it.
const paged = makeApp('s0');
await paged.app.refreshSessions();
await paged.app.loadSession('s0');
await settle();
paged.app.el.input.value = 'xin chào';
const pendingPaged = paged.app.sendMessage();
await settle();
paged.app.state.sessionPage = 3;
paged.finish();
await pendingPaged;
await settle();
result.paged = { page: paged.app.state.sessionPage, active: paged.app.state.activeId };

// The user opens another session mid-turn: completion must not flip to the
// finished session's page.
const navigated = makeApp('s0');
await navigated.app.refreshSessions();
await navigated.app.loadSession('s0');
await settle();
navigated.app.el.input.value = 'xin chào';
const pendingNavigated = navigated.app.sendMessage();
await settle();
await navigated.app.loadSession('s20');
await settle();
navigated.finish();
await pendingNavigated;
await settle();
result.navigated = { page: navigated.app.state.sessionPage, active: navigated.app.state.activeId };

// Still on the composer with the just-created session: the completion may
// reveal its page.
const stayed = makeApp('new');
await stayed.app.refreshSessions();
await settle();
stayed.app.el.input.value = 'xin chào';
const pendingStayed = stayed.app.sendMessage();
await settle();
stayed.finish();
await pendingStayed;
await settle();
result.stayed = { page: stayed.app.state.sessionPage, active: stayed.app.state.activeId };

console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def reveal() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, "--input-type=module", "-e", _REVEAL_SCRIPT],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=15,
    )
    return json.loads(result.stdout)


def test_background_completion_leaves_pager_where_the_user_put_it(reveal: dict) -> None:
    assert reveal["paged"] == {"page": 3, "active": "s0"}


def test_background_completion_does_not_reveal_after_navigation(reveal: dict) -> None:
    assert reveal["navigated"] == {"page": 2, "active": "s20"}


def test_completion_still_reveals_just_created_session(reveal: dict) -> None:
    assert reveal["stayed"] == {"page": 3, "active": "new"}
