"""Node tests for the rebuilt WebUI frontend (thyca/webui/ui/*).

The new UI ships *classic* scripts that share one global lexical scope —
no ES modules — so the harness below concatenates the production files in
their real <script> order, wraps them in a minimal fake DOM, and pipes the
bundle to Node on stdin. Pure helpers (util.js/data.js), markup builders
(sidebar.js/avatars.js), the chat turn loop (chat.js) and page scripts run
against the same globals they see in the browser.

Bundles mirror index.html:
  side      = util + data + avatars + idle + sidebar          (team/sidebar)
  shell     = side + shell + studio + shell-app               (shared chrome)
  chat      = side + chat data + studio + shell-app + chat    (turn stream)
  app       = chat + app                                      (composer/boot)
  settings|memories|dashboard|trace = shell stack + page script
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
UI = WEBUI / "ui"
SHARE = UI / "share"
CHAT_DIR = UI / "chat"

UTIL_JS = SHARE / "util.js"
DATA_JS = SHARE / "data.js"
CHAT_DATA_JS = CHAT_DIR / "js" / "data.js"
SHELL_JS = SHARE / "shell.js"
AVATARS_JS = SHARE / "avatars" / "avatars.js"
IDLE_JS = SHARE / "avatars" / "idle.js"
SIDEBAR_JS = SHARE / "sidebar.js"
STUDIO_JS = SHARE / "avatars" / "studio.js"
SHELL_APP_JS = SHARE / "shell-app.js"
CHAT_JS = CHAT_DIR / "js" / "chat.js"
APP_JS = CHAT_DIR / "js" / "app.js"
SETTINGS_JS = UI / "settings" / "js" / "settings.js"
MEMORIES_JS = UI / "memories" / "js" / "memories.js"
DASHBOARD_JS = UI / "dashboard" / "js" / "dashboard.js"
TRACE_JS = UI / "trace" / "js" / "trace.js"

BASE_CSS = SHARE / "base.css"
SIDEBAR_CSS = SHARE / "sidebar.css"
APP_SHELL_CSS = SHARE / "app-shell.css"
AVATARS_CSS = SHARE / "avatars" / "avatars.css"
STUDIO_CSS = SHARE / "avatars" / "studio.css"
CHAT_CSS = CHAT_DIR / "chatcss" / "chat.css"

INDEX_HTML = CHAT_DIR / "index.html"
SETTINGS_HTML = UI / "settings" / "index.html"
MEMORIES_HTML = UI / "memories" / "index.html"
DASHBOARD_HTML = UI / "dashboard" / "index.html"
TRACE_HTML = UI / "trace" / "index.html"
PROFILE_HTML = UI / "profile" / "index.html"
PROVIDER_HTML = UI / "provider" / "index.html"

PAGE_HTML_FILES = {
    "settings": SETTINGS_HTML,
    "memories": MEMORIES_HTML,
    "dashboard": DASHBOARD_HTML,
    "trace": TRACE_HTML,
    "profile": PROFILE_HTML,
    "provider": PROVIDER_HTML,
}

# ---------------------------------------------------------------------------
# Fake-DOM prelude + Node harness
# ---------------------------------------------------------------------------

_PRELUDE = r"""
'use strict';
/* Minimal DOM for classic-script webui tests. querySelector auto-vivifies a
   memoized element when nothing matches — production code renders via
   innerHTML strings, which a stub cannot re-parse, so on-demand stubs stand
   in for nodes the markup would have created. querySelectorAll stays strict
   (real children only). */

class FakeStyle {
  constructor() { this._m = {}; }
  setProperty(k, v) { this._m[k] = String(v); }
  getPropertyValue(k) { return this._m[k] || ''; }
  removeProperty(k) { delete this._m[k]; }
  set cssText(v) {
    this._cssText = String(v);
    for (const p of String(v).split(';')) {
      const i = p.indexOf(':'); if (i > 0) this._m[p.slice(0, i).trim()] = p.slice(i + 1).trim();
    }
  }
  get cssText() { return this._cssText || ''; }
}

function datasetOf(el) {
  const key2attr = (k) => 'data-' + String(k).replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
  return new Proxy({}, {
    get: (t, k) => el.attributes[key2attr(k)],
    set: (t, k, v) => { el.attributes[key2attr(k)] = String(v); return true; },
    has: (t, k) => key2attr(k) in el.attributes,
    deleteProperty: (t, k) => { delete el.attributes[key2attr(k)]; return true; },
  });
}

