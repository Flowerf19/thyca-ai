"""Execute the live turn card with real modules.

The live card is minimal: brand header + streaming reply only. Live
activity (đang nghĩ…, tool calls) is routed to the bot card's sidebar
description instead of rendering notes/usage rows in the chat. A
re-follow rebuilds the reply instead of duplicating it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = r"""
import assert from 'node:assert/strict';

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = {};
    this.className = '';
    this.style = { setProperty: () => {}, getPropertyValue: () => '' };
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
  scrollTo() {}
}
globalThis.document = {
  createElement: tag => new Element(tag),
  createElementNS: (_namespace, tag) => new Element(tag),
  createTextNode: text => {
    const node = new Element('#text');
    node.text = String(text);
    return node;
  },
  querySelector: () => { const node = new Element(); node.root = true; return node; },
};
globalThis.requestAnimationFrame = callback => callback();
const intervals = new Map();
let serial = 0;
globalThis.setInterval = callback => { intervals.set(++serial, callback); return serial; };
globalThis.clearInterval = id => intervals.delete(id);

const view = await import('./thyca/webui/pages/chat/chat-view.js');
const host = new Element(); host.root = true;
const live = view.createLiveStatus(host, Date.now() - 12000);
const replyTexts = () => [...host.querySelectorAll('.reply-live')].map(n => n.textContent);
const forbidden = () => ({
  notes: host.querySelectorAll('.thinking-note').length,
  usage: host.querySelectorAll('.usage-row').length,
});

view.updateLiveStatus(live, { type: 'llm.started', round: 1 });
view.updateLiveStatus(live, { type: 'llm.thinking', round: 1, delta: 'round one ' });
view.updateLiveStatus(live, { type: 'llm.thinking', round: 1, delta: 'thoughts' });
view.updateLiveStatus(live, { type: 'llm.content', round: 1, delta: 'Xin ' });
view.updateLiveStatus(live, { type: 'llm.content', round: 1, delta: 'chào' });
view.updateLiveStatus(live, { type: 'tool.started', name: 'bash', call_id: 'b1' });
view.updateLiveStatus(live, { type: 'tool.finished', name: 'bash', call_id: 'b1' });
const afterRound1 = { replies: replyTexts(), forbidden: forbidden() };

view.updateLiveStatus(live, { type: 'llm.started', round: 2 });
view.updateLiveStatus(live, { type: 'llm.content', round: 2, delta: ' round two' });
const afterRound2 = { replies: replyTexts(), forbidden: forbidden() };

view.updateLiveStatus(live, { type: 'turn.completed' });
const afterCompleted = { replies: replyTexts(), forbidden: forbidden() };

view.resetLiveStatus(live, live.startedAt);
const afterRefollowReset = replyTexts();
console.log(JSON.stringify({ afterRound1, afterRound2, afterCompleted, afterRefollowReset }));
"""


@pytest.fixture(scope="module")
def rounds() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, "--input-type=module", "-e", _SCRIPT],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_live_card_streams_reply_only_no_notes_or_usage(rounds: dict) -> None:
    assert rounds["afterRound1"] == {
        "replies": ["Xin chào"],
        "forbidden": {"notes": 0, "usage": 0},
    }


def test_second_round_appends_to_the_same_reply(rounds: dict) -> None:
    assert rounds["afterRound2"] == {
        "replies": ["Xin chào round two"],
        "forbidden": {"notes": 0, "usage": 0},
    }


def test_completion_keeps_the_streamed_reply(rounds: dict) -> None:
    assert rounds["afterCompleted"] == {
        "replies": ["Xin chào round two"],
        "forbidden": {"notes": 0, "usage": 0},
    }


def test_refollow_rebuilds_the_reply_instead_of_duplicating(rounds: dict) -> None:
    assert rounds["afterRefollowReset"] == []


_FOLD_SCRIPT = r"""
import assert from 'node:assert/strict';

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = {};
    this.className = '';
    this.style = { setProperty: () => {}, getPropertyValue: () => '' };
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
  scrollTo() {}
}
globalThis.document = {
  createElement: tag => new Element(tag),
  createElementNS: (_namespace, tag) => new Element(tag),
  createTextNode: text => {
    const node = new Element('#text');
    node.text = String(text);
    return node;
  },
  querySelector: () => { const node = new Element(); node.root = true; return node; },
};

const view = await import('./thyca/webui/pages/chat/chat-view.js');
// Same shape as the reported turn: think+search, then a silent bash round,
// content, another silent bash round, content.
const messages = [
  { role: 'assistant', reasoning: 'User wants sequential: search, reply, check.',
    tool_calls: [{ name: 'tavily-search__web_search' }] },
  { role: 'assistant', tool_calls: [{ name: 'bash' }] },
  { role: 'assistant', content: 'Search xong rồi. Vài tin hôm nay:' },
  { role: 'assistant', tool_calls: [{ name: 'bash' }] },
  { role: 'assistant', content: 'Nhiệt độ: 45°C — vẫn mát.' },
];
const root = new Element(); root.root = true;
view.renderConversation(root, messages);
const notes = [...root.querySelectorAll('.thinking-note')];
const article = root.querySelector('.message-assistant');
console.log(JSON.stringify({
  notes: notes.map(note => ({
    text: note.querySelector('.thought-output')?.textContent ?? '',
    hidden: note.hidden === true,
  })),
  usageRows: root.querySelectorAll('.usage-row').map(node => ({
    label: node.querySelector('.usage-label')?.textContent ?? '',
    body: node.querySelector('.usage-row-body')?.textContent ?? '',
  })),
  contentCount: root.querySelectorAll('.live-copy').length,
  order: [...article.children].map(node =>
    node.classList.contains('thinking-note') ? 'note'
    : node.classList.contains('usage-row') ? 'usage'
    : node.classList.contains('live-copy') ? 'content' : node.tag),
}));
"""


@pytest.fixture(scope="module")
def folded() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, "--input-type=module", "-e", _FOLD_SCRIPT],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_silent_tool_rounds_fold_into_the_previous_thinking_line(folded: dict) -> None:
    assert folded["notes"] == [
        {"text": "User wants sequential: search, reply, check.", "hidden": False},
    ]
    assert folded["usageRows"] == [
        {"label": "Đã dùng:", "body": "tavily-search__web_search x1, bash x1"},
        {"label": "Đã dùng:", "body": "bash x1"},
    ]
    assert folded["contentCount"] == 2


def test_tool_line_after_content_stays_attached_to_that_content(folded: dict) -> None:
    assert folded["order"] == [
        "header",
        "note",
        "usage",
        "content",
        "content",
        "usage",
        "footer",
    ]
