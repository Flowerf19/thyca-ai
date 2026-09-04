import { renderStaff } from "./staff-draw.js";
import { scoreFromEvents } from "./staff-map.js";

// RECORDS keyed by stable string — NOT by DOM node — so innerHTML
// replacement (renderPage / fillChatAt tab switch) doesn't lose live score.
// Key priority: opts.key > opts.sessionId(+index) > article.dataset.staffKey
// > auto per-DOM id (fallback for callers without a key, e.g. trace/tests).
const RECORDS = new Map();
const NODE_KEYS = new WeakMap();
const watched = new WeakSet();
let observer = null;
let autoId = 0;
let lastKey = null;

function normalizeScore(score) {
  if (Array.isArray(score) || score == null) {
    return scoreFromEvents(Array.isArray(score) ? score : []);
  }
  return score;
}

function keyFor(article, opts = {}) {
  if (opts.key) return String(opts.key);
  if (opts.sessionId) {
    const index = opts.index ?? "live";
    return `session:${String(opts.sessionId)}:${String(index)}`;
  }
  try {
    if (article?.dataset?.staffKey) return article.dataset.staffKey;
  } catch { /* dataset blocked, fall through */ }
  let key = null;
  try {
    key = NODE_KEYS.get(article) || null;
  } catch { key = null; }
  if (!key) {
    key = `auto:${++autoId}`;
    try {
      NODE_KEYS.set(article, key);
    } catch { /* ignore */ }
  }
  return key;
}

export function mountStaff(article, score, opts = {}) {
  if (!article || !article.classList.contains("entry-thyca")) return null;
  const key = keyFor(article, opts);
  const normalized = normalizeScore(score);
  RECORDS.set(key, { score: normalized });
  lastKey = key;
  try {
    if (article.dataset) article.dataset.staffKey = key;
  } catch { /* ignore */ }
  try {
    NODE_KEYS.set(article, key);
  } catch { /* ignore */ }
  paint(article, key);
  return key;
}

export function getStaff(key) {
  if (!key) return null;
  return RECORDS.get(String(key)) || null;
}

export function lastStaffKey() {
  return lastKey;
}

export function dropStaff(key) {
  if (!key) return;
  RECORDS.delete(String(key));
  if (lastKey === String(key)) lastKey = null;
}

export function unmountStaff(host) {
  if (!host) return;
  if (observer && watched.has(host)) {
    observer.unobserve(host);
    watched.delete(host);
  }
}

export function clearStaffs(root) {
  if (!root) return;
  // Intentionally KEEP RECORDS: clearing hosts for innerHTML rebuild must
  // not drop the live score — syncStaffs repaints from the Map afterwards.
  // Call dropStaff(key) explicitly when a turn settles for good.
  for (const host of root.querySelectorAll(".thyca-staff-host")) {
    unmountStaff(host);
    host.remove();
  }
}

export function syncStaffs(root, opts = {}) {
  if (!root) return;
  const forcedKey = opts.key
    ? String(opts.key)
    : opts.sessionId
      ? `session:${String(opts.sessionId)}:${String(opts.index ?? "live")}`
      : null;
  for (const article of root.querySelectorAll(".entry-thyca")) {
    const isStatus = article.classList.contains("entry-status");
    let key = null;
    try {
      key = article.dataset?.staffKey || NODE_KEYS.get(article) || null;
    } catch { key = null; }
    if (!key && isStatus) key = forcedKey || lastKey;
    const rec = key ? RECORDS.get(key) : null;
    if (!isStatus && !(rec && rec.score?.measures?.length)) {
      const stray = article.querySelector(":scope > .thyca-staff-host");
      if (stray) {
        unmountStaff(stray);
        stray.remove();
      }
      continue;
    }
    // Status with no record yet still gets an empty staff (old behavior).
    if (key) {
      try {
        if (article.dataset) article.dataset.staffKey = key;
      } catch { /* ignore */ }
      try {
        NODE_KEYS.set(article, key);
      } catch { /* ignore */ }
    }
    paint(article, key);
  }
}

function paint(article, explicitKey = null) {
  const host = ensureHost(article);
  watch(host);
  let key = explicitKey;
  if (!key) {
    try {
      key = article.dataset?.staffKey || NODE_KEYS.get(article) || null;
    } catch { key = null; }
  }
  if (!key && article.classList.contains("entry-status")) key = lastKey;
  const rec = key ? RECORDS.get(key) : null;
  const score = rec?.score || scoreFromEvents([]);
  const widthPx = Math.round(host.getBoundingClientRect().width) || 480;
  const sig = `${scoreSig(score)}:${widthPx}`;
  if (host.dataset.sig === sig) return;
  host.dataset.sig = sig;
  host.replaceChildren(renderStaff(score, { widthPx }));
}

function scoreSig(score) {
  const measures = score?.measures || [];
  return measures
    .map((measure) => {
      const events = (measure.events || [])
        .map((item) => `${item.offset}/${item.duration}/${(item.pitches || []).join(".")}`)
        .join(",");
      const rests = (measure.rests || [])
        .map((item) => `${item.offset}/${item.duration}`)
        .join(",");
      return `${measure.harmony || ""}:${measure.terminal || ""}:${events}:${rests}`;
    })
    .join(";");
}

function watch(host) {
  if (typeof ResizeObserver !== "function") return;
  if (!observer) {
    observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const article = entry.target.parentNode;
        if (article?.classList?.contains("entry-thyca")) paint(article);
      }
    });
  }
  if (watched.has(host)) return;
  observer.observe(host);
  watched.add(host);
}

function ensureHost(article) {
  let host = article.querySelector(":scope > .thyca-staff-host");
  if (host) return host;
  host = document.createElement("div");
  host.className = "thyca-staff-host";
  host.setAttribute("aria-hidden", "true");
  const copy = article.querySelector(":scope > .entry-copy");
  if (copy) article.insertBefore(host, copy);
  else article.append(host);
  return host;
}