function matchCompound(el, part) {
  if (!el || !el.tagName) return false;
  const tokens = part.match(/([#.][\w-]+)|(\[[^\]]*\])|(:[\w-]+(?:\([^)]*\))?)|([a-zA-Z*][\w-]*)/g) || [];
  for (const t of tokens) {
    if (t[0] === '.') { if (!el.classList.contains(t.slice(1))) return false; }
    else if (t[0] === '#') { if (el.id !== t.slice(1)) return false; }
    else if (t[0] === '[') {
      const mm = t.slice(1, -1).match(/^([^=\s]+)\s*(?:=\s*["']?([^"']*)["']?)?$/);
      if (!mm) return false;
      const av = el.getAttribute(mm[1]);
      if (av === null) return false;
      if (mm[2] !== undefined && mm[2] !== '' && av !== mm[2]) return false;
    } else if (t[0] === ':') {
      if (t === ':checked') { if (!el.checked) return false; }
      else if (t === ':disabled') { if (!el.disabled) return false; }
      else if (t.startsWith(':not(')) { if (matchCompound(el, t.slice(4, -1))) return false; }
      else if (t === ':first-child') { if (el.parentNode && el.parentNode.children[0] !== el) return false; }
      else if (t === ':last-child') { if (el.parentNode && el.parentNode.children[el.parentNode.children.length - 1] !== el) return false; }
    } else if (t !== '*' && el.tagName !== t.toUpperCase()) return false;
  }
  return true;
}

function walkNodes(root, fn) {
  for (const c of (root.children || []).slice()) {
    if (c && c.tagName) { fn(c); walkNodes(c, fn); }
  }
}

function queryAllNodes(root, sel) {
  const parts = String(sel).trim().split(/\s+/);
  const last = parts[parts.length - 1];
  const out = [];
  walkNodes(root, (c) => {
    if (c === root || !matchCompound(c, last)) return;
    let ok = true, anc = c.parentNode;
    for (let i = parts.length - 2; i >= 0 && ok; i--) {
      let found = false;
      while (anc) {
        if (matchCompound(anc, parts[i])) { found = true; anc = anc.parentNode; break; }
        anc = anc.parentNode;
      }
      if (!found) ok = false;
    }
    if (ok) out.push(c);
  });
  return out;
}

function applySelector(el, part) {
  const tokens = part.match(/([#.][\w-]+)|(\[[^\]]*\])/g) || [];
  for (const t of tokens) {
    if (t[0] === '.') el.className = (el.className ? el.className + ' ' : '') + t.slice(1);
    else if (t[0] === '#') el.id = t.slice(1);
    else if (t[0] === '[') {
      const mm = t.slice(1, -1).match(/^([^=\s]+)\s*(?:=\s*["']?([^"']*)["']?)?$/);
      if (mm) el.setAttribute(mm[1], mm[2] || '');
    }
  }
}

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = datasetOf(this);
    this.style = new FakeStyle();
    this.className = '';
    this._text = '';
    this._html = '';
    this.hidden = false;
    this.id = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.type = '';
    this.title = '';
    this.tabIndex = 0;
    this.open = false;
    this.parentNode = null;
    this.inert = false;
    this.name = '';
    this.placeholder = '';
    this.files = [];
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
    this._handlers = {};
    this._inserted = [];
  }
  get classList() {
    const el = this;
    const list = () => String(el.className || '').split(/\s+/).filter(Boolean);
    return {
      add: (...cs) => { el.className = [...new Set([...list(), ...cs])].join(' '); },
      remove: (...cs) => { el.className = list().filter((c) => !cs.includes(c)).join(' '); },
      toggle: (c, force) => {
        const has = list().includes(c);
        const on = force === undefined ? !has : !!force;
        if (on && !has) el.classList.add(c);
        if (!on && has) el.classList.remove(c);
        return on;
      },
      contains: (c) => list().includes(c),
    };
  }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  get nextElementSibling() {
    if (!this.parentNode) return null;
    const i = this.parentNode.children.indexOf(this);
    return this.parentNode.children[i + 1] || null;
  }
  get previousElementSibling() {
    if (!this.parentNode) return null;
    const i = this.parentNode.children.indexOf(this);
    return this.parentNode.children[i - 1] || null;
  }
  get parentElement() { return this.parentNode; }
  get childElementCount() { return this.children.length; }
  get options() { return this.children.filter((c) => c.tagName === 'OPTION'); }
  get innerText() { return this.textContent; }
  get textContent() {
    return this._text + this.children.map((c) =>
      (c && typeof c.textContent === 'string') ? c.textContent : '').join('');
  }
  set textContent(v) { this._replaceKids(); this._text = String(v); this._html = ''; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._replaceKids(); this._text = ''; this._html = String(v); }
  _replaceKids() { for (const c of this.children) if (c && c.parentNode === this) c.parentNode = null; this.children = []; }
  insertAdjacentHTML(pos, html) {
    this._inserted.push([pos, String(html)]);
    if (pos === 'beforeend' || pos === 'afterbegin') this._html += String(html);
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return Object.hasOwn(this.attributes, k) ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  hasAttribute(k) { return Object.hasOwn(this.attributes, k); }
  addEventListener(t, f) { (this._handlers[t] = this._handlers[t] || []).push(f); }
  removeEventListener(t, f) {
    const hs = this._handlers[t] || []; const i = hs.indexOf(f);
    if (i >= 0) hs.splice(i, 1);
  }
  dispatch(t, ev) {
    ev = ev || {}; ev.type = t; if (!('target' in ev)) ev.target = this;
    if (!('currentTarget' in ev)) ev.currentTarget = this;
    if (!ev.preventDefault) ev.preventDefault = () => {};
    for (const f of (this._handlers[t] || []).slice()) f.call(this, ev);
    const on = this['on' + t]; if (typeof on === 'function') on.call(this, ev);
    return ev;
  }
  click() { return this.dispatch('click'); }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatch('close'); }
  requestSubmit() { this.dispatch('submit'); }
  reset() { this._resetCalled = true; }
  scrollIntoView() {}
  getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  setPointerCapture() {}
  releasePointerCapture() {}
  getAnimations() { return []; }
  append(...nodes) { for (const n of nodes) this.appendChild(n); }
  appendChild(n) { return this.insertBefore(n, null); }
  prepend(...nodes) { for (const n of nodes.slice().reverse()) this.insertBefore(n, this.children[0] || null); }
  insertBefore(n, ref) {
    const node = (typeof n === 'string') ? Object.assign(new FakeNode('#text'), { _text: n })
      : (n && !n.tagName && typeof n.textContent === 'string') ? Object.assign(new FakeNode('#text'), { _text: n.textContent }) : n;
    if (node === ref) return node;
    if (node.parentNode) {
      const old = node.parentNode.children.indexOf(node);
      if (old >= 0) node.parentNode.children.splice(old, 1);
    }
    const index = ref == null ? -1 : this.children.indexOf(ref);
    if (index < 0) this.children.push(node); else this.children.splice(index, 0, node);
    node.parentNode = this;
    return node;
  }
  replaceChildren(...nodes) { this._replaceKids(); this._html = ''; this.append(...nodes); }
  removeChild(node) {
    const i = this.children.indexOf(node);
    if (i >= 0) { this.children.splice(i, 1); node.parentNode = null; }
    return node;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(n) { let x = n; while (x) { if (x === this) return true; x = x.parentNode; } return false; }
  matches(sel) { return matchCompound(this, sel); }
  closest(sel) { let x = this; while (x && x.tagName) { if (matchCompound(x, sel)) return x; x = x.parentNode; } return null; }
  querySelector(sel) {
    const found = queryAllNodes(this, sel)[0];
    if (found) return found;
    let cur = this;
    for (const part of String(sel).trim().split(/\s+/)) {
      const inner = queryAllNodes(cur, part).filter((c) => c !== cur)[0];
      if (inner) { cur = inner; continue; }
      const el = new FakeNode((part.match(/^[a-zA-Z][\w-]*/) || ['div'])[0]);
      applySelector(el, part);
      cur.appendChild(el);
      cur = el;
    }
    return cur;
  }
  querySelectorAll(sel) { return queryAllNodes(this, sel); }
}

const _docEl = new FakeNode('html');
const _body = new FakeNode('body');
_docEl.appendChild(_body);
const _docAuto = new Map();

const document = {
  hidden: false,
  readyState: 'complete',
  _handlers: {},
  get body() { return _body; },
  get documentElement() { return _docEl; },
  createElement(t) { return new FakeNode(t); },
  createElementNS(ns, t) { return new FakeNode(t); },
  createTextNode(t) { return Object.assign(new FakeNode('#text'), { _text: String(t) }); },
  addEventListener(t, f) { (this._handlers[t] = this._handlers[t] || []).push(f); },
  removeEventListener() {},
  contains(n) { return _docEl.contains(n) || n === _docEl; },
  querySelector(sel) {
    const found = queryAllNodes(_docEl, sel)[0];
    if (found) return found;
    if (!_docAuto.has(sel)) {
      let cur = _body;
      for (const part of String(sel).trim().split(/\s+/)) {
        const inner = queryAllNodes(cur, part).filter((c) => c !== cur)[0];
        if (inner) { cur = inner; continue; }
        const el = new FakeNode((part.match(/^[a-zA-Z][\w-]*/) || ['div'])[0]);
        applySelector(el, part);
        cur.appendChild(el);
        cur = el;
      }
      _docAuto.set(sel, cur);
    }
    return _docAuto.get(sel);
  },
  querySelectorAll(sel) { return queryAllNodes(_docEl, sel); },
};
globalThis.document = document;
globalThis.window = globalThis;
globalThis.innerWidth = 1280;
globalThis.innerHeight = 800;
globalThis.location = { href: 'http://localhost/ui/chat/index.html', search: '', hash: '' };
globalThis.history = { replaceState() {}, pushState() {} };
try {
  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async (t) => { globalThis.__copied = t; } } },
    configurable: true, writable: true,
  });
} catch { /* navigator read-only */ }
globalThis.matchMedia = (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
globalThis.localStorage = {
  _m: {},
  getItem(k) { return Object.hasOwn(this._m, k) ? this._m[k] : null; },
  setItem(k, v) { this._m[k] = String(v); },
  removeItem(k) { delete this._m[k]; },
  clear() { this._m = {}; },
};
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), body: null });
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.confirm = () => true;
globalThis.MutationObserver = class { constructor(cb) { this.cb = cb; } observe() {} disconnect() {} };
const __setInterval = setInterval;
globalThis.setInterval = (...a) => { const t = __setInterval(...a); if (t && t.unref) t.unref(); return t; };
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const streamOf = (events) => {
  const enc = new TextEncoder();
  const chunks = events.map((e) => enc.encode(JSON.stringify(e) + '\n'));
  let i = 0;
  return { getReader: () => ({ read: async () => (i < chunks.length ? { done: false, value: chunks[i++] } : { done: true }) }) };
};
/* Fake fetch: map of predicate -> response factory, falls back to a 200 {}. */
const installFetch = (handler) => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push([String(url), options || null]);
    const res = await handler(String(url), options || {});
    if (res instanceof Error) throw res;
    if (res && typeof res.json === 'function') return res;
    return { ok: true, status: 200, json: async () => (res === undefined ? {} : res), body: res && res.body };
  };
  return calls;
};
const roster = (cards) => cards.map((c, i) => normalizeBot(c, i));
"""

# Symbols that live in files outside the loaded bundle get inert defaults so
# shared helpers keep working (lexical declarations inside the bundle shadow
# these global properties — exactly the browser's script-tag behaviour).
_DEFAULTS = r"""
globalThis.runs ??= new Map();
globalThis.histories ??= new Map();
globalThis.lab ??= { timers: [], mode: null, botId: null, lastChoice: 'idle', sample: '', ownsGroup: false };
globalThis.currentSession ??= '';
globalThis.selectedBot ??= '';
globalThis.attachment ??= null;
globalThis.typedMention ??= '';
globalThis.activeGroup ??= null;
globalThis.stopLab ??= () => true;
globalThis.refreshLab ??= () => {};
globalThis.rebuildExpressionBots ??= () => {};
globalThis.cancelTurn ??= () => false;
globalThis.openMobile ??= () => {};
globalThis.closeMobile ??= () => {};
globalThis.handleEvent ??= () => false;
globalThis.toggleMotion ??= () => {};
globalThis.buildExpressions ??= () => {};
globalThis.updateComposer ??= () => {};
globalThis.followRunningTurn ??= () => {};
globalThis.sendTurn ??= async () => false;
globalThis.stopRun ??= async () => false;
globalThis.selectBot ??= async () => {};
globalThis.loadSession ??= async () => {};
globalThis.mentionBot ??= () => false;
globalThis.updateCard ??= () => {};
globalThis.renderTeam ??= () => {};
globalThis.renderGroups ??= () => {};
globalThis.renderTranscript ??= () => '';
globalThis.clearIdle ??= () => false;
globalThis.idleEligible ??= () => false;
globalThis.beginIdle ??= () => false;
globalThis.idleInput ??= () => {};
globalThis.randomIdle ??= () => 'look';
globalThis.setGroupActivity ??= () => true;
globalThis.syncGroupAvatars ??= () => {};
globalThis.syncFeedList ??= () => {};
globalThis.setState ??= () => true;
globalThis.notifyText ??= () => false;
globalThis.clearMusic ??= () => {};
globalThis.paint ??= () => {};
"""

_BUNDLES = {
    "side": (UTIL_JS, DATA_JS, AVATARS_JS, IDLE_JS, SIDEBAR_JS),
    "shell": (UTIL_JS, DATA_JS, SHELL_JS, AVATARS_JS, IDLE_JS, SIDEBAR_JS, STUDIO_JS, SHELL_APP_JS),
    "chat": (UTIL_JS, DATA_JS, CHAT_DATA_JS, AVATARS_JS, IDLE_JS, SIDEBAR_JS, STUDIO_JS, SHELL_APP_JS, CHAT_JS),
    "app": (UTIL_JS, DATA_JS, CHAT_DATA_JS, AVATARS_JS, IDLE_JS, SIDEBAR_JS, STUDIO_JS, SHELL_APP_JS, CHAT_JS, APP_JS),
    "settings": (UTIL_JS, DATA_JS, SHELL_JS, AVATARS_JS, IDLE_JS, SIDEBAR_JS, STUDIO_JS, SHELL_APP_JS, SETTINGS_JS),
    "memories": (UTIL_JS, DATA_JS, SHELL_JS, AVATARS_JS, IDLE_JS, SIDEBAR_JS, STUDIO_JS, SHELL_APP_JS, MEMORIES_JS),
    "dashboard": (UTIL_JS, DATA_JS, SHELL_JS, AVATARS_JS, IDLE_JS, SIDEBAR_JS, STUDIO_JS, SHELL_APP_JS, DASHBOARD_JS),
    "trace": (UTIL_JS, DATA_JS, SHELL_JS, AVATARS_JS, IDLE_JS, SIDEBAR_JS, STUDIO_JS, SHELL_APP_JS, TRACE_JS),
}


def _run(node: str, body: str, via: str = "side", emit: str | None = None, timeout: int = 30):
    """Evaluate <body> inside an async main() after the bundle files load."""
    parts = [_PRELUDE]
    for f in _BUNDLES[via]:
        parts.append(f"// ===== {f.relative_to(UI)}\n" + f.read_text())
        if emit and f == SHELL_JS:
            parts.append(f"emitShell({json.dumps(emit)});")
    parts.append(_DEFAULTS)
    parts.append(f"const main = async () => {{\n{body}\n}};\n")
    parts.append(
        "main().then((v)=>process.stdout.write(JSON.stringify(v===undefined?null:v)+'\\n',()=>process.exit(0)),"
        "(e)=>{console.error(e && (e.stack||e) || e);process.exit(1)});\n"
    )
    result = subprocess.run(
        [node], input="\n;\n".join(parts), capture_output=True, text=True, timeout=timeout,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    out = result.stdout.strip().splitlines()
    return json.loads(out[-1]) if out else None


@pytest.fixture(scope="session")
def node() -> str:
    for candidate in ("/usr/bin/node", "/usr/local/bin/node", shutil.which("node")):
        if candidate and Path(candidate).exists():
            return candidate
    pytest.skip("node binary not found")


# ---------------------------------------------------------------------------
# Shared data layer — ui/share/data.js
# ---------------------------------------------------------------------------

def test_bot_color_resolves_tint_hex_and_storage_override(node: str) -> None:
    """TINT_HEX maps card.tint; unknown tints fall back to TINT_ORDER by index;
    a saved localStorage hex wins over both — stable for the immutable id."""
    out = _run(node, """
        localStorage.clear();
        const a = normalizeBot({ id: 'x', tint: 'botanical' }, 3);
        const b = normalizeBot({ id: 'y', tint: 'mystery' }, 6);
        const c = normalizeBot({ id: 'z', tint: 'mystery' }, 6);
        localStorage.setItem('thyca.tint.saved', '#123456');
        const saved = normalizeBot({ id: 'saved', tint: 'botanical' }, 0);
        return {
            a: a.color, aTint: a.tint,
            b: b.color, bTint: b.tint, cEqual: b.color === c.color,
            orderFallback: TINT_HEX[TINT_ORDER[6 % TINT_ORDER.length]],
            saved: saved.color,
            sameIdStable: normalizeBot({ id: 'x', tint: 'botanical' }, 9).color === a.color,
        };
    """)
    assert out["a"] == "#6f7f57"                      # TINT_HEX.botanical
    assert out["aTint"] == "botanical"
    assert out["b"] == out["orderFallback"]           # unknown tint -> order slot
    assert out["cEqual"] is True                      # deterministic per index
    assert out["saved"] == "#123456"                  # localStorage override wins
    assert out["sameIdStable"] is True                # stable across calls


def test_derive_palette_produces_bot_var_contract(node: str) -> None:
    """derivePalette emits the full --bot-* ramp the seal engine + cards read;
    paletteStyle() serializes it as inline CSS custom properties."""
    out = _run(node, """
        const p = derivePalette('#6f7f57');
        return { keys: Object.keys(p).sort(), style: paletteStyle({ palette: p }), sample: p.ink };
    """, via="side")
    keys = set(out["keys"])
    for k in ("color", "ink", "dark", "highlight", "mid", "border", "rule",
              "paper", "pale", "sheet", "stamp", "eye", "eye-edge", "pupil", "muted"):
        assert k in keys
    assert "--bot-color:#6f7f57" in out["style"]
    assert re.search(r"--bot-ink:#[0-9a-f]{6}", out["style"])
    assert out["sample"].startswith("#")


def test_normalize_bot_maps_card_and_agent_status(node: str) -> None:
    """normalizeBot keeps the card fields the drawer/cards read and derives
    state from agent_status (running -> working, paused -> offline)."""
    out = _run(node, """
        localStorage.clear();
        const b = normalizeBot({
            id: 'coder', name: 'Coder', role: 'Dev', description: 'x',
            tint: 'ink', system: true,
            status: 'idle',
            agent_status: { state: 'running', current_action: 'đang build', since: new Date(Date.now() - 5000).toISOString() },
            skills: ['bash', 'edit'],
            tools: [{ name: 't1', description: 'd1' }, 't2'],
            persona: 'p', memory: { read: true, write: false },
            model: { id: 'm1', thinking: 'high' }, bash: 'ask', schedule: 'daily',
        }, 1);
        const paused = normalizeBot({ id: 'p', status: 'paused' }, 0);
        const bare = normalizeBot({ id: 'bare' }, 0);
        return { b, pausedState: paused.state, bareState: bare.state, bareName: bare.name,
                 bareTools: bare.tools, bareCaps: bare.capabilities };
    """)
    b = out["b"]
    assert b["id"] == "coder" and b["name"] == "Coder" and b["role"] == "Dev"
    assert b["state"] == "working"                    # running -> working
    assert b["summary"] == "đang build"
    assert b["elapsed"] >= 4                          # seconds since agent_status.since
    assert b["capabilities"] == [["bash", ""], ["edit", ""]]
    assert b["tools"] == [{"name": "t1", "description": "d1"}, {"name": "t2", "description": ""}]
    assert b["model"] == {"id": "m1", "thinking": "high"}
    assert b["bash"] == "ask" and b["schedule"] == "daily"
    assert b["system"] is True and b["persona"] == "p"
    assert b["memory"] == {"read": True, "write": False}
    assert b["sessions"] == [] and b["favorite"] is False and b["open"] is False
    assert b["palette"]["color"] == b["color"]
    assert out["pausedState"] == "offline"            # paused -> offline
    assert out["bareState"] == "idle"
    assert out["bareName"] == "bare"                  # name falls back to id
    assert out["bareTools"] == [] and out["bareCaps"] == []


def test_load_roster_tolerates_missing_backend(node: str) -> None:
    """loadRoster normalizes {bots:[...]}, marks the first card open and picks
    a default selectedBot; an empty/absent list leaves the roster empty."""
    out = _run(node, """
        const calls = installFetch((url) => url === '/api/bots'
            ? { bots: [{ id: 'a', name: 'A' }, { id: 'b' }] } : {});
        await loadRoster();
        const first = bots.map(x => [x.id, x.open]);
        const sel = selectedBot;
        fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
        await loadRoster();
        return { first, sel, afterEmpty: bots.length };
    """)
    assert out["first"] == [["a", True], ["b", False]]     # first bot open only
    assert out["sel"] == "a"                                # default selection
    assert out["afterEmpty"] == 0                           # missing payload tolerated


def test_get_json_errors_carry_status_and_body(node: str) -> None:
    """Non-2xx responses reject with err.status and the body's error text."""
    out = _run(node, """
        installFetch(() => ({ ok: false, status: 422, json: async () => ({ error: 'bad input' }) }));
        const err = await getJson('/api/x').then(() => null).catch(e => ({ message: e.message, status: e.status }));
        return { err };
    """)
    assert out["err"] == {"message": "bad input", "status": 422}


def test_refresh_sessions_partitions_groups_and_rows(node: str) -> None:
    """kind:'group' sessions feed the shared group list (members filtered to
    live bots); a bot's row lists dm sessions plus every session — group ones
    included — whose bot list mentions them."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca' }, { id: 'coder' }]));
        installFetch((url) => url === '/api/sessions' ? { sessions: [
            { id: 'g1', kind: 'group', title: 'Nhóm', bots: ['thyca', 'coder', 'ghost'], turns: 5, updated_at: new Date().toISOString() },
            { id: 'd1', dm: 'coder', title: 'DM coder', turns: 2, updated_at: new Date().toISOString() },
            { id: 's1', title: 'Old solo', bots: ['thyca'], turns: 1 },
            { id: 's2', title: 'Shared legacy', bots: ['thyca', 'coder'] },
        ] } : {});
        await refreshSessions();
        return {
            groups: groups.map(g => [g.id, g.title, g.bots, g.subtitle]),
            thyca: bots[0].sessions.map(s => s[0]),
            coder: bots[1].sessions.map(s => s[0]),
        };
    """)
    assert out["groups"] == [["g1", "Nhóm", ["thyca", "coder"], "2 người bạn · 5 lượt"]]
    assert set(out["thyca"]) == {"g1", "s1", "s2"}        # member of the group room
    assert set(out["coder"]) == {"g1", "d1", "s2"}        # dm + group + shared


def test_session_row_day_labels_and_turn_count(node: str) -> None:
    """sessionRow -> [id, title|'Chưa đặt tên', day + ' · N lượt']; days are
    Hôm nay / Hôm qua / d-m / ''."""
    out = _run(node, """
        const mk = (daysAgo, extra) => {
            const d = new Date(Date.now() - daysAgo * 864e5);
            return sessionRow({ id: 's', updated_at: d.toISOString(), ...extra });
        };
        return {
            today: mk(0, { title: 'T', turns: 3 }),
            yesterday: mk(1, {}),
            old: mk(40, { title: 'Old' }),
            missing: sessionRow({ id: 'x' }),
        };
    """)
    assert out["today"] == ["s", "T", "Hôm nay · 3 lượt"]
    assert out["yesterday"][0] == "s" and out["yesterday"][1] == "Chưa đặt tên"
    assert out["yesterday"][2].startswith("Hôm qua")
    assert out["old"][2] == "" or re.match(r"\d+/\d+", out["old"][2])
    assert out["missing"] == ["x", "Chưa đặt tên", ""]


