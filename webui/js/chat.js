import { el } from "./dom.js";
import { modes } from "./data.js";
import { escapeHtml } from "./memories.js";
import { state } from "./state.js";

const STATUS_LINES = [
  "Đang tìm vần…",
  "Đang lắng nghe nhịp…",
  "Đang tìm tứ thơ…",
  "Nghe nhịp trong đầu…",
  "Đang đợi cảm hứng…",
  "Lắng nghe khoảng lặng…",
  "Đang chọn từ…",
  "Đang cân nhắc chữ…",
  "Đang sắp xếp nhịp…",
  "Đang tìm hình ảnh…",
  "Đang buộc câu thơ…",
  "Đang chỉnh nhịp điệu…",
  "Hmm…",
  "Đang suy nghĩ…",
  "Tiếp tục suy nghĩ…",
  "Đang để cảm xúc lắng…",
  "Đang nghe trái tim…",
  "Đang viết tiếp…",
  "Đang làm thơ…",
  "Đang viết khổ thơ…",
  "Đang thả chữ xuống trang…",
  "Đang để thơ tự đến…",
  "Sắp xong rồi…",
];

const RECENT_CAP = 4;
let statusTimer = 0;
let statusRecent = [];

const EMPTY_BODY =
  '<div class="new-page-empty"><span aria-hidden="true">+</span><p>Chưa có tin nào.</p><small>Nói điều đầu tiên để mở phiên.</small></div>';

export async function hydrateChat() {
  const payload = await getJson("/api/sessions");
  if (!payload || !Array.isArray(payload.sessions)) return false;
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
  state.chatLive = true;
  let index = pages.findIndex((page) => page.sessionId && page.sessionId === state.activeSessionId);
  if (index < 0) index = 0;
  state.activePageIndex = index;
  await fillChatPage(pages[index]);
  return true;
}

export async function createChatSession() {
  const created = await postJson("/api/sessions", {});
  if (!created || !created.id) throw new Error("Không tạo được phiên.");
  state.activeSessionId = created.id;
  await hydrateChat();
  const index = modes.chat.pages.findIndex((page) => page.sessionId === created.id);
  state.activePageIndex = index >= 0 ? index : 0;
}

export function beginOutgoingTurn(text) {
  stopStatusCycle();
  let list = el.pageBody.querySelector(".entry-list");
  if (!list) {
    el.pageBody.innerHTML = '<div class="entry-list"></div>';
    list = el.pageBody.querySelector(".entry-list");
  }
  list.insertAdjacentHTML("beforeend", entryHtml("user", text));
  list.lastElementChild.classList.add("is-enter");
  const first = nextStatus([]);
  statusRecent = [first];
  list.insertAdjacentHTML("beforeend", statusHtml(first));
  startStatusCycle(list.querySelector(".entry-status"));
  scrollThread();
}

export function stopStatusCycle() {
  if (statusTimer) {
    window.clearInterval(statusTimer);
    statusTimer = 0;
  }
}

export function removeStatus() {
  stopStatusCycle();
  const node = el.pageBody.querySelector(".entry-status");
  if (node) node.remove();
}

export function settleIncoming() {
  const page = modes.chat.pages[state.activePageIndex];
  const liveList = el.pageBody.querySelector(".entry-list");
  const wrap = document.createElement("div");
  wrap.innerHTML = page?.body || "";
  const fresh = wrap.querySelector(".entry-list");
  if (!fresh || !liveList) return false;
  stopStatusCycle();
  const kept = [...liveList.children].filter((node) => !node.classList.contains("entry-status")).length;
  liveList.replaceWith(fresh);
  [...fresh.children].slice(kept).forEach((node, index) => {
    node.classList.add("is-enter");
    node.style.animationDelay = `${index * 80}ms`;
  });
  const heading = el.pageHeader.querySelector("h1");
  if (heading && page.title) heading.innerHTML = page.title;
  const kicker = el.pageHeader.querySelector(".page-kicker");
  if (kicker && page.kicker) kicker.textContent = page.kicker;
  scrollThread();
  return true;
}

