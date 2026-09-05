import { el } from "../shared/dom.js";
import { modes } from "../shared/data.js";
import { postJson } from "../shared/util.js";
import { createNdjsonDecoder } from "../shared/ndjson.js";
import { batchDoneText, collapseNames, statusTextForEvent } from "./status.js";
import { state } from "../shared/state.js";
import { ambientLineForEvent } from "./ambient.js";
import {
  chatStatusNode,
  ensureLive,
  getLiveTurn,
  isViewingSession,
  rekeyLiveTurn,
  startLiveTurn,
} from "./live.js";
import { applyDetail, bindSession } from "./pages.js";
import { renderComposerMeter } from "./meter.js";
import {
  entryHtml,
  reduceMotion,
  scrollThread,
  slideStatus,
  statusHtml,
} from "./view.js";

const STATUS_MIN_DWELL_MS = 500;
const AMBIENT_DEBOUNCE_MS = 520;
let pendingStatus = null;
let lastStatusSwapAt = 0;
let pendingAmbient = null;
let lastAmbientSwapAt = 0;

export function beginOutgoingTurn(text) {
  removeStatus();
  flushPendingStatus();
  flushPendingAmbient();
  lastStatusSwapAt = 0;
  lastAmbientSwapAt = performance.now();
  const page = modes.chat.pages[state.activePageIndex];
  const sessionId = (page && page.sessionId) || state.activeSessionId || "";
  const rec = startLiveTurn(sessionId, text);
  let list = el.pageBody.querySelector(".entry-list");
  if (!list) {
    el.pageBody.innerHTML = '<div class="entry-list"></div>';
    list = el.pageBody.querySelector(".entry-list");
  }
  list.insertAdjacentHTML("beforeend", entryHtml("user", text));
  list.lastElementChild.classList.add("is-enter");
  list.insertAdjacentHTML("beforeend", statusHtml(rec.statusText, rec.ambientText));
  scrollThread();
}

export function removeStatus() {
  const node = el.pageBody.querySelector(".entry-status");
  if (!node) return;
  node.remove();
}

export function settleIncoming() {
  const page = modes.chat.pages[state.activePageIndex];
  const sessionId = page?.sessionId || "";
  const rec = getLiveTurn(sessionId);
  const liveList = el.pageBody.querySelector(".entry-list");
  const wrap = document.createElement("div");
  wrap.innerHTML = page?.body || "";
  const fresh = wrap.querySelector(".entry-list");
  if (!fresh || !liveList) return false;
  const kept = [...liveList.children].filter((node) => !node.classList.contains("entry-status")).length;
  liveList.replaceWith(fresh);
  [...fresh.children].slice(kept).forEach((node, index) => {
    node.classList.add("is-enter");
    node.style.animationDelay = `${index * 80}ms`;
  });
  if (rec) {
    rec.running = false;
    rec.dirty = false;
    rec.failed = false;
  }
  const heading = el.pageHeader.querySelector("h1");
  if (heading && page.title) heading.innerHTML = page.title;
  const kicker = el.pageHeader.querySelector(".page-kicker");
  if (kicker && page.kicker) kicker.textContent = page.kicker;
  scrollThread();
  return true;
}

export async function sendChatTurn(text) {
  const page = modes.chat.pages[state.activePageIndex];
  let sessionId = page ? page.sessionId : state.activeSessionId;
  const pendingId = sessionId || "";
  if (!sessionId) {
    const created = await postJson("/api/sessions", {});
    if (!created || !created.id) throw new Error("Không tạo được phiên.");
    sessionId = created.id;
    if (page) {
      page.sessionId = sessionId;
      bindSession(page, sessionId);
    }
    rekeyLiveTurn(pendingId, sessionId);
  }
  const response = await fetch(`/api/sessions/${sessionId}/turn/stream`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const message = payload && payload.error ? String(payload.error) : "Không gửi được.";
    throw new Error(message);
  }
  if (!response.body) throw new Error("Không nhận được trả lời.");
  const decoder = createNdjsonDecoder();
  const reader = response.body.getReader();
  let completed = null;
  let failed = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const event of decoder.push(value)) {
      const terminal = ingestTurnEvent(sessionId, event);
      if (terminal === "completed") completed = event.detail;
      else if (terminal === "failed") failed = event;
    }
  }
  for (const event of decoder.flush()) {
    const terminal = ingestTurnEvent(sessionId, event);
    if (terminal === "completed") completed = event.detail;
    else if (terminal === "failed") failed = event;
  }
  if (failed) {
    const message = failed.message ? String(failed.message) : "Lượt đã dừng.";
    throw new Error(message);
  }
  if (!completed) throw new Error("Không nhận được trả lời.");
  applyDetail(completed);
  // Meter lên ngay khi turn xong — renderPage không chạy lại khi settle
  // thành công (chỉ renderPageList), nên update ở đây; tab nền thì bỏ qua.
  if (isViewingSession(completed.id)) renderComposerMeter(el.meter, completed.messages);
  return completed;
}