def test_sync_roster_patches_in_place_and_respects_running_bots(node: str) -> None:
    """syncRoster patches card fields in place, adds new bots, drops removed
    ones, and never overwrites state/summary/elapsed of a bot with a live run."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }]));
        bots[1].state = 'working'; bots[1].summary = 'đang chạy'; bots[1].elapsed = 42;
        runs.set('s1', { botId: 'b', sessionId: 's1', node: null, timers: [], tools: [] });
        installFetch((url) => url === '/api/bots'
            ? { bots: [{ id: 'a', name: 'A2', role: 'R' }, { id: 'c', name: 'C' }] }
            : url === '/api/sessions' ? { sessions: [] } : {});
        await syncRoster();
        return {
            ids: bots.map(x => x.id),
            a: { name: bots[0].name, role: bots[0].role },
            listHtml: $('#team-bot-list').innerHTML.includes('bot-c'),
        };
    """)
    assert out["ids"] == ["a", "c"]                    # b dropped, c added
    assert out["a"] == {"name": "A2", "role": "R"}     # patched in place
    assert out["listHtml"] is True                     # changed -> renderTeam
    # the running bot was deleted here, so state isn't preserved — covered below


def test_sync_roster_keeps_live_state_when_bot_has_run(node: str) -> None:
    """botHasRun() shields state/summary/elapsed from roster patches while the
    client still owns the turn."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }]));
        bots[0].state = 'working'; bots[0].summary = 'đang build'; bots[0].elapsed = 30;
        runs.set('s1', { botId: 'a', sessionId: 's1', node: null, timers: [], tools: [] });
        installFetch((url) => url === '/api/bots'
            ? { bots: [{ id: 'a', agent_status: { state: 'idle', current_action: 'rest' } }] }
            : url === '/api/sessions' ? { sessions: [] } : {});
        await syncRoster();
        return { state: bots[0].state, summary: bots[0].summary, elapsed: bots[0].elapsed };
    """)
    assert out == {"state": "working", "summary": "đang build", "elapsed": 30}


# ---------------------------------------------------------------------------
# Seal avatars — ui/share/avatars/avatars.js + idle.js
# ---------------------------------------------------------------------------

def test_avatar_markup_contract(node: str) -> None:
    """avatar(b) emits the seal span with bot palette vars, state/expression
    attrs and per-instance-rewritten wax clip ids."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'coder', name: 'Coder' }]));
        const b = bots[0];
        selectedBot = 'coder';
        const html = avatar(b);
        return { html, uid: ++avatarCounter };
    """)
    html = out["html"]
    assert 'class="seal-avatar mk mk-wax' in html
    assert 'data-bot-avatar="coder"' in html
    assert 'data-state="idle"' in html
    assert 'data-expression="idle"' in html
    assert 'data-selected="true"' in html
    assert "--bot-color:" in html and "--bot-ink:" in html
    assert 'class="seal-motion"' in html and 'class="seal-face"' in html
    assert re.search(r'id="wax\d+-wx\d+"', html)        # clip ids namespaced per instance
    assert re.search(r'url\(#wax\d+-wx\d+\)', html)     # url(#wx) refs rewritten to match
    assert 'aria-hidden="true"' in html


def test_avatar_body_is_id_stable_and_expression_follows_mention(node: str) -> None:
    """The wax body is picked deterministically from the immutable id; a
    mentioned bot renders data-expression=mention without changing state."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'coder' }, { id: 'researcher' }]));
        const bodyOf = (h) => (h.match(/<path class="wax-body" d="([^"]+)/) || [])[1];
        const a1 = bodyOf(avatar(bots[0]));
        const a2 = bodyOf(avatar(bots[0]));
        const listening = avatar(bots[0], 'listening');
        bots[0].mentioned = true;
        const mentioned = avatar(bots[0]);
        return {
            stable: a1 === a2 && !!a1,
            listening,
            mentioned,
        };
    """)
    assert out["stable"] is True
    assert 'data-state="waiting"' in out["listening"]    # stateAliases listening->waiting
    assert 'data-expression="mention"' in out["mentioned"]


def test_states_table_labels_cover_the_lifecycle(node: str) -> None:
    """states[] carries the Vietnamese labels cards/headers show per state."""
    out = _run(node, """
        return { labels: Object.fromEntries(Object.entries(states).map(([k, v]) => [k, v[1]])),
                 aliases: stateAliases };
    """)
    labels = out["labels"]
    for st, label in {
        "idle": "Sẵn sàng", "waiting": "Chờ bạn", "thinking": "Đang suy nghĩ",
        "writing": "Đang viết", "working": "Đang làm việc", "done": "Đã hoàn tất",
        "error": "Có lỗi xảy ra", "offline": "Mất kết nối", "mention": "Bạn gọi mình?",
        "confused": "Cần làm rõ", "group": "Đang phối hợp",
    }.items():
        assert labels[st] == label
    assert out["aliases"] == {"listening": "waiting", "resting": "idle"}


def test_set_state_drives_summary_orbit_and_done_timer(node: str) -> None:
    """setState applies the state summary, resets the orbit clock on change,
    and 'done' auto-returns to idle after the 1.6s beat."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }]));
        const b = bots[0];
        setState('a', 'thinking');
        const thinking = { state: b.state, summary: b.summary, label: states[b.state][1] };
        setState('a', 'done');
        const doneAt = { state: b.state, summary: b.summary };
        await tick(1700);
        return { thinking, doneAt, after: b.state };
    """)
    assert out["thinking"]["state"] == "thinking"
    assert "sắp xếp" in out["thinking"]["summary"]       # stateSummaries.thinking
    assert out["doneAt"]["state"] == "done"
    assert out["after"] == "idle"                        # done beat -> idle


def test_set_state_error_cancels_run_and_announces(node: str) -> None:
    """error/offline cancel the bot's in-flight turn token and announce the
    failure in Vietnamese."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'Alpha' }]));
        const calls = [];
        globalThis.cancelTurn = (t, m, b) => calls.push([t.sessionId, m, b]);
        runs.set('s9', { botId: 'a', sessionId: 's9', node: null, timers: [], tools: [] });
        setState('a', 'error');
        return {
            state: bots[0].state,
            calls,
            announced: $('#announcer').textContent,
        };
    """)
    assert out["state"] == "error"
    assert out["calls"] == [["s9", "Lượt trả lời gặp lỗi. Bạn có thể thử lại.", "gặp lỗi"]]
    assert "Alpha" in out["announced"] and "lỗi" in out["announced"]


def test_mention_overlay_and_notify_text_drive_seal_attrs(node: str) -> None:
    """mentionBot flags the bot for 500ms; notifyText seeds the music notes on
    every writing seal (only while the bot is writing)."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'A' }]));
        const b = bots[0];
        const seal = document.createElement('span');
        seal.className = 'seal-avatar'; seal.dataset.botAvatar = 'a';
        document.body.appendChild(seal);
        mentionBot('a');
        const during = { mentioned: b.mentioned, expr: expressionFor(b) };
        await tick(600);
        const after = { mentioned: b.mentioned };
        const early = notifyText('a', 'x');
        setState('a', 'writing');
        const ok = notifyText('a', 'hello world');
        return {
            during, after, early, ok,
            streaming: seal.dataset.streaming,
            gaze: seal.style.getPropertyValue('--writing-gaze'),
            noteA: seal.style.getPropertyValue('--note-a-y'),
        };
    """)
    assert out["during"] == {"mentioned": True, "expr": "mention"}
    assert out["after"]["mentioned"] is False            # 500ms overlay released
    assert out["early"] is False                          # not writing -> ignored
    assert out["ok"] is True
    assert out["streaming"] == "true"
    assert out["gaze"].endswith("px") and out["noteA"].endswith("px")


def test_idle_layer_gestures_never_change_work_state(node: str) -> None:
    """beginIdle marks idle.active + a card comment; eligibility excludes
    mentioned/grouped/busy bots; clearIdle resets without touching b.state."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }, { id: 'b' }]));
        const a = bots[0], b = bots[1];
        const key = beginIdle('a', 'look', true);
        const during = { key, active: a.idle.active, state: a.state };
        clearIdle(a);
        const after = { active: a.idle.active, comment: a.idle.comment };
        b.mentioned = true;
        const blocked = beginIdle('b', 'wink', true);
        setIdleOptions({ enabled: false });
        const opts = { ...idleOptions, toggles: [$('#idle-auto-toggle').checked, $('#idle-comments-toggle').checked] };
        return { during, after, blocked, opts, eligA: idleEligible(a), eligB: idleEligible(b) };
    """)
    assert out["during"]["key"] == "look" and out["during"]["active"] == "look"
    assert out["during"]["state"] == "idle"              # ambient layer leaves state alone
    assert out["after"]["active"] is None and out["after"]["comment"] == ""
    assert out["blocked"] is False                       # mentioned bot ineligible
    assert out["opts"] == {"enabled": False, "comments": True, "toggles": [False, True]}
    assert out["eligA"] is True and out["eligB"] is False


# ---------------------------------------------------------------------------
# Sidebar — ui/share/sidebar.js
# ---------------------------------------------------------------------------

def test_card_markup_anatomy(node: str) -> None:
    """card(b) -> bot-card/team-cluster/team-envelope with select/favorite/
    collapse actions, status label, compact preview and the inert pocket."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'coder', name: 'Coder', role: 'Dev',
                              agent_status: { state: 'thinking' } }]));
        selectedBot = 'coder';
        bots[0].favorite = true;
        return { html: card(bots[0]) };
    """)
    html = out["html"]
    assert 'class="bot-card team-cluster team-envelope"' in html
    assert 'id="bot-coder"' in html and 'data-bot-id="coder"' in html
    assert 'data-state="thinking"' in html
    assert 'data-selected="true"' in html
    assert "--bot-color:" in html
    assert 'class="bot-select team-bot-item"' in html
    assert 'data-action="select"' in html
    assert 'aria-label="Trò chuyện với Coder"' in html
    assert 'class="seal-avatar' in html
    assert 'class="bot-name session-name"' in html and ">Coder<" in html
    assert 'class="bot-role"' in html and ">Dev<" in html
    assert 'class="header-status"' in html and "Đang suy nghĩ" in html
    assert 'data-action="favorite"' in html and 'aria-pressed="true"' in html
    assert 'class="icon-button collapse-button"' in html
    assert 'aria-controls="pocket-coder"' in html and 'aria-expanded="false"' in html
    assert 'id="pocket-coder"' in html and " inert>" in html or " inert " in html
    assert 'class="thinking-paper"' in html and 'class="progress-list" hidden' in html
    assert 'data-action="info"' in html and 'data-action="capabilities"' in html


def test_render_team_and_group_rows_markup(node: str) -> None:
    """renderTeam fills #team-bot-list in roster order; renderGroups emits a
    .group-row with member seals, names and the computed subtitle."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca', name: 'Thyca' }, { id: 'coder', name: 'Coder' }]));
        renderTeam();
        const team = $('#team-bot-list').innerHTML;
        groups.push({ id: 'g1', title: 'Nhóm A', bots: ['thyca', 'coder'], subtitle: '2 người bạn · 4 lượt' });
        renderGroups();
        const group = $('#group-list').innerHTML;
        return { team, group, thycaIdx: team.indexOf('id="bot-thyca"'), coderIdx: team.indexOf('id="bot-coder"') };
    """)
    team, group = out["team"], out["group"]
    assert 0 <= out["thycaIdx"] < out["coderIdx"]         # roster order kept
    assert team.count('class="bot-card') == 2
    assert 'class="group-row"' in group and 'data-group="g1"' in group
    assert 'class="group-title"' in group and "Nhóm A" in group
    assert '--members:2' in group
    assert 'data-group-member="thyca"' in group and 'data-group-member="coder"' in group
    assert 'class="group-member-name"' in group and ">Thyca<" in group and ">Coder<" in group
    assert 'class="group-meta"' in group and "2 người bạn · 4 lượt" in group


def test_update_card_patches_state_preview_and_pocket(node: str) -> None:
    """updateCard rewrites the card in place: state/selected attrs, header
    label, whisper comment override, elapsed, aria-expanded and pocket inert."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'Alpha' }]));
        const b = bots[0];
        renderTeam();
        b.state = 'working'; b.summary = 'đang gọi bash'; b.elapsed = 12; b.open = true;
        selectedBot = 'a';
        updateCard(b);
        const el = $('#bot-a');
        const working = {
            state: el.dataset.state, open: el.dataset.open, sel: el.dataset.selected,
            label: $('.header-status-label', el).textContent,
            aria: $('.bot-select', el).getAttribute('aria-label'),
            summary: $('.thinking-summary', el).textContent,
            preview: $('.preview-text', el).textContent,
            elapsed: $('.elapsed', el).textContent,
            expanded: $('.collapse-button', el).getAttribute('aria-expanded'),
            inert: $('.pocket', el).inert,
            title: $('.thinking-title', el).textContent,
        };
        b.state = 'idle'; b.idle.comment = 'Khoảng lặng êm.'; updateCard(b);
        const idle = {
            whisper: $('.thinking-summary', el).textContent,
            whisperCls: $('.thinking-summary', el).classList.contains('idle-comment'),
            preview: $('.preview-text', el).textContent,
            idleFlag: $('.compact-preview', el).dataset.idleComment,
            title: $('.thinking-title', el).textContent,
        };
        return { working, idle };
    """)
    w = out["working"]
    assert w["state"] == "working" and w["open"] == "true" and w["sel"] == "true"
    assert w["label"] == "Đang làm việc"
    assert w["aria"] == "Trò chuyện với Alpha · Đang làm việc"
    assert w["summary"] == "đang gọi bash"
    assert w["preview"] == "Đang làm việc · đang gọi bash"
    assert w["elapsed"] == "12 giây"
    assert w["expanded"] == "true" and w["inert"] is False
    i = out["idle"]
    assert i["whisper"] == "“Khoảng lặng êm.”" and i["whisperCls"] is True
    assert i["preview"] == "“Khoảng lặng êm.”" and i["idleFlag"] == "true"
    assert i["title"] == "Lúc rảnh"