export async function sendChatTurn(text) {
  let sessionId = state.activeSessionId;
  if (!sessionId) {
    const created = await postJson("/api/sessions", {});
    if (!created || !created.id) throw new Error("Không tạo được phiên.");
    sessionId = created.id;
  }
  const detail = await postJson(`/api/sessions/${sessionId}/turn`, { text });
  applyDetail(detail);
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
    state.activeSessionId = null;
    page.body = EMPTY_BODY;
    return;
  }
  const detail = await getJson(`/api/sessions/${page.sessionId}`);
  if (!detail || !Array.isArray(detail.messages)) {
    state.activeSessionId = page.sessionId;
    page.body = page.body || EMPTY_BODY;
    return;
  }
  applyDetailToPage(page, detail);
}

function applyDetail(detail) {
  if (!detail || !detail.id) throw new Error("Không nhận được trả lời.");
  let pages = modes.chat.pages;
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
  state.activePageIndex = index;
}

function applyDetailToPage(page, detail) {
  const next = pageFromDetail(detail);
  page.title = next.title;
  page.date = next.date;
  page.tag = next.tag;
  page.kicker = next.kicker;
  page.body = next.body;
  page.sessionId = next.sessionId;
  state.activeSessionId = next.sessionId;
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
    tag: escapeHtml(shortId(id)),
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
    tag: escapeHtml(shortId(id)),
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
    if (!pending.length) return;
    parts.push(`<div class="tool-strip">${pending.join("")}</div>`);
    pending.length = 0;
  };
  for (const message of messages) {
    if (!message || message.role === "system" || message.role === "tool") continue;
    if (message.role === "assistant" && message.tool_calls?.length) {
      for (const call of message.tool_calls) {
        pending.push(`<span class="tool-pill">${escapeHtml(call.name || "")}</span>`);
      }
      if (!message.content) continue;
    }
    flushTools();
    if (message.role === "user" || message.role === "assistant") {
      parts.push(entryHtml(message.role, message.content || ""));
    }
  }
  flushTools();
  if (!parts.length) return EMPTY_BODY;
  return `<div class="entry-list">${parts.join("")}</div>`;
}

function statusHtml(line) {
  return `<article class="entry entry-thyca entry-status" aria-label="Thyca đang nghĩ" aria-live="off">
      <time>thyca</time>
      <div class="entry-copy">
        <div class="status-row">
          <span class="status-dots" aria-hidden="true"><i></i><i></i><i></i></span>
          <span class="status-ticker"><span class="status-line">${escapeHtml(line)}</span></span>
        </div>
      </div>
    </article>`;
}

function startStatusCycle(node) {
  if (!node) return;
  const ticker = node.querySelector(".status-ticker");
  if (!ticker) return;
  statusTimer = window.setInterval(() => {
    const next = nextStatus(statusRecent);
    statusRecent = [...statusRecent, next].slice(-RECENT_CAP);
    if (reduceMotion()) {
      const line = ticker.querySelector(".status-line");
      if (line) line.textContent = next;
      return;
    }
    slideStatus(ticker, next);
  }, 1000);
}

function nextStatus(recent) {
  const blocked = new Set(recent.slice(-RECENT_CAP));
  const pool = STATUS_LINES.filter((line) => !blocked.has(line));
  const pick = pool.length ? pool : STATUS_LINES;
  return pick[Math.floor(Math.random() * pick.length)];
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
  const who = role === "user" ? "you" : "thyca";
  const cls = role === "user" ? "entry-user" : "entry-thyca";
  const blocks = String(content)
    .split(/\n{2,}/)
    .map((block) => `<p>${escapeHtml(block).replaceAll("\n", "<br>")}</p>`)
    .join("");
  return `<article class="entry ${cls}"><time>${who}</time><div class="entry-copy">${blocks}</div></article>`;
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
