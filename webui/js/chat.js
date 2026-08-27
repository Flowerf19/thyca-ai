import { el } from "./dom.js";
import { modes } from "./data.js";
import { formatMarkdown } from "./markdown.js";
import { escapeHtml } from "./memories.js";
import { createNdjsonDecoder } from "./ndjson.js";
import { scoreFromEvents } from "./staff-map.js";
import { statusTextForEvent } from "./turn-status.js";
import { clearStaffs, mountStaff } from "./staff.js";
import { state } from "./state.js";

let liveEvents = [];
let liveScore = null;

const EMPTY_BODY =
  '<div class="new-page-empty"><span aria-hidden="true">+</span><p>Chưa có tin nào.</p><small>Nói điều đầu tiên để mở phiên.</small></div>';

export async function hydrateChat() {
  const pages = await refreshChatList();
  if (!pages) return false;
  state.chatLive = true;
  let index = pages.findIndex((page) => page.sessionId && page.sessionId === state.activeSessionId);
  if (index < 0) index = 0;
  state.activePageIndex = index;
  await fillChatPage(pages[index]);
  return true;
}

export async function createChatSession() {
  state.activeSessionId = null;
  if (state.chatLive) {
    await refreshChatList();
  }
  resetToNewChatPage();
}

// đưa trang "phiên mới" lên đầu — dùng khi bấm vào mode Chat
export function resetToNewChatPage() {
  const rest = (modes.chat.pages || []).filter((page) => page.sessionId);
  modes.chat.pages = [emptyPage(""), ...rest];
  state.activePageIndex = 0;
  state.activeSessionId = null;
}

export function beginOutgoingTurn(text) {
  removeStatus();
  clearStaffs(el.pageBody);
  let list = el.pageBody.querySelector(".entry-list");
  if (!list) {
    el.pageBody.innerHTML = '<div class="entry-list"></div>';
    list = el.pageBody.querySelector(".entry-list");
  }
  list.insertAdjacentHTML("beforeend", entryHtml("user", text));
  list.lastElementChild.classList.add("is-enter");
  list.insertAdjacentHTML("beforeend", statusHtml());
  liveEvents = [];
  liveScore = scoreFromEvents([]);
  mountStaff(list.lastElementChild, liveScore);
  scrollThread();
}

export function removeStatus() {
  const node = el.pageBody.querySelector(".entry-status");
  if (!node) return;
  clearStaffs(node);
  node.remove();
}

export function settleIncoming() {
  const page = modes.chat.pages[state.activePageIndex];
  const liveList = el.pageBody.querySelector(".entry-list");
  const wrap = document.createElement("div");
  wrap.innerHTML = page?.body || "";
  const fresh = wrap.querySelector(".entry-list");
  if (!fresh || !liveList) return false;
  clearStaffs(el.pageBody);
  const kept = [...liveList.children].filter((node) => !node.classList.contains("entry-status")).length;
  liveList.replaceWith(fresh);
  [...fresh.children].slice(kept).forEach((node, index) => {
    node.classList.add("is-enter");
    node.style.animationDelay = `${index * 80}ms`;
  });
  const born = [...fresh.children].slice(kept).find((node) => node.classList.contains("entry-thyca"));
  if (born && liveScore) mountStaff(born, liveScore);
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
  if (!sessionId) {
    const created = await postJson("/api/sessions", {});
    if (!created || !created.id) throw new Error("Không tạo được phiên.");
    sessionId = created.id;
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
      const terminal = ingestTurnEvent(event);
      if (terminal === "completed") completed = event.detail;
      else if (terminal === "failed") failed = event;
    }
  }
  for (const event of decoder.flush()) {
    const terminal = ingestTurnEvent(event);
    if (terminal === "completed") completed = event.detail;
    else if (terminal === "failed") failed = event;
  }
  if (failed) {
    const message = failed.message ? String(failed.message) : "Lượt đã dừng.";
    throw new Error(message);
  }
  if (!completed) throw new Error("Không nhận được trả lời.");
  applyDetail(completed);
  return completed;
}