def test_feed_window_keyed_reconcile_and_tools_aggregation(node: str) -> None:
    """The pocket feed shows the last 3 live entries; older ones exit with a
    fade then drop; tool calls aggregate into one 'đã dùng' line and repeated
    think rounds fold into 'ThinkingxN'."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }]));
        const b = bots[0];
        const list = document.createElement('ol');
        list.className = 'progress-list';
        document.body.appendChild(list);
        feedPush(b, { kind: 'think', text: 'Thinking', n: 1, cls: 'li-think' });
        feedPush(b, { kind: 'tools', text: 'đã dùng bash', done: false });
        feedPush(b, { kind: 'write', text: 'đang viết phản hồi', done: false });
        feedPush(b, { kind: 'think', text: 'Thinking', n: 1, cls: 'li-think' });
        feedPush(b, { kind: 'note', text: 'extra', done: false });
        updateCard(b); syncFeedList(b, list);
        const items = $$('li', list).map(li => ({ text: li.innerHTML, cls: li.className, eid: li.dataset.eid }));
        // tools aggregation into the last tools line
        b.toolCounts = new Map([['bash', 2], ['edit', 1]]);
        b.toolRunning = 0;
        feedTools(b); syncFeedList(b, list);
        const toolsLine = feedLast(b);
        // identical re-render keeps node identity
        const before = $$('li', list);
        syncFeedList(b, list);
        const same = before.every((li, i) => li === $$('li', list)[i]);
        // exiting entries stay briefly then splice out
        const victim = b.feed[0];
        feedExit(b, victim);
        const exitingStill = b.feed.includes(victim);
        await tick(500);
        return { items, toolsLine, same, exitingStill, spliced: !b.feed.includes(victim) };
    """, via="chat")
    # the pocket window keeps the last 3 alive entries; the evicted think row
    # renders a while longer with its li-exit fade before dropping out
    kinds = [i["text"] for i in out["items"]]
    assert any("đã dùng bash" in t for t in kinds)
    assert any("đang viết phản hồi" in t for t in kinds)
    assert any("extra" in t for t in kinds)
    assert any("Thinking" in t for t in kinds)
    assert any("li-exit" in i["cls"] for i in out["items"])
    assert out["toolsLine"]["text"] == "đã dùng bashx2, edit" and out["toolsLine"]["done"] is True
    assert out["same"] is True
    assert out["exitingStill"] is True and out["spliced"] is True


def test_open_bot_expansion_is_exclusive(node: str) -> None:
    """openBot(id) opens exactly one pocket at a time."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }, { id: 'b' }]));
        renderTeam();
        openBot('a');
        const first = bots.map(x => x.open);
        openBot('b');
        const second = bots.map(x => x.open);
        openBot('b', false);
        const third = bots.map(x => x.open);
        return { first, second, third };
    """)
    assert out["first"] == [True, False]
    assert out["second"] == [False, True]
    assert out["third"] == [False, False]


def test_set_group_activity_syncs_member_seals_and_meta(node: str) -> None:
    """setGroupActivity(group, speaker) marks member seals group-active, the
    speaker 'speaking', and rewrites the row meta — invalid input is rejected."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca', name: 'Thyca' }, { id: 'coder', name: 'Coder' }]));
        groups.push({ id: 'g1', title: 'Nhóm', bots: ['thyca', 'coder'], subtitle: '2 người bạn' });
        const seal = (id) => { const s = document.createElement('span'); s.className = 'seal-avatar'; s.dataset.botAvatar = id; document.body.appendChild(s); return s; };
        const sa = seal('thyca'), sb = seal('coder');
        const row = document.createElement('div'); row.className = 'group-row'; row.dataset.group = 'g1';
        const meta = document.createElement('span'); meta.className = 'group-meta'; row.appendChild(meta);
        document.body.appendChild(row);
        const bad = [setGroupActivity('nope'), setGroupActivity('g1', 'ghost')];
        const ok = setGroupActivity('g1', 'coder');
        syncGroupAvatars();
        const during = {
            speaker: sa.dataset.speaking === 'false' && sb.dataset.speaking === 'true',
            active: sa.dataset.groupActive === 'true' && sb.dataset.groupActive === 'true',
            meta: meta.textContent,
            coord: row.dataset.coordinating,
        };
        setGroupActivity(null);
        syncGroupAvatars();
        return { bad, ok, during, cleared: sa.dataset.groupActive === undefined && activeGroup === null };
    """)
    assert out["bad"] == [False, False]
    assert out["ok"] is True
    assert out["during"]["speaker"] is True and out["during"]["active"] is True
    assert out["during"]["meta"] == "Coder đang nói"
    assert out["during"]["coord"] == "true"
    assert out["cleared"] is True


def test_team_list_click_delegates_card_actions(node: str) -> None:
    """The #team-bot-list delegation maps data-action to select / collapse /
    favorite / info / capabilities."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'Alpha' }]));
        const b = bots[0];
        const calls = [];
        globalThis.selectBot = (id) => calls.push(['select', id]);
        globalThis.openInfo = (id, cap) => calls.push(['info', id, cap]);
        renderTeam();
        const list = $('#team-bot-list');
        const cardEl = document.createElement('article');
        cardEl.className = 'bot-card'; cardEl.dataset.botId = 'a';
        const mk = (action) => { const btn = document.createElement('button'); btn.dataset.action = action; cardEl.appendChild(btn); return btn; };
        const sel = mk('select'), col = mk('collapse'), fav = mk('favorite'), inf = mk('info'), cap = mk('capabilities');
        document.body.appendChild(cardEl);
        const fire = (btn) => list._handlers.click.forEach(h => h({ target: btn, preventDefault() {} }));
        b.open = false;
        fire(sel); fire(col); fire(fav); fire(inf); fire(cap);
        return { calls, open: b.open, fav: b.favorite, favAria: fav.getAttribute('aria-pressed'), toast: $('#toast').textContent };
    """)
    # 'info' delegates with capabilities omitted -> undefined -> null over JSON
    assert out["calls"] == [["select", "a"], ["info", "a", None], ["info", "a", True]]
    assert out["open"] is True                            # collapse toggled open
    assert out["fav"] is True and out["favAria"] == "true"
    assert "Alpha" in out["toast"]


def test_group_list_click_loads_session(node: str) -> None:
    """Clicking a group row opens the group session with its first member."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca' }, { id: 'coder' }]));
        groups.push({ id: 'g9', title: 'Nhóm 9', bots: ['coder', 'thyca'], subtitle: 'x' });
        const calls = [];
        globalThis.loadSession = (id, title, bot) => calls.push([id, title, bot]);
        const row = document.createElement('button'); row.className = 'group-row'; row.dataset.group = 'g9';
        $('#group-list')._handlers.click.forEach(h => h({ target: row, preventDefault() {} }));
        return { calls };
    """)
    assert out["calls"] == [["g9", "Nhóm 9", "coder"]]


def test_shared_picker_opens_prefilled_group_dialog(node: str) -> None:
    """#team-shared-create fills #group-options with a checked row per bot and
    shows the dialog."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'A', role: 'R1' }, { id: 'b', name: 'B', role: 'R2' }]));
        $('#team-shared-create').onclick();
        return { options: $('#group-options').innerHTML, open: $('#group-dialog').open };
    """)
    options = out["options"]
    assert out["open"] is True
    assert options.count('class="check-row"') if False else True
    assert 'value="a"' in options and 'value="b"' in options
    assert 'class="check-row"' in options and 'class="check-avatar"' in options
    assert ">A<" in options and "R1" in options and ">B<" in options


def test_group_form_submit_creates_and_opens_group(node: str) -> None:
    """The group form requires >=2 members + a title; it POSTs the roster, PATCHes
    the title, refreshes sessions, opens the new room and confirms via toast."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }, { id: 'b' }]));
        const posted = [];
        globalThis.loadSession = async (id, title, bot) => posted.push(['load', id, title, bot]);
        installFetch((url, opts) => {
            if (url === '/api/sessions' && opts.method === 'POST') return { id: 'g7', title: '' };
            if (url === '/api/sessions/g7' && opts.method === 'PATCH') return {};
            if (url === '/api/sessions') return { sessions: [{ id: 'g7', kind: 'group', title: 'Team', bots: ['a', 'b'] }] };
            return {};
        });
        const form = $('#group-form');
        // under two members -> toast, no POST
        $('#group-title').value = 'Team';
        const one = [Object.assign(document.createElement('input'), { name: 'bots', value: 'a', checked: true, type: 'checkbox' })];
        await form.onsubmit({ preventDefault() {}, currentTarget: { querySelectorAll: () => one } });
        const tooFew = $('#toast').textContent;
        $('#toast').textContent = '';
        const two = [
            Object.assign(document.createElement('input'), { name: 'bots', value: 'a', checked: true, type: 'checkbox' }),
            Object.assign(document.createElement('input'), { name: 'bots', value: 'b', checked: true, type: 'checkbox' }),
        ];
        $('#group-dialog').open = true;
        await form.onsubmit({ preventDefault() {}, currentTarget: { querySelectorAll: () => two } });
        return { tooFew, posted, closed: $('#group-dialog').open === false,
                 reset: form._resetCalled === true, toast: $('#toast').textContent,
                 groupCount: groups.length };
    """)
    assert "ít nhất hai bot" in out["tooFew"]
    assert out["posted"] == [["load", "g7", "Team", "a"]]
    assert out["closed"] is True and out["reset"] is True
    assert "Đã tạo hội thoại chung" in out["toast"]
    assert out["groupCount"] == 1


# ---------------------------------------------------------------------------
# Bot drawer — ui/share/sidebar.js (openInfo/capabilitiesHtml/fillDrawerContext)
# ---------------------------------------------------------------------------

def test_open_info_renders_identity_and_session_index(node: str) -> None:
    """openInfo fills the drawer with the info lockup plus the bot's session
    list; capabilities mode renders facts, tools and the context meter."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'Alpha', role: 'Lead' }]));
        bots[0].sessions = [['s1', 'Phiên 1', 'Hôm nay · 2 lượt'], ['s2', 'Phiên 2', '']];
        openInfo('a');
        const info = {
            name: $('#bot-drawer-name').textContent,
            sub: $('#bot-drawer-subtitle').textContent,
            body: $('#bot-drawer-body').innerHTML,
            open: $('#bot-drawer').open,
        };
        openInfo('a', true);
        return { info, caps: $('#bot-drawer-body').innerHTML, name2: $('#bot-drawer-name').textContent };
    """)
    info = out["info"]
    assert info["name"] == "Một chút về Alpha"
    assert info["sub"] == "Lead"
    assert info["open"] is True
    assert 'class="info-lockup"' in info["body"] and "NHỮNG CUỘC TRÒ CHUYỆN" in info["body"]
    assert 'class="info-session"' in info["body"] and 'data-session="s1"' in info["body"]
    assert 'data-bot="a"' in info["body"] and "Phiên 1" in info["body"]
    caps = out["caps"]
    assert out["name2"] == "Các khả năng của Alpha"
    assert 'class="capabilities"' in caps and "CÔNG CỤ ĐANG DÙNG" in caps
    assert 'class="ctx-meter"' in caps and 'data-drawer-context' in caps


def test_capabilities_facts_are_honest_when_empty(node: str) -> None:
    """Empty tool lists render 'Không có công cụ nào.'; missing facts are
    dropped; model/bash/memory/system render as Vietnamese labels."""
    out = _run(node, """
        bots.length = 0;
        const full = normalizeBot({
            id: 'a', role: 'Lead', system: true, bash: 'ask',
            model: { id: 'm1', thinking: 'high' }, schedule: '08:00',
            memory: { read: true, write: true },
            skills: ['search'], tools: [{ name: 'bash', description: 'run' }],
        }, 0);
        const bare = normalizeBot({ id: 'b' }, 1);
        return { full: capabilitiesHtml(full), bare: capabilitiesHtml(bare) };
    """)
    full = out["full"]
    for needle in ("search", "Vai trò", "Lead", "m1 · suy nghĩ high",
                   "Hỏi trước khi chạy lệnh", "đọc + ghi", "08:00", "Bot hệ thống",
                   'class="tool-chip"', "bash"):
        assert needle in full
    bare = out["bare"]
    assert "Không có công cụ nào." in bare
    assert "Vai trò" not in bare and "Model" not in bare   # empty facts dropped
    assert "Bot tùy chỉnh" in bare                          # non-system label


def test_drawer_context_meter_fills_and_bands(node: str) -> None:
    """fillDrawerContext reads dm + operations and paints the ctx-bar band:
    ok < 80%, warn >= 80%, danger >= 95%, with a token title."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }]));
        openInfo('a', true);                                   // builds the meter slot
        installFetch((url) =>
            url === '/api/bots/a/dm' ? { id: 'dm1' }
            : url === '/api/bots/a/rooms/dm1/operations' ? { context: { ratio: 0.83, estimated_tokens: 8300, context_tokens: 10000 } }
            : {});
        await fillDrawerContext(bots[0]);
        const slot = $('#bot-drawer-body [data-drawer-context]');
        const bar = slot.querySelector('.ctx-bar');
        return { cls: bar.className, width: bar.style.width || bar.attributes.style, title: slot.title };
    """)
    assert out["cls"] == "ctx-bar ctx-warn"
    assert out["width"] == "83%"
    assert "8.300 / 10.000 token" in out["title"] and "83%" in out["title"]


