"""Execute memories.js inline-edit concurrency with a minimal DOM harness.

Mobile renders one editor copy per matching view section, so a mutation in
flight must disable every rendered (and newly rendered) editor control, guard
start/cancel, and clear only the submitted draft identity. Focus must land on
the editor the user actually opened and return to its row action on cancel.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Minimal DOM surface used by memories.js: enough selector support for the
# compound/comma selectors the page itself issues (.memory-edit button, ...),
# closest() for the section-scoped focus helpers, and offsetParent semantics
# that treat a [hidden] subtree as invisible.
_HARNESS = r"""
class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.parentNode = null;
    this.root = false;
    this.dataset = {};
    this.attributes = {};
    this.className = '';
    this.value = '';
    this.text = '';
    this.hidden = false;
    this.disabled = false;
    this.id = '';
    this.listeners = {};
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
  get offsetParent() {
    if (!this.isConnected) return null;
    for (let node = this; node; node = node.parentNode) if (node.hidden) return null;
    return this.parentNode || this;
  }
  get nextElementSibling() {
    if (!this.parentNode) return null;
    return this.parentNode.children[this.parentNode.children.indexOf(this) + 1] ?? null;
  }
  append(...nodes) {
    for (const node of nodes) {
      node.remove();
      node.parentNode = this;
      this.children.push(node);
    }
  }
  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter(node => node !== this);
    }
    this.parentNode = null;
  }
  insertBefore(node, reference) {
    node.remove();
    const index = this.children.indexOf(reference);
    if (index === -1) { this.append(node); return node; }
    node.parentNode = this;
    this.children.splice(index, 0, node);
    return node;
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
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  focus() { FOCUSED = this; }
  closest(selector) {
    for (let node = this; node; node = node.parentNode) {
      if (matches(node, selector)) return node;
    }
    return null;
  }
  querySelectorAll(selector) { return collect(this, selector, false); }
  querySelector(selector) { return collect(this, selector, false)[0] ?? null; }
}

let FOCUSED = null;

const TOKEN = /([a-zA-Z][\w-]*|\.[\w-]+|#[\w-]+|\[[^\]]+\])/g;
function simpleMatches(node, simple) {
  for (const token of simple.match(TOKEN) ?? []) {
    if (token.startsWith('.')) { if (!node.classList.contains(token.slice(1))) return false; }
    else if (token.startsWith('#')) { if (node.id !== token.slice(1)) return false; }
    else if (token.startsWith('[')) {
      const inner = token.slice(1, -1);
      const eq = inner.indexOf('=');
      const name = eq === -1 ? inner : inner.slice(0, eq);
      const expected = eq === -1 ? null : inner.slice(eq + 1).replace(/^"|"$/g, '');
      const actual = name.startsWith('data-')
        ? node.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())]
        : node.attributes[name];
      if (expected === null ? actual === undefined : actual !== expected) return false;
    } else if (node.tag !== token) return false;
  }
  return true;
}

// Descendant combinator: the last compound must match the node itself, the
// earlier ones some ancestor chain in order.
function matches(node, selector) {
  return selector.split(',').some(part => chainMatches(node, part.trim().split(/\s+/)));
}
function chainMatches(node, compounds) {
  if (!simpleMatches(node, compounds[compounds.length - 1])) return false;
  const rest = compounds.slice(0, -1);
  if (!rest.length) return true;
  for (let parent = node.parentNode; parent; parent = parent.parentNode) {
    if (chainMatches(parent, rest)) return true;
  }
  return false;
}
function collect(root, selector, includeSelf) {
  const out = [];
  const walk = node => {
    if (matches(node, selector) && (includeSelf || node !== root)) out.push(node);
    node.children.forEach(walk);
  };
  walk(root);
  return out;
}

const forest = [];
const mount = node => { node.root = true; forest.push(node); return node; };
globalThis.document = {
  createElement: tag => new Element(tag),
  createElementNS: (_namespace, tag) => new Element(tag),
  querySelectorAll(selector) { return forest.flatMap(root => collect(root, selector, true)); },
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; },
};

