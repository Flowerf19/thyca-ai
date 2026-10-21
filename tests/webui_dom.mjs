// Shared Node harness for the rebuilt WebUI (thyca/webui/ui/**).
//
// The new UI ships classic scripts ('use strict', shared globals — no ES
// modules), so tests can't import them. This harness evals the real script
// chain in one Function scope against a minimal DOM parsed from the real
// ui/chat/index.html markup: real element ids/classes from production markup,
// no per-selector vivification.
//
//   import { installDom, loadScripts, TextNode, matchSimple, qsa } from './tests/webui_dom.mjs';
//   installDom('thyca/webui/ui/chat/index.html');
//   const ui = loadScripts([...files], 'return { sendTurn, handleStreamEvent };');
//
// Globals the scripts need are installed here (document, window, matchMedia,
// localStorage, closeMobile…). fetch stays per-test.

import fs from 'node:fs';

/* ---------- text ---------- */
const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" };
const decodeEntities = s => String(s).replace(/&(?:amp|lt|gt|quot|#39);/g, m => ENTITIES[m]);
const escapeText = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const VOID_HTML = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr',
]);

/* ---------- selector engine (type/.class/#id/[attr]/[attr="v"]/:not(...) + descendants) ---------- */
function matchSimple(el, sel) {
  if (!el || el.tag === '#text') return false;
  const nots = [];
  sel = sel.trim().replace(/:not\(([^)]*)\)/g, (_, inner) => { nots.push(inner.trim()); return ''; });
  if (!sel) return !nots.some(s => matchSimple(el, s));
  const re = /\[([^\]]+)\]|([#.][\w-]+)|([\w-]+)/g;
  let m, matched = false;
  while ((m = re.exec(sel))) {
    matched = true;
    if (m[1] !== undefined) {
      const am = m[1].match(/^([\w-]+)\s*(?:([~|^$*]?=)\s*"?([^"\]]*)"?\s*)?$/);
      if (!am) return false;
      const actual = el.attrs[am[1]];
      if (actual === undefined) return false;
      if (am[2] !== undefined && String(actual) !== am[3]) return false;
    } else if (m[2]) {
      if (m[2][0] === '.') { if (!el.classList.contains(m[2].slice(1))) return false; }
      else if (el.attrs.id !== m[2].slice(1)) return false;
    } else if (m[3]) {
      if (el.tag !== m[3].toLowerCase()) return false;
    }
  }
  return matched && !nots.some(s => matchSimple(el, s));
}

function matchChain(el, parts) {
  if (!matchSimple(el, parts[parts.length - 1])) return false;
  let idx = parts.length - 2, node = el.parentNode;
  while (idx >= 0 && node) {
    if (matchSimple(node, parts[idx])) idx--;
    node = node.parentNode;
  }
  return idx < 0;
}

export function qsa(root, sel) {
  const parts = sel.trim().split(/\s+/);
  const out = [];
  (function walk(node) {
    for (const child of node.children) {
      if (child.tag === '#text') continue;
      if (matchChain(child, parts)) out.push(child);
      walk(child);
    }
  })(root);
  return out;
}
const qs = (root, sel) => qsa(root, sel)[0] ?? null;

/* ---------- nodes ---------- */
export class TextNode {
  constructor(text) { this.tag = '#text'; this.text = String(text); this.parentNode = null; }
  get textContent() { return this.text; }
  set textContent(v) { this.text = String(v); }
  serialize() { return escapeText(this.text); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); this.parentNode = null; }
}