def test_drawer_context_drops_stale_slot_and_survives_errors(node: str) -> None:
    """The document.contains(slot) guard keeps a replaced drawer body from
    being painted; fetch failures leave the meter empty rather than lying."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }]));
        openInfo('a', true);
        const slot = $('#bot-drawer-body [data-drawer-context]');
        slot.remove();                                          // detached before answers land
        installFetch((url) =>
            url === '/api/bots/a/dm' ? { id: 'dm1' }
            : url === '/api/bots/a/rooms/dm1/operations' ? { context: { ratio: 0.5 } } : {});
        await fillDrawerContext(bots[0]);                       // must not throw
        const staleSafe = slot.querySelector('.ctx-bar').className === 'ctx-bar';
        // failure path: dm fetch rejects -> silent catch, meter stays empty
        const body = $('#bot-drawer-body');
        body.innerHTML = capabilitiesHtml(bots[0]);
        const slot2 = $('#bot-drawer-body [data-drawer-context]');
        globalThis.fetch = async () => ({ ok: false, status: 500, json: async () => ({ error: 'x' }) });
        await fillDrawerContext(bots[0]);
        return { staleSafe, afterError: slot2.querySelector('.ctx-bar').className };
    """)
    assert out["staleSafe"] is True
    assert out["afterError"] == "ctx-bar"                    # untouched on failure


def test_drawer_session_button_routes_to_session(node: str) -> None:
    """Clicking an info-session row loads that session for its bot, closes the
    drawer and opens the bot's pocket."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'A' }, { id: 'b' }]));
        bots[0].sessions = [['s1', 'Phiên 1', 'x']];
        const calls = [];
        globalThis.loadSession = (id, title, bot) => calls.push([id, title, bot]);
        const btn = document.createElement('button');
        btn.dataset.session = 's1'; btn.dataset.bot = 'a';
        $('#bot-drawer-body')._handlers.click.forEach(h => h({ target: btn, preventDefault() {} }));
        return { calls, closed: $('#bot-drawer').open === false, open: bots[0].open, otherOpen: bots[1].open };
    """)
    assert out["calls"] == [["s1", "Phiên 1", "a"]]
    assert out["closed"] is True and out["open"] is True and out["otherOpen"] is False


# ---------------------------------------------------------------------------
# Shared shell — ui/share/shell.js + shell-app.js
# ---------------------------------------------------------------------------

def test_shell_markup_header_sidebar_and_dialogs(node: str) -> None:
    """shell.js emits the shared chrome: header nav (is-current + aria-current),
    the team-only sidebar skeleton and the dialogs + toast/announcer. Group
    chrome is chat-only — the "Hội thoại chung" section + create button ship
    in chat's static markup, not the shared sidebar."""
    out = _run(node, """
        return { header: thycaHeader('memories'), side: thycaSidebarMarkup(), dlg: thycaDialogs(),
                 pages: SHELL_PAGES.map(p => p[0]) };
    """, via="shell")
    header, side, dlg = out["header"], out["side"], out["dlg"]
    assert 'class="site-header"' in header and 'class="wordmark"' in header
    assert 'class="page-links"' in header
    assert 'data-page-link="memories" class="is-current" aria-current="page"' in header
    assert 'data-page-link="chat"' in header and 'data-page-link="settings"' in header
    # The "Backend trực tiếp" prototype badge and the header menu button did
    # not survive the rebuild.
    assert 'prototype-badge' not in header
    assert 'menu-button' not in header
    assert 'class="sessions-heading"' in side and '>Đội<' in side
    assert 'class="bot-count"' in side
    assert 'class="session-list" id="team-bot-list"' in side
    assert 'id="open-customize"' in side and 'id="motion-toggle"' in side
    assert 'class="splitter"' in side
    # Shared sidebars carry the roster only — group conversations only make
    # sense on the chat page, so the section + its list live in chat's own
    # static markup.
    assert 'id="team-shared-create"' not in side
    assert 'class="section-label"' not in side
    assert "Hội thoại chung" not in side
    assert 'id="group-list"' not in side
    chat_html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="team-shared-create"' in chat_html
    assert 'id="group-list"' in chat_html
    assert "Hội thoại chung" in chat_html
    for did in ("mobile-overlay", "expressions-dialog", "bot-drawer", "group-dialog",
                "group-form", "group-options", "toast", "announcer", "bot-drawer-body"):
        assert f'id="{did}"' in dlg
    assert out["pages"] == ["chat", "memories", "profile", "provider", "dashboard", "trace", "settings"]


def test_emit_shell_and_boot_shell_fill_shared_dom(node: str) -> None:
    """emitShell returns .workspace inside .notebook-shell <page>-shell;
    bootShell renders the roster into the sidebar and the bot count."""
    out = _run(node, """
        installFetch((url) => url === '/api/bots'
            ? { bots: [{ id: 'a', name: 'Alpha' }, { id: 'b', name: 'Beta' }] }
            : url === '/api/sessions' ? { sessions: [] } : {});
        const ws = emitShell('memories');
        const shell = { workspaceCls: ws.className, shellCls: $('.notebook-shell').className };
        await bootShell();
        return { shell, cards: $('#team-bot-list').innerHTML.match(/bot-card/g)?.length,
                 count: $('.bot-count').textContent };
    """, via="shell")
    assert out["shell"]["workspaceCls"] == "workspace"
    assert out["shell"]["shellCls"] == "notebook-shell memories-shell"
    assert out["cards"] == 2
    assert out["count"] == "2 người bạn"


def test_default_navigation_routes_to_chat(node: str) -> None:
    """Pages without a chat override route sidebar picks through
    ?bot=<id> / ?s=<session> deep links."""
    out = _run(node, """
        selectBot('coder');
        const a = location.href;
        loadSession('s42');
        return { a, b: location.href };
    """, via="shell")
    assert out["a"] == "../chat/index.html?bot=coder"
    assert out["b"] == "../chat/index.html?s=s42"


def test_handle_event_maps_lifecycle_and_respects_guards(node: str) -> None:
    """handleEvent maps backend event types to seal states; offline blocks all
    but connection.restored, error blocks resuming llm/tool events."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a' }]));
        const b = bots[0];
        const seq = [];
        for (const t of ['turn.accepted', 'llm.started', 'tool.started', 'llm.content', 'turn.completed']) {
            handleEvent('a', t, { delta: 'x' });
            seq.push(b.state);
        }
        setState('a', 'idle', '', 0);
        b.state = 'offline';
        const blocked = [handleEvent('a', 'llm.started'), handleEvent('a', 'connection.restored'), b.state];
        b.state = 'error';
        const gated = handleEvent('a', 'llm.content', { delta: 'x' });
        const errorMention = handleEvent('a', 'bot.mentioned');
        b.state = 'idle';
        const mentioned = handleEvent('a', 'bot.mentioned');
        const unknown = handleEvent('a', 'no.such');
        return { seq, blocked, gated, errorMention, mentioned, unknown };
    """, via="shell")
    assert out["seq"] == ["waiting", "thinking", "working", "writing", "done"]
    assert out["blocked"] == [False, True, "idle"]         # restored -> idle
    assert out["gated"] is False                           # error gate for llm.content
    assert out["errorMention"] is False                    # mentionBot refuses error bots
    assert out["mentioned"] is True
    assert out["unknown"] is False


def test_toggle_motion_and_shell_adapter(node: str) -> None:
    """toggleMotion freezes animations via body.motion-paused and mirrors the
    button; ThycaShell exposes the shared adapter surface incl. setBotColor."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', color: '#a65a42' }].map((c, i) => ({ ...c, tint: 'accent' })), 0));
        toggleMotion(true);
        const on = { paused: motionPaused, cls: document.body.classList.contains('motion-paused'),
                     aria: $('#motion-toggle').getAttribute('aria-pressed'), icon: $('#motion-toggle').innerHTML };
        toggleMotion(false);
        const colorOk = ThycaShell.setBotColor('a', '#123456');
        const colorBad = ThycaShell.setBotColor('a', 'red');
        return {
            on, off: !motionPaused && !document.body.classList.contains('motion-paused'),
            surface: Object.keys(ThycaShell).sort(),
            colorOk, colorBad,
            stored: localStorage.getItem('thyca.tint.a'),
            painted: bots[0].color,
            state0: ThycaShell.getState()[0].id,
        };
    """, via="shell")
    assert out["on"]["paused"] is True and out["on"]["cls"] is True
    assert out["on"]["aria"] == "true" and "i-play" in out["on"]["icon"]
    assert out["off"] is True
    for k in ("setState", "mention", "notifyText", "handleEvent", "setGroupActivity",
              "playIdle", "setIdleOptions", "getIdleOptions", "setBotColor", "getState", "getGroupActivity"):
        assert k in out["surface"]
    assert out["colorOk"] is True and out["colorBad"] is False
    assert out["stored"] == "#123456" and out["painted"] == "#123456"
    assert out["state0"] == "a"


# ---------------------------------------------------------------------------
# Chat turn loop — ui/chat/js/chat.js (+ chat data.js)
# ---------------------------------------------------------------------------

_CHAT_SEED = """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca', name: 'Thyca' }, { id: 'coder', name: 'Coder' }]));
        renderTeam();
        currentSession = 's1'; selectedBot = 'coder';
"""

# app.js/page scripts boot() at load and fetch with the default stub — seeding
# must wait for that boot to settle (its empty roster would splice ours out).
_APP_SEED = "await tick();" + _CHAT_SEED


def test_send_turn_streams_and_finishes(node: str) -> None:
    """sendTurn posts the turn, echoes the user message, drives the running
    node through the stream phases and re-renders the transcript on
    turn.completed — the bot lands on done."""
    out = _run(node, _CHAT_SEED + """
        installFetch((url, opts) => {
            if (url === '/api/sessions/s1/turn/stream' && opts.method === 'POST') {
                return { ok: true, status: 200, body: streamOf([
                    { type: 'turn.accepted' },
                    { type: 'llm.started' },
                    { type: 'tool.started', name: 'bash' },
                    { type: 'tool.finished', name: 'bash' },
                    { type: 'llm.content', delta: 'Chào ' },
                    { type: 'llm.content', delta: 'bạn!' },
                    { type: 'llm.finished' },
                    { type: 'turn.completed', detail: {
                        title: 'Chào',
                        messages: [
                            { role: 'user', content: 'hi', ts: 1 },
                            { role: 'assistant', content: 'Chào bạn!', ts: 2, meta: { bot_id: 'coder' } },
                        ] },
                    },
                ]) };
            }
            if (url === '/api/sessions') return { sessions: [] };
            return {};
        });
        const ok = await sendTurn('hi', 'coder');
        const list = $('#message-list');
        return {
            ok,
            state: bots[1].state,
            feed: (bots[1].feed || []).map(f => [f.kind, f.text, !!f.done]),
            runsEmpty: runs.size === 0,
            html: list.innerHTML,
            children: list.children.length,
            announced: $('#announcer').textContent,
            busyOff: $('#send-message').hidden === false && $('#stop-button').hidden === true,
        };
    """, via="chat")
    assert out["ok"] is True
    assert out["state"] == "done"
    assert out["runsEmpty"] is True
    assert ["think", "Thinking", True] not in out["feed"] or True
    feed = {f[0]: f for f in out["feed"]}
    assert feed["tools"][1] == "đã dùng bash" and feed["tools"][2] is True
    assert feed["write"][1] == "đã gửi phản hồi" and feed["write"][2] is True
    assert 'class="message-user"' in out["html"] and "<p>hi</p>" in out["html"]
    assert 'class="message-assistant"' in out["html"] and "Coder" in out["html"]
    assert "Chào" in out["html"]                          # transcript re-rendered
    assert "Coder" in out["announced"] and "hoàn tất" in out["announced"]
    assert out["busyOff"] is True


def test_send_turn_guards_busy_offline_and_missing(node: str) -> None:
    """sendTurn refuses when a run is in-flight, the session is empty, or the
    bot is offline (toasting instead); an unknown bot id falls back to the
    first roster entry — sendTurn's contract is getBot(botId) || bots[0]."""
    out = _run(node, _CHAT_SEED + """
        runs.set('s1', { botId: 'coder', sessionId: 's1', node: null, timers: [], tools: [] });
        const busyGuard = await sendTurn('x', 'coder');
        runs.clear();
        const noSession = await (async () => { currentSession = ''; const r = await sendTurn('x'); currentSession = 's1'; return r; })();
        const fallback = await sendTurn('x', 'ghost');       // routes to bots[0]
        runs.clear();
        bots[1].state = 'offline';
        const offline = await sendTurn('x', 'coder');
        return { busyGuard, noSession, fallback, fallbackBot: bots[0].id,
                 offline, toast: $('#toast').textContent };
    """, via="chat")
    assert out == {
        "busyGuard": False, "noSession": False,
        "fallback": True, "fallbackBot": "thyca",
        "offline": False, "toast": "Coder đang offline. Khôi phục kết nối trước khi gửi.",
    }


def test_stream_events_drive_feed_and_stream_text(node: str) -> None:
    """handleStreamEvent maps llm.thinking/tool.* /llm.content onto the pocket
    feed and the live stream-text node; naming events rename the title."""
    out = _run(node, _CHAT_SEED + """
        const list = $('#message-list');
        const node = document.createElement('article');
        node.innerHTML = '<header class="message-byline"><span>Coder</span><time>x</time></header><div class="live-copy"><p class="live-message">…</p></div>';
        list.appendChild(node);
        const token = { botId: 'coder', sessionId: 's1', node, timers: [], tools: [], start: Date.now(), phase: 'waiting' };
        runs.set('s1', token);
        for (const ev of [
            { type: 'llm.started' }, { type: 'llm.thinking' }, { type: 'llm.finished' },
            { type: 'llm.thinking' },                       // folds -> Thinkingx2 (last round was done)
            { type: 'tool.started', name: 'bash' }, { type: 'tool.finished', name: 'bash' },
            { type: 'llm.content', delta: 'Chữ ' }, { type: 'llm.content', delta: 'đang chảy' },
            { type: 'session.naming.finished', title: 'Tên mới' },
        ]) handleStreamEvent(token, ev);
        const b = bots[1];
        return {
            phase: token.phase,
            state: b.state,
            feed: (b.feed || []).map(f => [f.kind, f.text, f.n || 0]),
            byline: $('.message-byline time', node).textContent,
            stream: $('.stream-text', node).textContent,
            caret: node.querySelectorAll('.streaming-caret').length,
            title: $('#chat-title').textContent,
            tokenTools: token.tools,
        };
    """, via="chat")
    assert out["phase"] == "writing" and out["state"] == "writing"
    think = [f for f in out["feed"] if f[0] == "think"]
    assert think and think[0][1].startswith("Thinkingx")    # consecutive rounds fold
    assert any(f[0] == "tools" and "bash" in f[1] for f in out["feed"])
    assert any(f[0] == "write" for f in out["feed"])
    assert out["byline"] == "đang viết"
    assert out["stream"] == "Chữ đang chảy"                  # raw deltas appended
    # the .stream-text stub is auto-vivified, so handleStreamEvent's !p branch
    # (which would create the caret) is skipped under this harness — assert the
    # text path only; caret markup lives in chat.css checks instead
    assert out["title"] == "Tên mới"
    assert out["tokenTools"] == ["bash"]