function ingestTurnEvent(sessionId, event) {
  const rec = ensureLive(sessionId);
  rec.waiting = event.type === "llm.started" || event.type === "llm.retry";
  if (event.type === "turn.failed") rec.failed = true;
  if (event.type === "turn.completed" || event.type === "turn.failed") {
    rec.running = false;
    rec.dirty = true;
  }
  applyAmbient(sessionId, event);
  applyStatus(sessionId, event);
  const status = chatStatusNode(sessionId);
  if (status) {
    if (rec.failed) status.classList.add("is-error");
    status.classList.toggle("is-waiting", rec.waiting);
  }
  if (event.type === "turn.completed") return "completed";
  if (event.type === "turn.failed") return "failed";
  return null;
}

function applyStatus(sessionId, event) {
  const rec = ensureLive(sessionId);
  let text = statusTextForEvent(event);
  if (text === null) return;
  const opKind = event.type.startsWith("tool.") ? "tool"
    : event.type.startsWith("skill.") ? "skill" : null;
  if (opKind) {
    const id = event.call_id || "call";
    if (event.type.endsWith(".started")) {
      const name = event.name || (opKind === "skill" ? "skill" : "tool");
      rec.activeOps.set(id, { kind: opKind, name });
      rec.batchNames.push(name);
    } else {
      rec.activeOps.delete(id);
    }
    // Aggregate every in-flight op into one line; when the batch drains,
    // summarize what ran so the result text matches what was announced.
    const skills = [];
    const tools = [];
    for (const op of rec.activeOps.values()) (op.kind === "skill" ? skills : tools).push(op.name);
    const chunks = [];
    if (skills.length) chunks.push(`Đang mở skill ${collapseNames(skills)}…`);
    if (tools.length) chunks.push(`Đang dùng ${collapseNames(tools)}…`);
    text = chunks.length ? chunks.join(" · ") : batchDoneText(rec.batchNames);
    if (!rec.activeOps.size) rec.batchNames = [];
    rec.lastOperationalText = text;
  } else if (event.type === "llm.started") {
    if (rec.lastOperationalText) return;
  } else if (event.type === "llm.retry") {
    rec.lastOperationalText = "";
  } else if (event.type === "llm.finished" || event.type === "turn.accepted") {
    rec.lastOperationalText = "";
  }
  rec.statusText = text;
  const status = chatStatusNode(sessionId);
  const ticker = status && status.querySelector(".status-ticker");
  if (!ticker || !ticker.isConnected) {
    flushPendingStatus();
    return;
  }
  if (reduceMotion()) {
    flushPendingStatus();
    let line = ticker.querySelector(".status-line");
    if (!line) {
      line = document.createElement("span");
      line.className = "status-line";
      ticker.append(line);
    }
    line.textContent = text;
    return;
  }
  // Minimum dwell: parallel tool calls emit status lines tens of ms apart —
  // too fast to read. Hold each line ≥ STATUS_MIN_DWELL, coalescing to the
  // newest queued text. Terminals bypass so the outcome is never delayed.
  const terminal = event.type === "turn.completed" || event.type === "turn.failed";
  if (terminal) {
    flushPendingStatus();
    showStatusLine(ticker, text);
    return;
  }
  const wait = STATUS_MIN_DWELL_MS - (performance.now() - lastStatusSwapAt);
  if (wait <= 0) {
    flushPendingStatus();
    showStatusLine(ticker, text);
    return;
  }
  if (pendingStatus) {
    pendingStatus.text = text; // coalesce: newest wins
    return;
  }
  const tickerRef = ticker;
  pendingStatus = {
    text,
    timer: window.setTimeout(() => {
      const pending = pendingStatus;
      pendingStatus = null;
      if (pending && tickerRef.isConnected) showStatusLine(tickerRef, pending.text);
    }, wait),
  };
}

function applyAmbient(sessionId, event) {
  const rec = ensureLive(sessionId);
  const text = ambientLineForEvent(event);
  rec.ambientText = text;
  const status = chatStatusNode(sessionId);
  const line = status && status.querySelector(".status-ambient");
  if (!line || !line.isConnected) {
    flushPendingAmbient();
    return;
  }
  if (reduceMotion()) {
    flushPendingAmbient();
    line.textContent = text;
    return;
  }
  const terminal = event.type === "turn.completed" || event.type === "turn.failed";
  if (terminal) {
    flushPendingAmbient();
    showAmbient(line, text);
    return;
  }
  const wait = AMBIENT_DEBOUNCE_MS - (performance.now() - lastAmbientSwapAt);
  if (wait <= 0) {
    flushPendingAmbient();
    showAmbient(line, text);
    return;
  }
  if (pendingAmbient) {
    pendingAmbient.text = text;
    return;
  }
  const lineRef = line;
  pendingAmbient = {
    text,
    timer: window.setTimeout(() => {
      const pending = pendingAmbient;
      pendingAmbient = null;
      if (pending && lineRef.isConnected) showAmbient(lineRef, pending.text);
    }, wait),
  };
}

function showStatusLine(ticker, text) {
  lastStatusSwapAt = performance.now();
  slideStatus(ticker, text);
}

function showAmbient(line, text) {
  lastAmbientSwapAt = performance.now();
  line.textContent = text;
}

function flushPendingStatus() {
  if (pendingStatus) {
    window.clearTimeout(pendingStatus.timer);
    pendingStatus = null;
  }
}

function flushPendingAmbient() {
  if (pendingAmbient) {
    window.clearTimeout(pendingAmbient.timer);
    pendingAmbient = null;
  }
}
