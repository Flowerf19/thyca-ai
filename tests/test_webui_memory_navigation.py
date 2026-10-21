"""Exercise the shared memory-scope submenu with a small DOM harness."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAVIGATION = ROOT / "thyca" / "webui" / "shared" / "js" / "navigation.js"

_HARNESS = r"""
import fs from 'node:fs';
class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.className = '';
    this.hidden = false;
    this.open = false;
    this.id = '';
    this.text = '';
    this.href = '';
  }
  append(...nodes) { for (const node of nodes) { node.remove(); node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) { for (const child of [...this.children]) child.remove(); this.text = ''; this.append(...nodes); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(item => item !== this); this.parentNode = null; }
  set textContent(value) { this.replaceChildren(); this.text = String(value); }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) {
    this.html = value;
    this.replaceChildren();
    if (this.tagName === 'dialog') {
      const heading = new Element('div');
      const title = new Element('h2'); title.id = 'screen-navigation-title'; title.textContent = 'Mục lục';
      const close = new Element('button'); close.setAttribute('aria-label', 'Đóng mục lục');
      heading.append(title, close);
      const nav = new Element('nav');
      this.append(heading, nav);
    } else if (this.tagName === 'a' && value.includes('<span></span>')) {
      this.append(new Element('span'));
    }
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  querySelectorAll(selector) {
    const matches = node => selector.startsWith('.')
      ? node.className.split(/\s+/).includes(selector.slice(1))
      : selector.startsWith('#') ? node.id === selector.slice(1)
      : node.tagName === selector;
    const found = [];
    const walk = node => { for (const child of node.children) { if (matches(child)) found.push(child); walk(child); } };
    walk(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  click() {
    const event = { target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const fn of this.listeners.click ?? []) fn(event);
    return event;
  }
  showModal() { this.open = true; }
  close() { this.open = false; for (const fn of this.listeners.close ?? []) fn({ target: this }); }
  getBoundingClientRect() { return { left: 0, right: 100, top: 0, bottom: 100 }; }
  focus() { this.focused = true; }
  blur() { this.focused = false; }
}
const menuButton = new Element('button'); menuButton.className = 'menu-button';
const documentListeners = {};
const body = new Element('body');
const document = globalThis.document = {
  body,
  documentElement: { dataset: {} },
  querySelectorAll(selector) {
    if (selector === '.menu-button') return [menuButton];
    if (selector === '.thyca-search') return [];
    return body.querySelectorAll(selector);
  },
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; },
  createElement(tag) { return new Element(tag); },
  addEventListener(type, fn) { (documentListeners[type] ??= []).push(fn); },
  dispatchEvent(event) { for (const fn of documentListeners[event.type] ?? []) fn(event); return !event.defaultPrevented; },
};
globalThis.CustomEvent = class {
  constructor(type, options = {}) { this.type = type; this.detail = options.detail; this.cancelable = options.cancelable; this.defaultPrevented = false; }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
};
globalThis.localStorage = { getItem: () => null };
const compact = { matches: process.env.MODE !== 'desktop', listeners: {}, addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); } };
globalThis.matchMedia = () => compact;
globalThis.location = { pathname: process.env.MODE === 'outside' ? '/index.html' : '/memories.html', search: '' };
const calls = [];
globalThis.fetch = async (url) => {
  calls.push(url);
  return { ok: true, json: async () => ({ bots: [
    { id: 'coder', name: 'Coder' },
    { id: 'researcher', name: 'Researcher' },
  ] }) };
};
let pending = process.env.MODE === 'rejected';
const selections = [];
document.addEventListener('thyca:memory-scope', event => {
  selections.push({ botId: event.detail.botId, botName: event.detail.botName });
  event.detail.accepted = !pending;
});
new Function(fs.readFileSync(process.env.NAV_PATH, 'utf8'))();
const dialog = body.querySelector('dialog');
const nav = dialog.querySelector('nav');
const journal = nav.querySelectorAll('a').find(link => link.textContent === 'Nhật ký');
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
const result = await (async () => {
  if (process.env.MODE === 'desktop') {
    menuButton.click();
    const click = journal.click();
    return { defaultPrevented: click.defaultPrevented, href: journal.href, calls, submenuHidden: dialog.querySelector('.memory-scope-submenu').hidden };
  }
  menuButton.click();
  const callsBeforeExpand = [...calls];
  const expand = journal.click();
  await settle();
  const submenu = dialog.querySelector('.memory-scope-submenu');
  const choices = submenu.querySelectorAll('a');
  const bot = choices.find(link => link.textContent === 'Researcher');
  if (process.env.MODE === 'outside') {
    const click = bot.click();
    return { callsBeforeExpand, expandPrevented: expand.defaultPrevented, clickPrevented: click.defaultPrevented, href: bot.href, selections, calls, dialogOpen: dialog.open };
  }
  const click = bot.click();
  return {
    callsBeforeExpand,
    expandPrevented: expand.defaultPrevented,
    clickPrevented: click.defaultPrevented,
    href: bot.href,
    choices: choices.map(link => link.textContent),
    selections,
    calls,
    dialogOpen: dialog.open,
    submenuChoiceCountAfterClick: submenu.querySelectorAll('a').length,
  };
})();
console.log(JSON.stringify(result));
"""


def _run(mode: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, "--input-type=module", "-e", _HARNESS],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=15,
        env={**os.environ, "MODE": mode, "NAV_PATH": str(NAVIGATION)},
    )
    return json.loads(result.stdout)


def test_mobile_menu_lazy_loads_bot_scopes_and_closes_after_accepted_selection() -> None:
    state = _run("mobile")
    assert state["callsBeforeExpand"] == []
    assert state["expandPrevented"] is True
    assert state["clickPrevented"] is True
    assert state["href"] == "./memories.html?bot_id=researcher"
    assert state["choices"] == ["Tổng quan", "Coder", "Researcher"]
    assert state["selections"] == [{"botId": "researcher", "botName": "Researcher"}]
    assert state["calls"] == ["/api/bots"]
    assert state["dialogOpen"] is False


def test_mobile_scope_choice_stays_open_when_memory_page_rejects_change() -> None:
    state = _run("rejected")
    assert state["callsBeforeExpand"] == []
    assert state["selections"] == [{"botId": "researcher", "botName": "Researcher"}]
    assert state["dialogOpen"] is True
    assert state["submenuChoiceCountAfterClick"] == 3


def test_scope_links_navigate_normally_off_the_memories_page() -> None:
    state = _run("outside")
    assert state["callsBeforeExpand"] == []
    assert state["expandPrevented"] is True
    assert state["clickPrevented"] is False
    assert state["href"] == "./memories.html?bot_id=researcher"
    assert state["selections"] == []
    assert state["calls"] == ["/api/bots"]


def test_desktop_journal_link_stays_a_normal_route_without_roster_fetch() -> None:
    state = _run("desktop")
    assert state == {
        "defaultPrevented": False,
        "href": "./memories.html",
        "calls": [],
        "submenuHidden": True,
    }