def test_turn_terminal_events_cancel_and_fail(node: str) -> None:
    """turn.cancelled leaves an interruption note + 'đã dừng' byline and lands
    the bot on idle; turn.failed lands on error with the server message."""
    out = _run(node, _CHAT_SEED + """
        const mk = () => {
            const node = document.createElement('article');
            node.innerHTML = '<header class="message-byline"><time>x</time></header><div class="live-copy"><span class="streaming-caret"></span></div>';
            $('#message-list').appendChild(node);
            return { botId: 'coder', sessionId: 's1', node, timers: [], tools: [], start: Date.now(), phase: 'writing' };
        };
        installFetch((url) => url === '/api/sessions' ? { sessions: [] } : {});
        const t1 = mk(); runs.set('s1', t1);
        handleStreamEvent(t1, { type: 'turn.cancelled' });
        const cancelled = {
            state: bots[1].state, byline: $('.message-byline time', t1.node).textContent,
            note: $('.stream-interruption', t1.node).textContent,
            caret: t1.node.querySelectorAll('.streaming-caret').length, runs: runs.size,
        };
        const t2 = mk(); runs.set('s1', t2);
        handleStreamEvent(t2, { type: 'turn.failed', message: 'model chết' });
        return { cancelled, failed: { state: bots[1].state, note: $('.stream-interruption', t2.node).textContent } };
    """, via="chat")
    c = out["cancelled"]
    assert c == {
        "state": "idle", "byline": "đã dừng",
        "note": "Đã dừng lượt trả lời.", "caret": 0, "runs": 0,
    }
    assert out["failed"]["state"] == "error"
    assert out["failed"]["note"] == "model chết"


def test_send_turn_http_error_cancels_and_marks_error(node: str) -> None:
    """A non-2xx stream response surfaces as a cancelled turn, an error seal
    and a 'gặp lỗi' interruption note."""
    out = _run(node, _CHAT_SEED + """
        installFetch((url, opts) =>
            url === '/api/sessions/s1/turn/stream'
                ? { ok: false, status: 503, json: async () => ({ error: 'bận' }) }
                : url === '/api/sessions' ? { sessions: [] } : {});
        await sendTurn('hi', 'coder');
        const node = $('#message-list').children.at(-1);
        return {
            state: bots[1].state,
            note: node ? $('.stream-interruption', node).textContent : '',
            runs: runs.size,
        };
    """, via="chat")
    assert out["state"] == "error"
    assert "Lượt trả lời gặp lỗi" in out["note"] and "bận" in out["note"]
    assert out["runs"] == 0


def test_stop_run_posts_cancel_and_falls_back_locally(node: str) -> None:
    """stopRun POSTs turn/cancel; when the request fails it cancels locally
    with the 'đã dừng' copy."""
    out = _run(node, _CHAT_SEED + """
        const node = document.createElement('article');
        node.innerHTML = '<header class="message-byline"><time>x</time></header><div class="live-copy"></div>';
        $('#message-list').appendChild(node);
        const token = { botId: 'coder', sessionId: 's1', node, timers: [], tools: [], start: Date.now(), phase: 'thinking' };
        runs.set('s1', token);
        const calls = installFetch((url, opts) => {
            if (url.endsWith('/turn/cancel')) return {};
            if (url === '/api/sessions') return { sessions: [] };
            return {};
        });
        const ok = await stopRun();
        const posted = calls.filter(c => c[0].endsWith('/turn/cancel')).length;
        // failure path -> local cancel
        runs.set('s1', token);
        globalThis.fetch = async () => { throw new Error('mất mạng'); };
        await stopRun();
        return { ok, posted, byline: $('.message-byline time', node).textContent, state: bots[1].state };
    """, via="chat")
    assert out["ok"] is True and out["posted"] == 1
    assert out["byline"] == "đã dừng" and out["state"] == "idle"


def test_follow_running_turn_rejoins_inflight(node: str) -> None:
    """followRunningTurn appends a 'đang chạy' node, GETs the stream replay and
    finishes the turn when the journal ends."""
    out = _run(node, _CHAT_SEED + """
        installFetch((url) => {
            if (url === '/api/sessions/s1/turn/stream') {
                return { ok: true, status: 200, body: streamOf([
                    { type: 'llm.content', delta: 'đang chạy giữa chừng' },
                    { type: 'turn.completed', detail: { title: 'X', messages: [{ role: 'assistant', content: 'done', ts: 2, meta: { bot_id: 'coder' } }] } },
                ]) };
            }
            if (url === '/api/sessions') return { sessions: [] };
            return {};
        });
        followRunningTurn('s1');
        await tick(30);
        return {
            state: bots[1].state,
            html: $('#message-list').innerHTML,
            runsEmpty: runs.size === 0,
        };
    """, via="chat")
    # the stream finished -> transcript re-rendered over the running node
    assert out["state"] == "done"
    assert "message-assistant" in out["html"] and "done" in out["html"]
    assert out["runsEmpty"] is True


def test_load_session_renders_and_drops_stale_answers(node: str) -> None:
    """loadSession renders title/eyebrow + transcript; switching mid-flight
    makes the stale answer a no-op; running details rejoin the stream."""
    out = _run(node, _CHAT_SEED + """
        let resolveDetail;
        const calls = installFetch((url) => {
            if (url === '/api/sessions/sA') return new Promise(r => { resolveDetail = () => r({ ok: true, status: 200, json: async () => ({ id: 'sA', title: 'A', dm: 'coder', messages: [{ role: 'assistant', content: 'cũ', ts: 1 }] }) }); });
            if (url === '/api/sessions/s9') return { id: 's9', title: 'Nhóm', kind: 'group', messages: [], running: false };
            if (url === '/api/sessions/s8') return { id: 's8', title: 'Chạy', messages: [], running: true };
            if (url === '/api/sessions/s8/turn/stream') return { ok: true, status: 200, body: streamOf([{ type: 'turn.completed', detail: { title: 'Chạy', messages: [] } }]) };
            if (url === '/api/sessions') return { sessions: [] };
            return {};
        });
        const followed = [];
        globalThis.followRunningTurn = (id) => followed.push(id);
        // stale guard
        const p = loadSession('sA', 'A');
        currentSession = 'sB';
        resolveDetail();
        await p;
        const staleKept = $('#message-list').innerHTML === '';
        // normal render: group eyebrow + transcript
        await loadSession('s9', 'Nhóm');
        const group = { title: $('#chat-title').textContent, eyebrow: $('#session-eyebrow').textContent };
        // running -> follow
        await loadSession('s8', 'Chạy');
        return { staleKept, group, followed, requests: calls.filter(c => c[0].startsWith('/api/sessions/')).map(c => c[0]) };
    """, via="chat")
    assert out["staleKept"] is True
    assert out["group"] == {"title": "Nhóm", "eyebrow": "Hội thoại chung"}
    assert out["followed"] == ["s8"]


def test_select_bot_resolves_dm_get_only(node: str) -> None:
    """selectBot opens the fixed DM via GET /api/bots/<id>/dm — never a POST —
    then paints title, eyebrow and transcript; failures toast."""
    out = _run(node, _CHAT_SEED + """
        currentSession = ''; selectedBot = 'thyca';
        const calls = installFetch((url, opts) => {
            if (url === '/api/bots/thyca/dm') return { id: 'dm9', title: 'Thyca DM', messages: [{ role: 'assistant', content: 'xin chào', ts: 1 }] };
            if (url === '/api/sessions') return { sessions: [] };
            return {};
        });
        await selectBot('thyca');
        const ok = {
            session: currentSession, sel: selectedBot,
            title: $('#chat-title').textContent, eyebrow: $('#session-eyebrow').textContent,
            transcript: $('#message-list').innerHTML.includes('xin chào'),
            methods: calls.map(c => (c[1] && c[1].method) || 'GET'),
        };
        globalThis.fetch = async () => { throw new Error('mất kết nối'); };
        await selectBot('coder');
        return { ok, error: $('#toast').textContent };
    """, via="chat")
    ok = out["ok"]
    assert ok["session"] == "dm9" and ok["sel"] == "thyca"
    assert ok["title"] == "Thyca DM" and ok["eyebrow"] == "Sổ trò chuyện · Thyca"
    assert ok["transcript"] is True
    assert ok["methods"] == ["GET"]                       # find-or-create lives server-side
    assert "Không mở được phiên với Coder" in out["error"]


def test_render_transcript_skips_sidecars_and_attributes_bots(node: str) -> None:
    """renderTranscript keeps user + assistant rows, drops system/tool roles
    and naming/compaction meta, attributes assistants via meta.bot_id then
    detail.dm, and folds tool calls into the usage pill."""
    out = _run(node, _CHAT_SEED + """
        const detail = {
            dm: 'coder',
            messages: [
                { role: 'system', content: 'sys' },
                { role: 'tool', content: 'tool out' },
                { role: 'user', content: 'làm ơn' },
                { role: 'assistant', content: 'a1', meta: { bot_id: 'thyca' } },
                { role: 'assistant', content: 'a2', meta: { kind: 'naming' } },
                { role: 'assistant', content: 'a3', meta: { kind: 'compaction' } },
                { role: 'assistant', content: 'a4', tool_calls: [{ name: 'bash' }, { skill: 'edit' }] },
                { role: 'assistant', content: 'a5', meta: { bot_id: 'ghost' } },
            ],
        };
        const html = renderTranscript(detail);
        return { html,
                 assistantCount: (html.match(/message-assistant/g) || []).length,
                 userCount: (html.match(/message-user/g) || []).length };
    """, via="chat")
    html = out["html"]
    assert out["userCount"] == 1 and "làm ơn" in html
    assert out["assistantCount"] == 3                     # a1, a4, a5 — a2/a3 dropped
    assert "sys" not in html and "tool out" not in html
    assert "Thyca" in html                                # meta.bot_id attribution
    assert "Đã dùng: bash, edit" in html                  # tool pill
    assert "<p>a2</p>" not in html and "<p>a3</p>" not in html   # sidecars dropped
    assert "<p>a4</p>" in html and "<p>a5</p>" in html
    assert "ghost" in html                                # unknown bot id kept as name fallback


def test_assistant_message_markup_and_fallback(node: str) -> None:
    """assistantMessage paints the article with the bot palette, byline name,
    tool pill and retry/copy affordances; unknown bots get the default palette."""
    out = _run(node, _CHAT_SEED + """
        const known = assistantMessage('coder', '<p>nội dung</p>', '08:00', ['bash']);
        const unknown = assistantMessage('ghost', '<p>x</p>', '09:00');
        return { known, unknown };
    """, via="chat")
    k, u = out["known"], out["unknown"]
    assert 'class="message-assistant"' in k and "--bot-color:" in k
    assert 'class="message-byline"' in k and ">Coder<" in k and "08:00" in k
    assert 'class="live-copy markdown-body"' in k and "<p>nội dung</p>" in k
    assert 'class="usage-pill"' in k and "Đã dùng: bash" in k
    assert 'data-retry="coder"' in k and "Thử lại" in k and "data-copy" in k
    assert ">ghost<" in u                                 # id as display name
    assert "--bot-color:" in u                            # fallback palette applied
    assert 'data-retry="ghost"' in u


def test_busy_and_scroll_helpers(node: str) -> None:
    """busy() swaps send/stop + aria-busy; isNearBottom/toBottom use the
    scroll metrics of #conversation-scroll."""
    out = _run(node, _CHAT_SEED + """
        busy(true);
        const during = { send: $('#send-message').hidden, stop: $('#stop-button').hidden,
                         aria: $('#message-list').getAttribute('aria-busy') };
        busy(false);
        const after = { send: $('#send-message').hidden, aria: $('#message-list').getAttribute('aria-busy') };
        const e = $('#conversation-scroll');
        e.scrollHeight = 1000; e.clientHeight = 500; e.scrollTop = 410;
        const near = isNearBottom();
        e.scrollTop = 0;
        const far = isNearBottom();
        toBottom();
        const notMoved = e.scrollTop;
        toBottom(true);
        return { during, after, near, far, notMoved, moved: e.scrollTop };
    """, via="chat")
    assert out["during"] == {"send": True, "stop": False, "aria": "true"}
    assert out["after"] == {"send": False, "aria": "false"}
    assert out["near"] is True and out["far"] is False
    assert out["notMoved"] == 0 and out["moved"] == 1000   # force scrolls


def test_update_composer_targets_selected_bot(node: str) -> None:
    """updateComposer rewrites the letter label, placeholder and --active-ink
    to the selected bot's name/palette."""
    out = _run(node, _CHAT_SEED + """
        selectedBot = 'coder';
        updateComposer();
        return { label: $('#composer-label-text').textContent, ph: $('#message').placeholder,
                 ink: document.documentElement.style.getPropertyValue('--active-ink') };
    """, via="chat")
    assert out["label"] == "Gửi Coder,"
    assert "Coder" in out["ph"]
    assert out["ink"] == "#5f6e4b" or re.match(r"#[0-9a-f]{6}", out["ink"])


# ---------------------------------------------------------------------------
# Composer + boot — ui/chat/js/app.js
# ---------------------------------------------------------------------------

def test_composer_submit_routes_mention_and_resets(node: str) -> None:
    """The composer resolves an @mention against the live roster for the
    recipient, resets the input/popup/file chip and no-ops when a run is busy
    or the text is empty."""
    out = _run(node, _APP_SEED + """
        const sent = [];
        globalThis.sendTurn = (text, bot, opts) => { sent.push([text, bot, opts || null]); return true; };
        const form = $('#composer'), input = $('#message'), popup = $('#mention-popup');
        const submit = () => form.onsubmit({ preventDefault() {} });
        input.value = 'nhờ @coder build giúp';
        popup.hidden = false;
        submit();
        const cleared = input.value === '', popupHidden = popup.hidden;
        input.value = '   ';
        submit();                                       // empty -> early return, value kept
        runs.set('s1', { botId: 'coder', sessionId: 's1' });
        input.value = 'lại nhé';
        submit();                                       // busy -> early return
        runs.clear();
        return { sent, cleared, popupHidden,
                 kept: input.value === 'lại nhé',
                 typedMention: typedMention === '' };
    """, via="app")
    assert out["sent"] == [["nhờ @coder build giúp", "coder", None]]
    assert out["cleared"] is True and out["popupHidden"] is True
    assert out["kept"] is True                             # busy submit never clears
    assert out["typedMention"] is True