function ingestTurnEvent(event) {
  liveEvents.push(event);
  liveScore = scoreFromEvents(liveEvents);
  applyStatus(event);
  const status = chatStatusNode();
  if (status) {
    mountStaff(status, liveScore);
    if (event.type === "turn.failed") status.classList.add("is-error");
  }
  if (event.type === "turn.completed") return "completed";
  if (event.type === "turn.failed") return "failed";
  return null;
}

function chatStatusNode() {
  if (state.activeMode !== "chat") return null;
  const status = el.pageBody.querySelector(".entry-status");
  return status && status.isConnected ? status : null;
}

function applyStatus(event) {
  const text = statusTextForEvent(event);
  if (text === null) return;
  const status = chatStatusNode();
  const ticker = status && status.querySelector(".status-ticker");
  if (!ticker || !ticker.isConnected) return;
  if (reduceMotion()) {
    let line = ticker.querySelector(".status-line");
    if (!line) {
      line = document.createElement("span");
      line.className = "status-line";
      ticker.append(line);
    }
    line.textContent = text;
  } else {
    slideStatus(ticker, text);
  }
}

export async function fillChatAt(index) {
  const page = modes.chat.pages[index];
  if (!page) return;
  state.activePageIndex = index;
  await fillChatPage(page);
}

async function fillChatPage(page) {
  if (!page) return;
  if (!page.sessionId) {
    bindSession(page, null);
    page.body = EMPTY_BODY;
    return;
  }
  const detail = await getJson(`/api/sessions/${page.sessionId}`);
  if (!detail || !Array.isArray(detail.messages)) {
    bindSession(page, page.sessionId);
    page.body = page.body || EMPTY_BODY;
    return;
  }
  applyDetailToPage(page, detail);
}

function applyDetail(detail) {
  if (!detail || !detail.id) throw new Error("Không nhận được trả lời.");
  let pages = modes.chat.pages;
  const viewingId =
    state.activeMode === "chat" ? String(pages[state.activePageIndex]?.sessionId || "") : null;
  let index = pages.findIndex((page) => page.sessionId === detail.id);
  if (index < 0) {
    pages = [pageFromDetail(detail), ...pages.filter((page) => page.sessionId)];
    modes.chat.pages = pages;
    index = 0;
  } else {
    pages[index] = pageFromDetail(detail);
  }
  const count = document.querySelector('[data-mode="chat"] .mode-count');
  if (count) count.textContent = String(pages.filter((page) => page.sessionId).length);
  state.activeSessionId = detail.id;
  if (state.activeMode === "chat" && (viewingId === "" || viewingId === detail.id)) {
    state.activePageIndex = index;
  }
}

function applyDetailToPage(page, detail) {
  const next = pageFromDetail(detail);
  page.title = next.title;
  page.date = next.date;
  page.tag = next.tag;
  page.kicker = next.kicker;
  page.body = next.body;
  page.sessionId = next.sessionId;
  bindSession(page, next.sessionId);
}

function bindSession(page, sessionId) {
  if (modes.chat.pages[state.activePageIndex] !== page) return;
  state.activeSessionId = sessionId;
}

async function refreshChatList() {
  const payload = await getJson("/api/sessions");
  if (!payload || !Array.isArray(payload.sessions)) return null;
  const pages = pagesFromSessions(payload);
  modes.chat = {
    label: "Chat",
    listLabel: "Phiên gần đây",
    kicker: payload.model ? `~/.thyca · ${payload.model}` : modes.chat.kicker,
    note: modes.chat.note,
    chips: modes.chat.chips,
    pages,
  };
  const count = document.querySelector('[data-mode="chat"] .mode-count');
  if (count) count.textContent = String(payload.sessions.length);
  return pages;
}

export function pagesFromSessions(payload) {
  const model = payload.model || "";
  const sessions = payload.sessions || [];
  if (!sessions.length) return [emptyPage(model)];
  return sessions.map((item) => pageFromSummary(item, model));
}

function pageFromSummary(item, model) {
  const id = String(item.id || "");
  return {
    title: escapeHtml(item.title || "Phiên trống"),
    date: escapeHtml(formatUpdated(item.updated_at)),
    tag: "",
    tone: "chat",
    sessionId: id,
    kicker: escapeHtml(id && model ? `${id} · ${model}` : id || model),
    body: "",
  };
}

