"""Execute the live thinking panel across LLM rounds with real modules.

Each LLM round gets its own thinking segment: round 2 must start a fresh
note below round 1's, whose text and tool line stay (like the settled
transcript). A re-follow rebuilds segments instead of duplicating them.
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

const view = await import('./thyca/webui/backend/chat-view.js');
const host = new Element(); host.root = true;
const live = view.createLiveStatus(host, Date.now() - 12000);
const segments = () => live.notes.map(note => ({
  text: note.body.querySelector('.thought-output').textContent,
  tool: note.body.querySelector('.tool-status').textContent,
  caretSettled: note.body.querySelector('.streaming-caret')?.hidden === true,
}));

view.updateLiveStatus(live, { type: 'llm.started', round: 1 });
view.updateLiveStatus(live, { type: 'llm.thinking', round: 1, delta: 'round one ' });
view.updateLiveStatus(live, { type: 'llm.thinking', round: 1, delta: 'thoughts' });
view.updateLiveStatus(live, { type: 'llm.content', round: 1, delta: 'Xin ' });
view.updateLiveStatus(live, { type: 'llm.content', round: 1, delta: 'chào' });
view.updateLiveStatus(live, { type: 'tool.started', name: 'bash', call_id: 'b1' });
view.updateLiveStatus(live, { type: 'tool.finished', name: 'bash', call_id: 'b1' });
const afterRound1 = segments();
const replyTexts = () => [...live.notesWrap.querySelectorAll('.reply-live')].map(n => n.textContent);

view.updateLiveStatus(live, { type: 'llm.started', round: 2 });
const atRound2Start = { segments: segments(), replies: replyTexts() };
view.updateLiveStatus(live, { type: 'llm.thinking', round: 2, delta: 'round two' });
view.updateLiveStatus(live, { type: 'llm.content', round: 2, delta: 'round two reply' });
const afterRound2 = { segments: segments(), replies: replyTexts() };

view.updateLiveStatus(live, { type: 'turn.completed' });
const afterCompleted = segments();

view.resetLiveStatus(live, live.startedAt);
const afterRefollowReset = segments();
console.log(JSON.stringify({ afterRound1, atRound2Start, afterRound2, afterCompleted, afterRefollowReset }));
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


def test_round_one_streams_and_keeps_tool_line(rounds: dict) -> None:
    assert rounds["afterRound1"] == [
        {"text": "round one thoughts", "tool": "Đã dùng: bash x1", "caretSettled": False},
    ]


def test_round_two_adds_a_segment_and_keeps_the_previous(rounds: dict) -> None:
    assert rounds["atRound2Start"] == {
        "segments": [
            {"text": "round one thoughts", "tool": "Đã dùng: bash x1", "caretSettled": True},
            {"text": "", "tool": "Đã dùng: bash x1", "caretSettled": False},
        ],
        "replies": ["Xin chào"],
    }
    assert rounds["afterRound2"] == {
        "segments": [
            {"text": "round one thoughts", "tool": "Đã dùng: bash x1", "caretSettled": True},
            {"text": "round two", "tool": "Đã dùng: bash x1", "caretSettled": False},
        ],
        "replies": ["Xin chào", "round two reply"],
 }


def test_completion_settles_every_segment(rounds: dict) -> None:
    assert all(segment["caretSettled"] for segment in rounds["afterCompleted"])


def test_refollow_rebuilds_segments_instead_of_duplicating(rounds: dict) -> None:
    assert rounds["afterRefollowReset"] == [
        {"text": "", "tool": "", "caretSettled": False},
    ]