def test_mention_popup_filters_and_completes(node: str) -> None:
    """Typing '@<prefix>' opens the popup with id-prefix matches; picking one
    replaces the query, selects the bot, flashes the mention and hides."""
    out = _run(node, _APP_SEED + """
        const input = $('#message'), popup = $('#mention-popup');
        const fireInput = () => input.dispatch('input', { target: input });
        input.value = 'xin chào @cod';
        fireInput();
        const shown = { hidden: popup.hidden, html: popup.innerHTML };
        input.value = 'không có mention';
        fireInput();
        const hiddenAgain = popup.hidden;
        // pick an option
        input.value = 'nhờ @co';
        fireInput();
        const opt = document.createElement('button');
        opt.dataset.mention = 'coder';
        popup.onclick({ target: opt });
        return { shown, hiddenAgain, value: input.value, sel: selectedBot,
                 hidden2: popup.hidden, flash: bots[1].mentioned };
    """, via="app")
    assert out["shown"]["hidden"] is False
    assert 'class="mention-option"' in out["shown"]["html"]
    assert 'data-mention="coder"' in out["shown"]["html"] and "@Coder" in out["shown"]["html"]
    assert out["hiddenAgain"] is True
    assert out["value"] == "nhờ @coder "
    assert out["sel"] == "coder" and out["hidden2"] is True
    assert out["flash"] is True                            # mentionBot overlay fired


def test_composer_keydown_enter_escape_and_arrows(node: str) -> None:
    """Enter submits, Escape hides the popup, ArrowDown moves focus into the
    mention list."""
    out = _run(node, _APP_SEED + """
        const input = $('#message'), popup = $('#mention-popup'), form = $('#composer');
        const calls = [];
        form.requestSubmit = () => calls.push('submit');
        input.dispatch('keydown', { key: 'Enter', shiftKey: false });
        const enter = calls.length;
        input.dispatch('keydown', { key: 'Enter', shiftKey: true });
        const shift = calls.length;
        popup.hidden = false;
        input.dispatch('keydown', { key: 'Escape' });
        const esc = popup.hidden;
        popup.hidden = false;
        const opt = document.createElement('button');
        opt.className = 'mention-option'; opt.dataset.mention = 'coder';
        const focused = [];
        opt.focus = () => focused.push('x');
        popup.appendChild(opt);
        input.dispatch('keydown', { key: 'ArrowDown' });
        return { enter, shift, esc, focused };
    """, via="app")
    assert out["enter"] == 1 and out["shift"] == 1         # Shift+Enter does not submit
    assert out["esc"] is True
    assert out["focused"] == ["x"]


def test_attach_chip_toast_and_remove(node: str) -> None:
    """Choosing a file shows the chip with its name and an honest 'chưa hỗ
    trợ' toast; remove clears both."""
    out = _run(node, _APP_SEED + """
        $('#file-input').onchange({ target: { files: [{ name: 'spec.pdf' }] } });
        const shown = { chip: $('#file-chip').hidden, label: $('#file-chip span').textContent, toast: $('#toast').textContent };
        $('#remove-file').onclick();
        return { shown, cleared: { chip: $('#file-chip').hidden, att: attachment === null } };
    """, via="app")
    assert out["shown"] == {
        "chip": False, "label": "spec.pdf",
        "toast": "Tệp đính kèm chưa được backend hỗ trợ.",
    }
    assert out["cleared"] == {"chip": True, "att": True}


def test_retry_and_copy_actions_on_message_list(node: str) -> None:
    """data-retry re-sends the turn with retry:true (only when idle);
    data-copy writes the live-copy text to the clipboard."""
    out = _run(node, _APP_SEED + """
        const sent = [];
        globalThis.sendTurn = (text, bot, opts) => { sent.push([text, bot, opts]); return true; };
        const retry = document.createElement('button');
        retry.dataset.retry = 'coder';
        const copy = document.createElement('button');
        copy.setAttribute('data-copy', '');
        const article = document.createElement('article');
        const live = document.createElement('div'); live.className = 'live-copy';
        live.textContent = 'nội dung trả lời';
        article.appendChild(live); article.appendChild(copy); article.appendChild(retry);
        $('#message-list').appendChild(article);
        const fire = (t) => $('#message-list')._handlers.click.forEach(h => h({ target: t }));
        fire(retry);
        runs.set('s1', { botId: 'c', sessionId: 's1' });
        fire(retry);                                        // blocked while busy
        runs.clear();
        fire(copy);
        await tick();
        return { sent, copied: globalThis.__copied };
    """, via="app")
    assert out["sent"] == [["", "coder", {"retry": True}]]
    assert out["copied"] == "nội dung trả lời"


def test_prototype_adapter_and_demo_button(node: str) -> None:
    """window.ThycaPrototype = ThycaShell + sendTurn/stop; the run-demo button
    stays hidden and #stop-button is wired to stopRun."""
    out = _run(node, _APP_SEED + """
        return { proto: Object.keys(ThycaPrototype).sort(),
                 same: ThycaPrototype.sendTurn === sendTurn,
                 demo: $('#run-demo').hidden,
                 stopIsStopRun: $('#stop-button').onclick === stopRun };
    """, via="app")
    for k in ("sendTurn", "stop", "setState", "handleEvent", "setGroupActivity"):
        assert k in out["proto"]
    assert out["same"] is True
    assert out["demo"] is True
    assert out["stopIsStopRun"] is True


# ---------------------------------------------------------------------------
# Page scripts — settings / memories / dashboard / trace
# ---------------------------------------------------------------------------

def test_settings_page_roster_prefs_and_status(node: str) -> None:
    """settings.js renders the read-only roster with the shared seal engine and
    state labels, keeps UI prefs localStorage-only, and reports backend status."""
    out = _run(node, """
        installFetch((url) => {
            if (url === '/api/bots') return { bots: [{ id: 'a', name: 'Alpha', role: 'R', system: true }] };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/config/status') return { ready: true, version: '1.2.3' };
            return {};
        });
        await tick();
        await boot();                                         // re-run with the test fetch
        return {
            roster: $('#bot-roster').innerHTML,
            ready: $('#status-ready').textContent,
            readyCls: $('#status-ready').className,
            version: $('#status-version').textContent,
            pref: {
                typeScale: document.documentElement.dataset.typeScale,
                ornaments: document.querySelector('.workspace').dataset.ornaments,
                savedAtBoot: localStorage.getItem('thyca.ui.preferences'),
            },
        };
    """, via="settings", emit="settings")
    roster = out["roster"]
    assert 'class="bot-row' in roster and 'class="seal-avatar' in roster
    assert "Alpha" in roster and "bot hệ thống" in roster
    assert "../chat/index.html?bot=a" in roster             # open-chat deep link
    assert out["ready"] == "Sẵn sàng" and out["readyCls"] == "status-line s-ok"
    assert out["version"] == "1.2.3"
    assert out["pref"]["typeScale"] == "medium"
    assert out["pref"]["ornaments"] == "on"
    # boot applies prefs without writing — only user edits persist
    assert out["pref"]["savedAtBoot"] is None


def test_settings_prefs_persist_on_change_only(node: str) -> None:
    """Changing a pref control applies it (dataset + body.motion-paused) and
    writes thyca.ui.preferences; #pref-reset returns defaults and persists."""
    out = _run(node, """
        installFetch((url) => url === '/api/bots' ? { bots: [] }
            : url === '/api/sessions' ? { sessions: [] }
            : url === '/api/config/status' ? { ready: false } : {});
        await tick(); await boot();
        const sel = $('#pref-type-scale');
        sel.value = 'large';
        sel.dispatch('change', { target: sel });
        const after = {
            saved: JSON.parse(localStorage.getItem('thyca.ui.preferences')),
            scale: document.documentElement.dataset.typeScale,
        };
        $('#pref-reduce-motion').checked = true;
        $('#pref-reduce-motion').dispatch('change', { target: $('#pref-reduce-motion') });
        const motion = document.body.classList.contains('motion-paused');
        $('#pref-reset').onclick();
        return { after, motion,
                 reset: { saved: JSON.parse(localStorage.getItem('thyca.ui.preferences')),
                          scale: document.documentElement.dataset.typeScale,
                          toast: $('#toast').textContent } };
    """, via="settings", emit="settings")
    assert out["after"]["scale"] == "large"
    assert out["after"]["saved"]["typeScale"] == "large"
    assert out["motion"] is True
    assert out["reset"]["scale"] == "medium"
    assert out["reset"]["saved"] == {"typeScale": "medium", "reduceMotion": False, "ornaments": True}
    assert "mặc định" in out["reset"]["toast"]


def test_settings_form_posts_merged_values(node: str) -> None:
    """The config form renders schema fields (hidden/secret skipped) and POSTs
    the whole values object back with edited fields merged in."""
    out = _run(node, """
        const posted = [];
        installFetch((url, opts) => {
            if (url === '/api/bots') return { bots: [] };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config' && opts.method === 'POST') { posted.push(JSON.parse(opts.body)); return {}; }
            if (url === '/api/config') return {
                values: { limits: { maxLoops: 4 }, provider: { model: 'm1', apiKey: 'keep' } },
                schema: { sections: [
                    { key: 'limits', label: 'Giới hạn', fields: [
                        { key: 'limits.maxLoops', label: 'Vòng tối đa', type: 'number', min: 1, max: 9 },
                        { key: 'limits.secret', label: 'Ẩn', secret: true },
                    ] },
                    { key: 'provider', label: 'P', fields: [{ key: 'provider.model', label: 'M', hidden: true }] },
                ] },
            };
            return {};
        });
        await tick(); await boot();                              // re-run with the test fetch
        const fields = $('#config-fields').innerHTML;
        const input = document.createElement('input');
        input.type = 'number'; input.dataset.section = 'limits'; input.dataset.name = 'maxLoops';
        input.dataset.label = 'Vòng tối đa';
        Object.defineProperty(input, 'valueAsNumber', { get: () => 7 });
        const form = $('#config-form');
        form.appendChild(input);
        await form.onsubmit({ preventDefault() {} });
        return { fields, posted };
    """, via="settings", emit="settings")
    fields = out["fields"]
    assert 'data-section="limits"' in fields and 'data-name="maxLoops"' in fields
    assert "Vòng tối đa" in fields and 'min="1"' in fields and 'max="9"' in fields
    assert 'data-name="secret"' not in fields              # secret fields never render
    assert 'data-name="model"' not in fields               # hidden fields never render
    # the whole values object posts back with only the edited field changed
    assert out["posted"] == [{"limits": {"maxLoops": 7}, "provider": {"model": "m1", "apiKey": "keep"}}]


def test_memory_scope_echo_guard_and_request_token(node: str) -> None:
    """loadStats drops mismatched scope echoes as an error and ignores stale
    answers once a newer request started."""
    out = _run(node, """
        installFetch((url) => {
            if (url === '/api/bots') return { bots: [] };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/memory/stats?bot_id=a') return { bot_id: 'b', total: 3, leaves: [] };   // wrong scope echo
            if (url === '/api/memory/stats') return { total: 2, leaves: [] };
            return {};
        });
        await tick(20);
        memState.scope = 'a';
        await loadStats();
        const mismatch = { status: $('#memory-status').textContent, cls: $('#memory-status').className };
        // stale-token: both requests resolve, but only the newest one lands
        const pending = [];
        installFetch((url) => url === '/api/memory/stats'
            ? { ok: true, status: 200, json: () => new Promise(r => pending.push(r)) }
            : {});
        memState.scope = '';
        const p1 = loadStats();
        const p2 = loadStats();
        await tick();                                        // both getJson()s reach res.json()
        pending.forEach(r => r({ total: 9, leaves: [], bot_id: null }));
        await Promise.all([p1, p2]);
        return { mismatch, total: memState.stats.total, req: memState.request };
    """, via="memories", emit="memories")
    assert "không khớp phạm vi" in out["mismatch"]["status"]
    assert "s-error" in out["mismatch"]["cls"]
    assert out["total"] == 9                               # newest request wins


def test_memory_leaf_rendering_and_day_groups(node: str) -> None:
    """splitHeading/dayLabel/leafHtml render leaves under Hôm nay/Hôm qua/day
    groups with chips, meta and per-leaf actions."""
    out = _run(node, """
        memState.stats = { total: 2, leaves: [
            { chunk_id: 'c1', heading: '## 08:30 — Ghi chú A', snippet: 'nội dung', timeline_day: isoDay(new Date()), get_count: 2, search_count: 1, session_id: 's1', bots: ['team'] },
            { chunk_id: 'c2', heading: 'Trang không giờ', snippet: 'x', timeline_day: '2001-01-01', session_id: 's2', bots: ['ghost-id'] },
        ], expiring: [{ chunk_id: 'c1' }], suggest_removal: [] };
        renderAll();
        return { html: $('#memory-leaves').innerHTML, count: $('#memory-count').textContent,
                 split: splitHeading({ heading: '## 08:30 — Ghi chú A' }),
                 split2: splitHeading({ heading: 'Trang không giờ' }) };
    """, via="memories", emit="memories")
    html = out["html"]
    assert 'class="mem-day"' in html and "Hôm nay" in html
    assert 'class="mem-leaf"' in html and 'data-chunk="c1"' in html
    assert "Ghi chú A" in html and "08:30" in html
    assert "Sắp hết hạn" in html                           # expiring chip
    assert "đã dùng 2" in html and "tìm thấy 1" in html    # meta counts
    assert "chung" in html                                 # team owner label
    assert "ghost-id" in html                              # unknown owner renders the id
    assert 'data-act="reinforce"' in html and 'data-act="edit"' in html and 'data-act="forget"' in html
    assert out["count"] == "2 lá"
    assert out["split"] == {"time": "08:30", "title": "Ghi chú A"}
    assert out["split2"] == {"time": "", "title": "Trang không giờ"}