const list = mount(new Element('ul'));
list.id = 'memory-list';
const empty = mount(new Element('p'));
empty.id = 'memory-empty';
const status = mount(new Element('p'));
status.id = 'memory-status';
const sections = ['overview', 'day', 'used-more', 'searched-more', 'used-less'].map(view => {
  const section = new Element('section');
  section.className = 'memory-view';
  section.dataset.view = view;
  const body = new Element('div');
  body.className = 'memory-view-body';
  section.append(body);
  mount(section);
  return section;
});
const bar = mount(new Element('div'));
bar.className = 'memory-search-bar';
const cluster = new Element('div');
cluster.className = 'memory-search-cluster';
const search = new Element('input');
search.id = 'memory-search';
cluster.append(search);
bar.append(cluster);
const sessionsAside = mount(new Element('section'));
sessionsAside.className = 'sessions';
const sessionList = new Element('nav');
sessionList.className = 'session-list';
sessionsAside.append(sessionList);
const viewButtons = ['overview', 'day', 'used-more', 'searched-more', 'used-less'].map(view => {
  const button = new Element('button');
  button.className = 'session-item';
  button.dataset.view = view;
  mount(button);
  return button;
});

// Start on the mobile layout: bind()'s placeSearch then takes the simple
// branch, and every editor-copy scenario lives there.
const media = {
  matches: true,
  listeners: {},
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
};
globalThis.matchMedia = () => media;
globalThis.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
globalThis.confirm = () => true;
const settle = async () => {
  for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve));
};

// Transport and data helpers are behind hooks so each scenario can swap them
// while the page module holds only the wrapper bindings.
const hooks = { leaves: [], getJson: async () => ({}), postJson: async () => ({}) };
const calls = { gets: [], posts: [] };
const deps = {
  getJson: async url => { calls.gets.push(url); return hooks.getJson(url); },
  postJson: async (url, body) => { calls.posts.push({ url, body }); return hooks.postJson(url, body); },
  completeDays: rows => rows,
  rollingRange: () => [],
  drawBarChart: () => {},
  formatDate: value => value,
  formatDateTime: value => value,
  formatInteger: value => String(value),
  selectMemories: () => hooks.leaves, // every view shows every leaf
};
const source = fs.readFileSync('./thyca/webui/memories.js', 'utf8')
  .replace(/^import[\s\S]*?from .*?;\n/gm, '');
const page = new Function(...Object.keys(deps), source + `
  return { state, el, render, startEdit, cancelEdit, mutateMemory };
`)(...Object.values(deps));

const inSection = view => sections.find(section => section.dataset.view === view);
const editors = () => document.querySelectorAll('.memory-edit');
const controls = () => editors().flatMap(form => form.querySelectorAll('button, input, textarea'));
const visibleEditors = () => editors().filter(form => form.offsetParent !== null);
const visibleControls = () => controls().filter(control => control.offsetParent !== null);
const fire = (node, type, event = {}) => {
  event.preventDefault ??= () => {};
  event.stopPropagation ??= () => {};
  for (let n = node; n; n = n.parentNode) {
    event.currentTarget = n;
    for (const fn of n.listeners[type] ?? []) fn(event);
  }
};
const leaf = {
  id: 'm1', sessionId: 's1', title: 'Trang một', description: 'Nội dung một',
  uses: 1, searches: 2, date: '2026-09-20',
};
const openEditor = view => fire(inSection(view).querySelectorAll('.row-action')[0], 'click');
"""

_SCENARIO_COPIES = r"""
await settle();
hooks.leaves = [leaf];
page.render();
openEditor('day');
const result = { editors: editors().length, editingId: page.state.editing?.id };

let release;
hooks.postJson = () => new Promise(resolve => { release = resolve; });
const saving = page.mutateMemory('/api/memory/update', { session_id: 's1', topic: 'T', summary: 'S' }, 'Đã cập nhật');
await settle();
result.controlCount = controls().length;
result.pendingAllDisabled = controls().length > 0 && controls().every(control => control.disabled);

// A search keystroke during the pending mutation re-renders every section;
// the fresh editor copies must come back disabled with the draft intact.
search.value = 'một';
fire(search, 'input');
result.afterSearchEditors = editors().length;
result.afterSearchAllDisabled = controls().every(control => control.disabled);
result.draftAfterSearch = page.state.editing?.topic;