export class El {
  constructor(tag = 'div') {
    this.tag = String(tag).toLowerCase();
    this.children = [];
    this.parentNode = null;
    this.attrs = {};
    this._class = '';
    this._text = '';
    this.hidden = false;
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.inert = false;
    this.open = false;
    this.tabIndex = 0;
    this.title = '';
    this.selected = false;
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
    const self = this;
    this.dataset = new Proxy({}, {
      set(t, k, v) {
        t[k] = v;
        self.attrs['data-' + String(k).replace(/[A-Z]/g, c => '-' + c.toLowerCase())] = String(v);
        return true;
      },
      get: (t, k) => t[k],
      deleteProperty(t, k) { delete t[k]; return true; },
    });
    this.style = { setProperty() {}, removeProperty() {}, getPropertyValue: () => '' };
    this.classList = {
      contains: n => self.className.split(/\s+/).filter(Boolean).includes(n),
      add: (...ns) => {
        self.className = [...new Set([...self.className.split(/\s+/).filter(Boolean), ...ns])].join(' ');
      },
      remove: (...ns) => {
        self.className = self.className.split(/\s+/).filter(x => x && !ns.includes(x)).join(' ');
      },
      toggle: (n, force) => {
        const has = self.classList.contains(n);
        const want = force ?? !has;
        if (want && !has) self.classList.add(n);
        if (!want && has) self.classList.remove(n);
        return want;
      },
    };
  }
  get className() { return this._class; }
  set className(v) { this._class = String(v ?? ''); this.attrs.class = this._class; }
  get id() { return this.attrs.id ?? ''; }
  set id(v) { this.attrs.id = String(v); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this.children = []; this._text = String(v); }
  get innerHTML() {
    return escapeText(this._text) + this.children.map(c => c.serialize()).join('');
  }
  set innerHTML(html) {
    const frag = parseHTML(String(html));
    this.children = frag.children;
    this._text = frag._text;
    this.children.forEach(c => { c.parentNode = this; });
  }
  serialize() {
    if (this.tag === '#text') return escapeText(this.text);
    const attrs = Object.entries(this.attrs)
      .map(([k, v]) => (v === '' ? ` ${k}` : ` ${k}="${v}"`)).join('');
    if (VOID_HTML.has(this.tag)) return `<${this.tag}${attrs}>`;
    return `<${this.tag}${attrs}>${escapeText(this._text)}${this.children.map(c => c.serialize()).join('')}</${this.tag}>`;
  }
  /* tree ops */
  append(...nodes) { for (const n of nodes) { if (n == null) continue; n.remove?.(); n.parentNode = this; this.children.push(n); } }
  appendChild(n) { this.append(n); return n; }
  prepend(...nodes) { for (const n of [...nodes].reverse()) { if (n == null) continue; n.remove?.(); n.parentNode = this; this.children.unshift(n); } }
  insertBefore(n, ref) {
    if (n == null) return n;
    n.remove?.(); n.parentNode = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i >= 0) this.children.splice(i, 0, n); else this.children.push(n);
    return n;
  }
  after(...nodes) {
    const p = this.parentNode; if (!p) return;
    const i = p.children.indexOf(this);
    for (const n of [...nodes].reverse()) { n.remove?.(); n.parentNode = p; p.children.splice(i + 1, 0, n); }
  }
  replaceChildren(...nodes) {
    for (const c of [...this.children]) c.parentNode = null;
    this.children = []; this._text = '';
    this.append(...nodes);
  }
  remove() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this);
    this.parentNode = null;
  }
  insertAdjacentHTML(pos, html) {
    const frag = parseHTML(String(html));
    if (pos === 'beforeend') this.append(...frag.children);
    else if (pos === 'afterbegin') this.prepend(...frag.children);
    else if (pos === 'beforebegin') this.after(...frag.children);
    else if (pos === 'afterend') { const p = this.parentNode; if (p) { const i = p.children.indexOf(this); frag.children.forEach(c => { c.parentNode = p; }); p.children.splice(i + 1, 0, ...frag.children); } }
  }
  /* queries */
  querySelector(sel) { return qs(this, sel); }
  querySelectorAll(sel) { return qsa(this, sel); }
  closest(sel) {
    const parts = sel.trim().split(/\s+/);
    let n = this;
    while (n && n.tag !== '#document') { if (matchChain(n, parts)) return n; n = n.parentNode; }
    return null;
  }
  matches(sel) { return matchChain(this, sel.trim().split(/\s+/)); }
  contains(node) { let n = node; while (n) { if (n === this) return true; n = n.parentNode; } return false; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n.tag === '#document' || n.root === true; }
  get firstElementChild() { return this.children.find(c => c.tag !== '#text') ?? null; }
  get lastElementChild() { const els = this.children.filter(c => c.tag !== '#text'); return els[els.length - 1] ?? null; }
  get nextElementSibling() {
    if (!this.parentNode) return null;
    const els = this.parentNode.children.filter(c => c.tag !== '#text');
    return els[els.indexOf(this) + 1] ?? null;
  }
  get options() { return this.children.filter(c => c.tag === 'option'); }
  /* attributes */
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k === 'class') this._class = String(v);
    else if (k === 'hidden') this.hidden = true;
    else if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(v);
  }
  getAttribute(k) { return this.attrs[k] ?? null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  /* events/focus/measure — handlers never fire in tests */
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() { return true; }
  focus() {}
  blur() {}
  click() {}
  requestSubmit() {}
  scrollIntoView() {}
  scrollTo() {}
  scrollIntoViewIfNeeded() {}
  getBoundingClientRect() { return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }; }
  setPointerCapture() {}
  releasePointerCapture() {}
  getAnimations() { return []; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

/* ---------- mini HTML parser (machine-generated markup: quoted attrs, no '>' in values) ---------- */
export function parseHTML(html) {
  const root = new El('#root');
  const stack = [root];
  let i = 0;
  const n = html.length;
  while (i < n) {
    const lt = html.indexOf('<', i);
    if (lt < 0) { if (html.slice(i).trim()) stack.at(-1).append(new TextNode(decodeEntities(html.slice(i)))); break; }
    if (lt > i && html.slice(i, lt).trim()) stack.at(-1).append(new TextNode(decodeEntities(html.slice(i, lt))));
    if (html.startsWith('<!--', lt)) {
      const end = html.indexOf('-->', lt + 4);
      i = end < 0 ? n : end + 3;
      continue;
    }
    // scan to '>' outside quotes
    let j = lt + 1, q = null;
    while (j < n) {
      const c = html[j];
      if (q) { if (c === q) q = null; }
      else if (c === '"' || c === "'") q = c;
      else if (c === '>') break;
      j++;
    }
    const raw = html.slice(lt + 1, j);
    i = j + 1;
    if (!raw || raw[0] === '!' || raw[0] === '?') continue;      // doctype, decl
    if (raw[0] === '/') {
      const name = raw.slice(1).trim().split(/\s/)[0].toLowerCase();
      for (let k = stack.length - 1; k > 0; k--) {
        if (stack[k].tag === name) { stack.length = k; break; }
      }
      continue;
    }
    const selfClose = raw.endsWith('/');
    const body = selfClose ? raw.slice(0, -1) : raw;
    const tm = body.match(/^([a-zA-Z][\w:-]*)/);
    if (!tm) { stack.at(-1).append(new TextNode('<' + raw + '>')); continue; }
    const el = new El(tm[1]);
    const attrRe = /([\w:-]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?/g;
    attrRe.lastIndex = tm[0].length;
    let am;
    while ((am = attrRe.exec(body))) {
      let v = am[2] ?? '';
      if (v.length >= 2 && ((v[0] === '"' && v.at(-1) === '"') || (v[0] === "'" && v.at(-1) === "'"))) v = v.slice(1, -1);
      el.setAttribute(am[1], decodeEntities(v));
    }
    stack.at(-1).append(el);
    if (!selfClose && !VOID_HTML.has(el.tag)) stack.push(el);
  }
  return root;
}

/* ---------- install: document from real page markup + browser globals ---------- */
export function installDom(pagePath = 'thyca/webui/ui/chat/index.html') {
  const doc = new El('#document');
  doc.root = true;
  const tree = parseHTML(fs.readFileSync(pagePath, 'utf8'));
  doc.children = tree.children;
  doc.children.forEach(c => { c.parentNode = doc; });
  doc.createElement = tag => new El(tag);
  doc.createElementNS = (_ns, tag) => new El(tag);
  doc.createTextNode = text => new TextNode(text);
  doc.documentElement = qs(doc, 'html') ?? new El('html');
  doc.body = qs(doc, 'body') ?? new El('body');
  doc.hidden = false;
  doc.addEventListener = () => {};
  doc.removeEventListener = () => {};
  doc.title = qs(doc, 'title')?.textContent ?? '';
  globalThis.document = doc;
  globalThis.window = globalThis;
  globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.location = { href: '', search: '', hash: '' };
  globalThis.innerWidth = 1280;
  globalThis.addEventListener = () => {};
  // shell-app.js is not loaded in tests; these are its page-level hooks.
  globalThis.closeMobile = () => {};
  globalThis.openMobile = () => {};
  globalThis.requestAnimationFrame = cb => cb();
  // Keep the process alive only for awaited work: a turn's 1s ticker or a
  // pending feed/settle timer must not hold the node process open.
  const _setTimeout = globalThis.setTimeout, _setInterval = globalThis.setInterval;
  globalThis.__realSetTimeout = _setTimeout;
  globalThis.setTimeout = (fn, ms, ...a) => { const t = _setTimeout(fn, ms, ...a); t.unref?.(); return t; };
  globalThis.setInterval = (fn, ms, ...a) => { const t = _setInterval(fn, ms, ...a); t.unref?.(); return t; };
  return doc;
}

/* Real-time sleep that keeps the event loop referenced (unlike the wrapped
   setTimeout above, which is unref'd so scripts can exit cleanly). */
export const sleep = ms => new Promise(resolve => globalThis.__realSetTimeout(resolve, ms));

/* Eval classic scripts in one shared scope; `tail` is appended raw — use it
   to return the surface the test needs:
   loadScripts(files, 'return { sendTurn, handleStreamEvent, runs };') */
export function loadScripts(files, tail = 'return {};', { strip = [] } = {}) {
  let src = files.map(f => fs.readFileSync(f, 'utf8')).join('\n');
  for (const pat of strip) src = src.replace(pat, '');
  return new Function(`${src}\n${tail}`)();
}

/* The chat page's classic-script chain, in index.html order (markdown.js is
   an ES module — contentHtml() falls back to escaped paragraphs without it;
   shell-app.js/app.js bind boot-time side effects tests stub themselves). */
export const CHAT_CHAIN = [
  'thyca/webui/ui/share/util.js',
  'thyca/webui/ui/share/data.js',
  'thyca/webui/ui/chat/js/data.js',
  'thyca/webui/ui/share/avatars/avatars.js',
  'thyca/webui/ui/share/avatars/idle.js',
  'thyca/webui/ui/share/sidebar.js',
  'thyca/webui/ui/share/avatars/studio.js',
  'thyca/webui/ui/chat/js/chat.js',
];
