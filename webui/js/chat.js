import { modes } from "./data.js";
import { escapeHtml } from "./memories.js";
import { state } from "./state.js";

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
  for (const message of messages) {
    if (!message || message.role === "system" || message.role === "tool") continue;
    if (message.role === "assistant" && message.tool_calls?.length) {
      const pills = message.tool_calls
        .map((call) => `<span class="tool-pill">${escapeHtml(call.name || "")}</span>`)
        .join("");
      parts.push(`<div class="tool-strip">${pills}</div>`);
      if (!message.content) continue;
    }
    if (message.role === "user" || message.role === "assistant") {
      parts.push(entryHtml(message.role, message.content || ""));
    }
  }
  if (!parts.length) return EMPTY_BODY;
  return `<div class="entry-list">${parts.join("")}</div>`;
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

async function getJson(url) {
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