release({});
await saving;
await settle();
result.afterSuccessEditors = editors().length;
result.editingCleared = page.state.editing === null;
result.postCount = calls.posts.length;
console.log(JSON.stringify(result));
"""

_SCENARIO_RESIZE_ERROR = r"""
await settle();
hooks.leaves = [leaf];
page.render();
openEditor('used-more');
const draft = page.state.editing;
let release;
let scenarioReject;
hooks.postJson = () => new Promise((resolve, reject) => { release = resolve; scenarioReject = reject; });
const saving = page.mutateMemory('/api/memory/update', { session_id: 's1', topic: 'T', summary: 'S' }, 'Đã cập nhật');
await settle();

// Resize across the breakpoint mid-mutation: the desktop re-render (day view)
// must rebuild the single editor disabled, not live.
media.matches = false;
fire(viewButtons.find(button => button.dataset.view === 'day'), 'click');
fire(media, 'change');
const desktop = { editors: visibleEditors().length, allDisabled: visibleControls().every(control => control.disabled) };

// And back to mobile: four fresh copies, still disabled.
media.matches = true;
fire(media, 'change');
const mobile = { editors: visibleEditors().length, allDisabled: visibleControls().every(control => control.disabled) };

scenarioReject(new Error('Không lưu được.'));
await saving;
await settle();
const failed = {
  draftKept: page.state.editing === draft,
  topic: page.state.editing?.topic,
  reEnabled: visibleControls().length > 0 && visibleControls().every(control => !control.disabled),
  pendingFalse: page.state.pending === false,
  status: status.textContent,
};
console.log(JSON.stringify({ desktop, mobile, failed }));
"""

_SCENARIO_CONCURRENCY = r"""
await settle();
hooks.leaves = [leaf];
page.render();
openEditor('searched-more');
let release;
hooks.postJson = () => new Promise(resolve => { release = resolve; });
const saving = page.mutateMemory('/api/memory/update', { session_id: 's1', topic: 'T', summary: 'S' }, 'Đã cập nhật');
await settle();

// Save (duplicate submit), reinforce and forget while one mutation is in
// flight: every path must be refused, nothing posted twice.
const form = inSection('searched-more').querySelector('.memory-edit');
fire(form, 'submit');
const buttons = form.querySelectorAll('button');
fire(buttons.find(button => button.textContent === 'Gia hạn'), 'click');
fire(buttons.find(button => button.textContent === 'Quên'), 'click');
const during = { posts: calls.posts.length };

release({});
await saving;
await settle();

// startEdit and cancelEdit are refused while a mutation is pending.
openEditor('day');
const guardDraft = page.state.editing;
let releaseGuard;
hooks.postJson = () => new Promise(resolve => { releaseGuard = resolve; });
const guardSaving = page.mutateMemory('/api/memory/update', { session_id: 's1', topic: 'T', summary: 'S' }, 'Đã cập nhật');
await settle();
openEditor('used-less'); // startEdit during pending
fire(inSection('day').querySelector('.memory-edit'), 'keydown', { key: 'Escape', isComposing: false }); // cancel during pending
const guards = {
  editingUntouched: page.state.editing === guardDraft,
  posts: calls.posts.length,
};
releaseGuard({});
await guardSaving;
await settle();
console.log(JSON.stringify({ during, guards, totalPosts: calls.posts.length }));
"""

_SCENARIO_IDENTITY = r"""
await settle();
hooks.leaves = [leaf, { ...leaf, id: 'm2', sessionId: 's2', title: 'Trang hai', description: 'Hai' }];
page.render();
openEditor('day');
const submitted = page.state.editing;
let release;
hooks.postJson = () => new Promise(resolve => { release = resolve; });
const saving = page.mutateMemory('/api/memory/update', { session_id: 's1', topic: 'T', summary: 'S' }, 'Đã cập nhật');
await settle();

// A newer draft replaces the submitted one while the request is in flight;
// success clears only the submitted identity.
const newer = { id: 'm2', sessionId: 's2', topic: 'Bản mới', summary: 'Bản mới' };
page.state.editing = newer;
page.render();
release({});
await saving;
await settle();
console.log(JSON.stringify({
  newerKept: page.state.editing === newer,
  editors: editors().length,
  allEnabled: controls().length > 0 && controls().every(control => !control.disabled),
}));
"""

_SCENARIO_FOCUS = r"""
await settle();
hooks.leaves = [leaf];
page.render();