function pageFromDetail(detail) {
  const id = String(detail.id || "");
  const model = detail.model || "";
  return {
    title: escapeHtml(detail.title || "Phiên trống"),
    date: escapeHtml(formatUpdated((detail.messages || []).at(-1)?.ts)),
    tag: "",
    tone: "chat",
    sessionId: id,
    kicker: escapeHtml(id && model ? `${id} · ${model}` : id || model),
    body: threadHtml(detail.messages || []),
  };
}

function emptyPage(model) {
  return {
    title: "Phiên trống",
    date: "chưa lưu",
    tag: "mới",
    tone: "chat",
    sessionId: "",
    kicker: model ? `${model} · phiên mới` : "Phiên mới · chưa lưu",
    body: EMPTY_BODY,
  };
}

export function threadHtml(messages) {
  const parts = [];
  const pending = [];
  const flushTools = () => {
    const names = pending.map((name) => String(name || "").trim()).filter(Boolean);
    pending.length = 0;
    if (!names.length) return;
    const counts = new Map();
    for (const name of names) {
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    const items = [];
    for (const [name, count] of counts) {
      items.push(count > 1 ? `${name} ×${count}` : name);
    }
    parts.push(
      `<div class="tool-strip"><span class="tool-kicker">Tools used:</span> ${escapeHtml(items.join(", "))}</div>`,
    );
  };
  for (const message of messages) {
    if (!message || message.role === "system" || message.role === "tool") continue;
    if (message.role === "assistant" && message.tool_calls?.length) {
      for (const call of message.tool_calls) {
        pending.push(call.name || "");
      }
      if (!message.content) continue;
    }
    // meta-only messages (kind: "naming") carry no chat content — never a bubble
    if (message.role === "assistant" && !message.content && !message.tool_calls?.length) continue;
    flushTools();
    if (message.role === "user" || message.role === "assistant") {
      parts.push(entryHtml(message.role, message.content || ""));
    }
  }
  flushTools();
  if (!parts.length) return EMPTY_BODY;
  return `<div class="entry-list">${parts.join("")}</div>`;
}

function statusHtml() {
  return `<article class="entry entry-thyca entry-status" aria-label="Thyca đang nghĩ" aria-live="off">
      <div class="entry-thyca-head"><time>thyca</time><span class="status-ticker"><span class="status-line">Đang chờ Thyca…</span></span></div>
    </article>`;
}

function slideStatus(ticker, next) {
  const outgoing = ticker.querySelector(".status-line:not(.is-out)");
  const incoming = document.createElement("span");
  incoming.className = "status-line is-in";
  incoming.textContent = next;
  ticker.append(incoming);
  if (outgoing) outgoing.classList.add("is-out");
  window.setTimeout(() => {
    if (outgoing) outgoing.remove();
    incoming.classList.remove("is-in");
  }, 200);
}

function scrollThread() {
  if (!el.notebook) return;
  el.notebook.scrollTo({
    top: el.notebook.scrollHeight,
    behavior: reduceMotion() ? "auto" : "smooth",
  });
}

function reduceMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function entryHtml(role, content) {
  const cls = role === "user" ? "entry-user" : "entry-thyca";
  const stamp =
    role === "assistant" ? "<time>thyca</time>" : "";
  return `<article class="entry ${cls}">${stamp}<div class="entry-copy">${formatMarkdown(content)}</div></article>`;
}

function shortId(id) {
  const match = String(id).match(/_([0-9a-f]{4})$/);
  return match ? match[1] : id.slice(-6) || "chat";
}

function formatUpdated(value) {
  if (!value) return "";
  const stamp = new Date(String(value));
  if (Number.isNaN(stamp.getTime())) return String(value);
  return stamp.toLocaleString("vi-VN", { dateStyle: "medium", timeStyle: "short" });
}

export async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) return null;
  return response.json();
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload && payload.error ? String(payload.error) : "Không gửi được.";
    throw new Error(message);
  }
  return payload;
}
