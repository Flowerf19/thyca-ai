import { modes } from "../shared/data.js";
import { state } from "../shared/state.js";
import { el } from "../shared/dom.js";
import { collapseNames } from "./status.js";
import { ambientLineForEvent } from "./ambient.js";
import { entryHtml, statusHtml } from "./view.js";

// Per-session in-flight turn. Survives renderPage / fillChatAt innerHTML
// replacement: restoreLiveTurn remounts .entry-status from this Map.
const liveTurns = new Map();

export function liveKey(sessionId) {
  return String(sessionId || "");
}

function makeLive(text) {
  return {
    statusText: "Đang chờ Thyca…",
    ambientText: ambientLineForEvent(null),
    running: true,
    dirty: false,
    failed: false,
    waiting: false,
    text: text || "",
    activeOps: new Map(),
    batchNames: [],
    seenTools: [],
    lastOperationalText: "",
  };
}

export function paintLiveTools(rec) {
  if (!el.toolMeter) return;
  const tools = collapseNames(rec && rec.seenTools);
  el.toolMeter.textContent = tools;
  el.toolMeter.hidden = !tools;
  if (tools) el.toolMeter.setAttribute("title", tools);
  else el.toolMeter.removeAttribute("title");
}

export function startLiveTurn(sessionId, text) {
  const rec = makeLive(text);
  liveTurns.set(liveKey(sessionId), rec);
  return rec;
}

export function ensureLive(sessionId) {
  const key = liveKey(sessionId);
  let rec = liveTurns.get(key);
  if (!rec) {
    rec = makeLive("");
    liveTurns.set(key, rec);
  }
  return rec;
}

export function getLiveTurn(sessionId) {
  if (sessionId == null) return null;
  return liveTurns.get(liveKey(sessionId)) || null;
}

export function isViewingSession(sessionId) {
  if (state.activeMode !== "chat") return false;
  const page = modes.chat.pages[state.activePageIndex];
  if (!page) return false;
  if (!sessionId) return !page.sessionId;
  if (!page.sessionId) return true;
  return String(page.sessionId) === String(sessionId);
}

export function rekeyLiveTurn(fromId, toId) {
  const from = liveKey(fromId);
  const to = liveKey(toId);
  if (from === to) return;
  const rec = liveTurns.get(from);
  if (rec) {
    liveTurns.delete(from);
    liveTurns.set(to, rec);
  }
}

function ensureEntryList(root) {
  let list = root.querySelector(".entry-list");
  if (list) return list;
  root.innerHTML = '<div class="entry-list"></div>';
  return root.querySelector(".entry-list");
}

function ensureOutgoingUser(list, text) {
  if (!text) return;
  const nodes = [...list.children].filter((node) => !node.classList.contains("entry-status"));
  const last = nodes.at(-1);
  if (last && last.classList.contains("entry-user")) return;
  list.insertAdjacentHTML("beforeend", entryHtml("user", text));
}

function paintStatusCopy(status, rec) {
  const ambient = status.querySelector(".status-ambient");
  if (ambient) ambient.textContent = rec.ambientText;
  status.classList.toggle("is-error", rec.failed);
  status.classList.toggle("is-waiting", rec.waiting);
}

export function restoreLiveTurn(root, page) {
  if (!root || !page) return false;
  const rec = getLiveTurn(page.sessionId);
  if (!rec) return false;
  if (!(rec.running || rec.failed)) {
    rec.dirty = false;
    return false;
  }
  const list = ensureEntryList(root);
  ensureOutgoingUser(list, rec.text);
  let status = list.querySelector(".entry-status");
  if (!status) {
    list.insertAdjacentHTML("beforeend", statusHtml(rec.ambientText));
    status = list.querySelector(".entry-status");
  } else {
    paintStatusCopy(status, rec);
  }
  if (!status) return false;
  status.classList.toggle("is-error", rec.failed);
  status.classList.toggle("is-waiting", rec.waiting);
  paintLiveTools(rec);
  return true;
}

export function discardRunningLiveTurns() {
  for (const [key, rec] of [...liveTurns.entries()]) {
    if (!rec.running) continue;
    liveTurns.delete(key);
  }
}

export function chatStatusNode(sessionId) {
  if (state.activeMode !== "chat") return null;
  if (sessionId && !isViewingSession(sessionId)) return null;
  const status = el.pageBody.querySelector(".entry-status");
  return status && status.isConnected ? status : null;
}