// Mobile: focus lands on the editor copy in the section the user opened,
// and cancel returns focus to that row's action button.
openEditor('used-more');
const opened = { tag: FOCUSED?.className, section: FOCUSED?.closest('.memory-view')?.dataset.view };
fire(inSection('used-more').querySelector('.memory-edit'), 'keydown', { key: 'Escape', isComposing: false });
const cancelled = {
  isRowAction: FOCUSED?.classList.contains('row-action') ?? false,
  section: FOCUSED?.closest('.memory-view')?.dataset.view,
  memoryId: FOCUSED?.closest('.memory-card')?.dataset.memoryId,
};

// Desktop: the same flow through the single visible list.
media.matches = false;
fire(viewButtons.find(button => button.dataset.view === 'day'), 'click');
fire(media, 'change');
fire(page.el.list.querySelectorAll('.row-action')[0], 'click');
const desktop = { tag: FOCUSED?.className, inList: FOCUSED?.closest('#memory-list') != null };
fire(page.el.list.querySelector('.memory-edit'), 'keydown', { key: 'Escape', isComposing: false });
const desktopCancel = {
  isRowAction: FOCUSED?.classList.contains('row-action') ?? false,
  inList: FOCUSED?.closest('#memory-list') != null,
};
console.log(JSON.stringify({ opened, cancelled, desktop, desktopCancel }));
"""


def _run_scenario(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = "import fs from 'node:fs';\n" + _HARNESS + scenario
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=15,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def copies() -> dict:
    return _run_scenario(_SCENARIO_COPIES)


@pytest.fixture(scope="module")
def resize_error() -> dict:
    return _run_scenario(_SCENARIO_RESIZE_ERROR)


@pytest.fixture(scope="module")
def concurrency() -> dict:
    return _run_scenario(_SCENARIO_CONCURRENCY)


@pytest.fixture(scope="module")
def identity() -> dict:
    return _run_scenario(_SCENARIO_IDENTITY)


@pytest.fixture(scope="module")
def focus() -> dict:
    return _run_scenario(_SCENARIO_FOCUS)


def test_mobile_renders_four_editor_copies_and_disables_all(copies: dict) -> None:
    """One memory matches all four list views on mobile: every editor copy
    must answer to one mutation, not just the last one rendered."""
    assert copies["editors"] == 4
    assert copies["editingId"] == "m1"
    assert copies["controlCount"] == 24
    assert copies["pendingAllDisabled"] is True


def test_search_during_pending_renders_disabled_copies_with_draft(copies: dict) -> None:
    assert copies["afterSearchEditors"] == 4
    assert copies["afterSearchAllDisabled"] is True
    assert copies["draftAfterSearch"] == "Trang một"


def test_success_clears_draft_and_editors(copies: dict) -> None:
    assert copies["afterSuccessEditors"] == 0
    assert copies["editingCleared"] is True
    assert copies["postCount"] == 1


def test_resize_during_pending_rebuilds_editors_disabled(resize_error: dict) -> None:
    assert resize_error["desktop"] == {"editors": 1, "allDisabled": True}
    assert resize_error["mobile"] == {"editors": 4, "allDisabled": True}


def test_failure_preserves_draft_and_reenables_editor(resize_error: dict) -> None:
    assert resize_error["failed"]["draftKept"] is True
    assert resize_error["failed"]["topic"] == "Trang một"
    assert resize_error["failed"]["reEnabled"] is True
    assert resize_error["failed"]["pendingFalse"] is True
    assert resize_error["failed"]["status"] == "Không lưu được."


def test_no_duplicate_requests_from_save_reinforce_forget(concurrency: dict) -> None:
    assert concurrency["during"] == {"posts": 1}
    assert concurrency["guards"] == {"editingUntouched": True, "posts": 2}
    assert concurrency["totalPosts"] == 2


def test_start_and_cancel_refused_while_pending(concurrency: dict) -> None:
    assert concurrency["guards"]["editingUntouched"] is True


def test_success_clears_only_submitted_draft_identity(identity: dict) -> None:
    assert identity["newerKept"] is True
    assert identity["editors"] == 4
    assert identity["allEnabled"] is True


def test_focus_opens_visible_editor_and_cancel_restores_row_action(focus: dict) -> None:
    assert focus["opened"] == {"tag": "memory-edit-title", "section": "used-more"}
    assert focus["cancelled"] == {
        "isRowAction": True,
        "section": "used-more",
        "memoryId": "m1",
    }
    assert focus["desktop"] == {"tag": "memory-edit-title", "inList": True}
    assert focus["desktopCancel"] == {"isRowAction": True, "inList": True}
