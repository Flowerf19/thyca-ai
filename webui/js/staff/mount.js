import { renderStaff } from "./draw.js";
import { scoreFromEvents } from "./map.js";
import { playPitches, playScore, stopPlayback } from "./play.js";

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
  const live = article.classList.contains("entry-status");
  const sig = `${scoreSig(score)}:${widthPx}:${live ? "1" : "0"}`;
  if (host.dataset.sig === sig) return;
  host.dataset.sig = sig;
  host.replaceChildren(renderStaff(score, { widthPx, maxSystems: live ? 1 : 0 }));
  const svg = host.querySelector(".thyca-staff");
  const notes = svg && svg.querySelectorAll(".staff-event[data-pitches]");
  const last = notes && notes.length ? notes[notes.length - 1] : null;
  if (last && last.classList && typeof last.classList.add === "function") {
    last.classList.add("is-ink");
  }
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

let playRaf = 0;

function reducedMotion() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function clearReveal(svg) {
  if (playRaf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(playRaf);
  playRaf = 0;
  if (!svg || !svg.querySelectorAll) return;
  svg.classList.remove("is-playback");
  for (const el of svg.querySelectorAll(".staff-event")) {
    el.classList.remove("is-revealed", "is-playing");
  }
}

function startReveal(svg, timeline) {
  clearReveal(svg);
  if (!svg || !timeline.length) return;
  svg.classList.add("is-playback");
  if (reducedMotion()) {
    for (const el of svg.querySelectorAll(".staff-event[data-pitches]")) {
      el.classList.add("is-revealed");
    }
    return;
  }
  if (typeof requestAnimationFrame !== "function") return;
  const end = Math.max(...timeline.map((item) => item.atSec + item.durSec), 0);
  const t0 = typeof performance !== "undefined" ? performance.now() : 0;
  const step = () => {
    const now = typeof performance !== "undefined" ? (performance.now() - t0) / 1000 : 0;
    for (const item of timeline) {
      const el = svg.querySelector(
        `.staff-event[data-measure="${item.measure}"][data-offset="${item.offset}"]`,
      );
      if (!el) continue;
      if (now + 0.03 >= item.atSec) el.classList.add("is-revealed");
      el.classList.toggle("is-playing", now >= item.atSec && now < item.atSec + item.durSec);
    }
    if (now < end) playRaf = requestAnimationFrame(step);
    else {
      playRaf = 0;
      svg.classList.remove("is-playback");
      for (const el of svg.querySelectorAll(".is-playing")) el.classList.remove("is-playing");
    }
  };
  playRaf = requestAnimationFrame(step);
}

function scoreForHost(host) {
  const article = host && host.parentNode;
  let key = null;
  try {
    key = article?.dataset?.staffKey || NODE_KEYS.get(article) || null;
  } catch { key = null; }
  if (!key && article?.classList?.contains("entry-status")) key = lastKey;
  const rec = key ? RECORDS.get(key) : null;
  return rec?.score || null;
}

function onStaffPointer(ev) {
  const host = ev.currentTarget;
  const target = ev.target;
  const eventNode = target && typeof target.closest === "function"
    ? target.closest(".staff-event")
    : null;
  if (eventNode && eventNode.getAttribute("data-pitches")) {
    if (typeof ev.stopPropagation === "function") ev.stopPropagation();
    const raw = eventNode.getAttribute("data-sound") || eventNode.getAttribute("data-pitches");
    const duration = Number(eventNode.getAttribute("data-duration")) || 4;
    const score = scoreForHost(host);
    stopPlayback();
    const svg = host.querySelector && host.querySelector(".thyca-staff");
    clearReveal(svg);
    playPitches(raw.split(","), duration, { bpm: score && score.bpm });
    eventNode.classList.add("is-playing", "is-revealed");
    return;
  }
  const score = scoreForHost(host);
  if (!score) return;
  const svg = host.querySelector && host.querySelector(".thyca-staff");
  playScore(score).then((result) => {
    if (!result || !result.ok) return;
    startReveal(svg, result.timeline);
  });
}

function ensureHost(article) {
  let host = article.querySelector(":scope > .thyca-staff-host");
  if (host) return host;
  host = document.createElement("div");
  host.className = "thyca-staff-host";
  host.setAttribute("aria-hidden", "true");
  if (typeof host.addEventListener === "function") {
    host.addEventListener("pointerdown", onStaffPointer);
  }
  const copy = article.querySelector(":scope > .entry-copy");
  if (copy) article.insertBefore(host, copy);
  else article.append(host);
  return host;
}