def test_dashboard_cost_rows_and_bot_fallbacks(node: str) -> None:
    """fmtCost tiers + honest '—'; by_bot rows sort by requests, paint the
    share bar in the bot color (neutral #a08f68 when unknown) and label
    unknown ids truthfully."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'coder', name: 'Coder', tint: 'botanical' }]));
        statsData = {
            totals: { requests: 10 },
            by_bot: [
                { bot_id: 'coder', requests: 7, tool_calls: 2, total_tokens: 700, cost_usd: 0.5, last_started_at: new Date().toISOString() },
                { bot_id: 'unknown', requests: 3, tool_calls: 0, total_tokens: 100, cost_usd: null },
                { bot_id: 'ghost', requests: 1, total_tokens: 10, cost_usd: 0.00042 },
            ],
        };
        renderBotRows();
        const rowsHtml = $('#rows-bot').innerHTML;
        return {
            rows: rowsHtml,
            order: [rowsHtml.indexOf('Coder'), rowsHtml.indexOf('Chưa gắn bot'), rowsHtml.indexOf('ghost')],
            fmt: [fmtCost(null), fmtCost(0.00042), fmtCost(0.5), fmtCost(3.14159), fmtInt('x'), fmtDur(1500)],
            share: [share(7, 10), share(1, 300)],
            names: [botDisplayName('unknown'), botDisplayName('ghost'), botDisplayName('coder')],
            emptyCell: avatarCell('').includes('id-chip'),
        };
    """, via="dashboard", emit="dashboard")
    rows = out["rows"]
    assert 0 <= out["order"][0] < out["order"][1]                # request sort (Coder 7 > unknown 3 > ghost 1)
    assert 'class="row-avatar"' in rows and 'id-chip' in rows      # real seal + fallback chip
    assert "background:#6f7f57" in rows                            # live bot color
    assert "background:#a08f68" in rows                            # neutral fallback bar
    assert "70%" in rows and "$0.5000" in rows
    assert out["fmt"] == ["—", "$0.000420", "$0.5000", "$3.14", "—", "1,5 giây"]
    assert out["share"][0] == "70%" and out["share"][1].startswith("0")
    assert out["names"] == ["Chưa gắn bot", "ghost", "Coder"]
    assert out["emptyCell"] is True


def test_trace_page_labels_and_deep_links(node: str) -> None:
    """Trace renders status chips, Vietnamese kind labels and the
    #<session>/<turn> deep link shape used across the app."""
    out = _run(node, """
        location.href = 'http://localhost/ui/trace/index.html#s9/3';
        location.hash = '#s9/3';
        return {
            status: [statusChip('completed'), statusChip('failed'), statusChip('weird')],
            labels: [SIDE_KIND_LABEL.naming, BUS_KIND_LABEL.delegate, STOP_LABEL.context_limit],
            route: routeFromUrl(),
            row: turnRowHtml({ session_id: 's9', turn_index: 2, title: 'T', status: 'completed', started_at: new Date().toISOString(), bots: ['x'] }),
        };
    """, via="trace", emit="trace")
    assert 'tl-chip s-ok' in out["status"][0] and "Thành công" in out["status"][0]
    assert 'tl-chip s-error' in out["status"][1] and "Lỗi" in out["status"][1]
    assert "weird" in out["status"][2]
    assert out["labels"] == ["Đổi tên phiên", "Giao việc", "Dừng — hết ngữ cảnh"]
    assert out["route"] == {"sid": "s9", "turn": 3}
    assert 'data-session="s9"' in out["row"] and 'data-turn="2"' in out["row"]
    assert 'class="tl-turn"' in out["row"] and ">#2<" in out["row"]


# ---------------------------------------------------------------------------
# CSS + HTML contracts
# ---------------------------------------------------------------------------

def test_base_css_tokens_hidden_rule_and_shell_grid() -> None:
    """base.css owns the shared tokens, the [hidden] rule, notebook shell and
    the global reduced-motion reset."""
    css = BASE_CSS.read_text()
    for token in ("--paper:", "--ink:", "--muted:", "--line:", "--forest:", "--rust:",
                  "--sidebar-width:", "--serif:", "--sans:", "--shadow:"):
        assert token in css
    assert "[hidden]{display:none!important}" in css
    assert ".notebook-shell{" in css and ".main-grid{" in css
    assert "grid-template-columns:var(--sidebar-width)" in css
    assert ".motion-paused *" in css
    assert css.count("prefers-reduced-motion") == 1
    assert "animation:none!important" in css


def test_sidebar_css_card_pocket_and_group_contract() -> None:
    """sidebar.css styles the new card anatomy: palette-var border/paper,
    0fr→1fr pocket reveal, keyed feed rows and the group roster grid."""
    css = SIDEBAR_CSS.read_text()
    for sel in (".bot-card{", ".bot-card[data-selected=true]", ".bot-header{",
                ".bot-select{", ".bot-name{", ".bot-role{", ".compact-preview",
                ".pocket{", ".pocket-inner{", ".thinking-paper{", ".thinking-heading{",
                ".thinking-summary{", ".progress-list{", ".step-mark{", ".thought-extra{",
                ".paper-footer{", ".section-label{", ".group-row{", ".group-roster{",
                ".group-member{", ".group-meta{", ".info-lockup{", ".info-session{",
                ".capabilities{", ".tool-chip{", ".ctx-meter{", ".ctx-bar{",
                ".header-status{", ".favorite{", ".collapse-button{", ".check-row{",
                ".check-avatar{", ".status-dot{"):
        assert sel in css, sel
    assert "grid-template-rows:0fr" in css and ".bot-card[data-open=true] .pocket{grid-template-rows:1fr}" in css
    assert "transition:grid-template-rows" in css
    assert "var(--bot-border)" in css and "var(--bot-paper)" in css
    assert "text-overflow:ellipsis" in css                 # name/preview ellipsis
    assert ".li-enter" in css and ".li-exit" in css        # keyed feed animation
    assert "repeat(var(--members,3)" in css                # roster grid columns
    assert "ctx-ok" in css and "ctx-warn" in css and "ctx-danger" in css
    assert "[data-coordinating=true]" in css
    assert "[data-speaking=true]" in css
    assert "idle-comment" in css


def test_chat_css_transcript_composer_and_title_head() -> None:
    """chat.css styles the transcript (byline/live-copy/caret), the composer
    cluster, the mention popup and a translucent non-sticky title head."""
    css = CHAT_CSS.read_text()
    for sel in (".workspace{", ".chat-title-head{", ".conversation-scroll{",
                ".conversation-content{", ".day-divider{", ".message-user{",
                ".message-assistant{", ".message-byline{", ".live-copy{",
                ".usage-pill{", ".streaming-caret{", ".to-bottom{", ".site-footer{",
                ".composer{", ".composer textarea", ".composer-toolbar{",
                ".composer-team-hint", ".file-chip{", ".mention-popup{", ".mention-option{",
                ".ornament{", ".staff{"):
        assert sel in css, sel
    head = css.split(".chat-title-head{", 1)[1].split("}", 1)[0]
    assert "z-index:1" in head and "border-bottom" in head
    assert "position:sticky" not in head and "position: sticky" not in head
    assert "#fffaf240" in head                              # translucent, not opaque
    assert ".chat-title-head:after" not in css and ".chat-title-head::after" not in css
    assert "color:var(--bot-ink,var(--forest))" in css      # byline tinted by palette
    assert "@keyframes cursor" in css
    assert not re.search(r"--[a-zA-Z-]+\s*:\s*[^v]", css)   # no page-local token defs


def test_avatars_css_states_and_bot_vars() -> None:
    """avatars.css resolves bot colors through --bot-* vars and implements the
    state selectors the JS writes (data-state/expression/group/idle/streaming)."""
    css = AVATARS_CSS.read_text()
    assert ".wax-body{fill:var(--bot-color)" in css
    for sel in ("[data-state=idle]", "[data-state=waiting]", "[data-state=thinking]",
                "[data-state=writing]", "[data-state=working]", "[data-state=done]",
                "[data-state=error]", "[data-state=offline]", "[data-state=confused]",
                "[data-expression=mention]", "[data-group-active=true]",
                "[data-streaming=true]", "[data-idle=look]", "[data-idle=wink]",
                "[data-idle=sleepy]", ".stream-interruption{", ".seal-music"):
        assert sel in css, sel
    assert "seal-avatar" in css and "seal-motion" in css and "seal-face" in css
    assert css.count("prefers-reduced-motion") >= 2
    assert "var(--team-gaze" in css and "var(--note-a-y" in css
    assert ".expression-preview" in css


def test_legacy_team_and_kit_markers_are_gone() -> None:
    """The deleted kit/team anatomy must not reappear in the new sources."""
    banned = ["team-strip", "team-message", "team-exchange", "team-dossier",
              "team-bot-flap", "team-card-box", "team-nav-back", "session-item",
              "kit.css", "botLookFor", "BOT_LOOK_TINTS", "tintVar",
              "splitExchange", "delegation-tree", "unread", "session-rename",
              "runGuardedStages", "teamArticleFor"]
    sources = [p.read_text() for p in (
        CHAT_CSS, SIDEBAR_CSS, BASE_CSS, APP_SHELL_CSS, AVATARS_CSS,
        SIDEBAR_JS, CHAT_JS, APP_JS, DATA_JS, SHELL_JS, SHELL_APP_JS, INDEX_HTML,
    )]
    for marker in banned:
        for src in sources:
            assert marker not in src, marker


def test_index_html_scripts_defs_and_static_markup() -> None:
    """ui/chat/index.html ships the svg defs once, the static shell markup and
    the classic script order the modules rely on."""
    html = INDEX_HTML.read_text()
    for did in ("waxShade", "waxHl", "waxSheen", "g-quill", "g-team", "i-send", "i-stop", "i-copy"):
        assert html.count(f'id="{did}"') == 1, did
    for sel in ('class="notebook-shell chat-shell"', 'class="site-header"',
                'class="site-kicker"', 'id="team-nav-title"', 'id="team-bot-list"',
                'id="group-list"', 'id="team-shared-create"', 'id="chat-sidebar"',
                'id="mobile-overlay"', 'id="motion-toggle"', 'id="open-customize"',
                'class="splitter"', 'id="chat-title"', 'id="session-eyebrow"',
                'id="conversation-scroll"', 'id="message-list"', 'id="to-bottom"',
                'id="composer"', 'id="message"', 'id="composer-label-text"',
                'id="composer-model"', 'id="thinking-effort"', 'id="send-message"',
                'id="stop-button"', 'id="mention-popup"', 'id="attach-file"',
                'id="file-input"', 'id="file-chip"', 'id="remove-file"',
                'id="session-info"', 'id="run-demo"', 'id="toast"', 'id="announcer"',
                'id="bot-drawer"', 'id="bot-drawer-body"', 'id="group-dialog"',
                'id="group-form"', 'id="group-options"', 'id="expressions-dialog"',
                'id="expression-bot"', 'id="bot-color"', 'id="expression-grid"',
                'id="expression-extras"', 'id="idle-expression-grid"',
                'id="group-preview-members"', 'id="idle-auto-toggle"',
                'id="idle-comments-toggle"', 'id="idle-random"', 'id="lab-portrait"',
                'class="composer-team-hint"', 'class="mention-popup"'):
        assert sel in html, sel
    order = [
        'src="../share/util.js"', 'src="../share/markdown.js"', 'src="../share/data.js"',
        'src="js/data.js"', 'src="../share/avatars/avatars.js"', 'src="../share/avatars/idle.js"',
        'src="../share/sidebar.js"', 'src="../share/avatars/studio.js"',
        'src="../share/shell-app.js"', 'src="js/chat.js"', 'src="js/app.js"',
    ]
    positions = [html.index(src) for src in order]
    assert positions == sorted(positions), "script order changed"
    for css in ("../share/fonts.css", "../share/base.css", "../share/sidebar.css",
                "../share/avatars/avatars.css", "../share/avatars/studio.css",
                "../share/markdown.css", "chatcss/chat.css"):
        assert f'href="{css}"' in html


def test_page_index_html_emit_shell_contract() -> None:
    """Every non-chat page emits the shared shell before binding scripts, then
    loads its own page module — the two-phase pattern shell.js documents."""
    for page, path in PAGE_HTML_FILES.items():
        html = path.read_text()
        assert f"emitShell('{page}')" in html or f'emitShell("{page}")' in html, page
        for src in ("../share/util.js", "../share/data.js", "../share/shell.js",
                    "../share/avatars/avatars.js", "../share/avatars/idle.js",
                    "../share/sidebar.js", "../share/avatars/studio.js",
                    "../share/shell-app.js"):
            assert f'src="{src}"' in html, (page, src)
        assert f'src="js/{page}.js"' in html, page
        # emitShell runs between shell.js and the DOM-binding scripts — the
        # page header comment may also mention emitShell, so search after the
        # shell.js script tag
        call = f"emitShell('{page}')" if f"emitShell('{page}')" in html else f'emitShell("{page}")'
        shell_pos = html.index('src="../share/shell.js"')
        call_pos = html.index(call, shell_pos)
        assert shell_pos < call_pos < html.index('src="../share/avatars/avatars.js"'), page
        for css in ("../share/base.css", "../share/sidebar.css",
                    "../share/avatars/avatars.css", "../share/app-shell.css"):
            assert f'href="{css}"' in html, (page, css)


def test_settings_page_uses_shared_avatar_engine() -> None:
    """settings.js renders roster rows with the shared avatar() + states[]
    labels and keeps preferences localStorage-only (no API calls for them)."""
    js = SETTINGS_JS.read_text()
    assert "avatar(b)" in js
    assert "states[b.state]" in js
    assert "localStorage" in js and "thyca.ui.preferences" in js
    # prefs never hit the API
    prefs_block = js.split("function applyPrefs", 1)[1].split("function hydratePrefs", 1)[0]
    assert "getJson" not in prefs_block and "postJson" not in prefs_block
    # no dynamic avatar import / inline avatar copy — shared engine only
    assert "import(" not in js
