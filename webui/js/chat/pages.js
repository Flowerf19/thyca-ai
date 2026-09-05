import { el } from "../dom.js";
import { modes } from "../data.js";
import { escapeHtml, formatUpdated, getJson } from "../util.js";
import { state } from "../state.js";
import { EMPTY_BODY, LOAD_ERROR_BODY, threadHtml } from "./view.js";

// Guard chống race khi bấm tab liên tiếp: lượt fetch cũ về sau phải bỏ,
// không được render đè lên tab mới.
let chatFillGen = 0;
// Guard generation cho hydrateChat: renderMode(chat) bấm liên tiếp không
// để lượt hydrate cũ đè activeSessionId của lượt mới.
let hydrateChatGen = 0;

export async function hydrateChat() {
  const gen = ++hydrateChatGen;
  const pages = await refreshChatList();
  if (gen !== hydrateChatGen) return false;
  if (!pages) return false;
  state.chatLive = true;
  // Reload page: state mất trắng (activeSessionId=null) → khôi phục tab
  // đang xem từ sessionStorage, không mặc định về mới nhất.
  if (!state.activeSessionId) {
    try {
      state.activeSessionId = window.sessionStorage.getItem("thyca.activeSessionId") || null;
    } catch { /* storage bị chặn, giữ null */ }
  }
  let index = pages.findIndex((page) => page.sessionId && page.sessionId === state.activeSessionId);
  if (index < 0) index = 0;
  state.activePageIndex = index;
  await fillChatPage(pages[index]);
  if (gen !== hydrateChatGen) return false;
  return true;
}

export function invalidateChatHydrate() {
  hydrateChatGen++;
  chatFillGen++;
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

export async function fillChatAt(index) {
  const gen = ++chatFillGen;
  const page = modes.chat.pages[index];
  if (!page) return false;
  const ok = await fillChatPage(page);
  // Fetch cũ về trễ: bỏ, giữ tab mới.
  if (gen !== chatFillGen) return false;
  state.activePageIndex = index;
  return ok;
}

async function fillChatPage(page) {
  if (!page) return false;
  if (!page.sessionId) {
    bindSession(page, null);
    page.body = EMPTY_BODY;
    return true;
  }
  let detail = null;
  try {
    // Timeout 15s như postJson: session kẹt không treo tab,
    // fail thì rơi vào nhánh LOAD_ERROR_BODY bên dưới.
    detail = await getJson(`/api/sessions/${page.sessionId}`);
  } catch {
    detail = null;
  }
  if (!detail || !Array.isArray(detail.messages)) {
    // Lỗi visible thay vì trang trắng: giữ body cũ nếu có, báo rõ.
    bindSession(page, page.sessionId);
    if (!page.body) page.body = LOAD_ERROR_BODY;
    page.loadError = true;
    return false;
  }
  page.loadError = false;
  applyDetailToPage(page, detail);
  return true;
}

export function applyDetail(detail) {
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

export function bindSession(page, sessionId) {
  if (modes.chat.pages[state.activePageIndex] !== page) return;
  state.activeSessionId = sessionId;
  // Giữ tab đang xem qua reload page (sessionStorage theo tab).
  try {
    if (sessionId) window.sessionStorage.setItem("thyca.activeSessionId", sessionId);
    else window.sessionStorage.removeItem("thyca.activeSessionId");
  } catch { /* storage bị chặn, bỏ qua */ }
}

async function refreshChatList() {
  const payload = await getJson("/api/sessions");
  if (!payload || !Array.isArray(payload.sessions)) return null;
  const pages = pagesFromSessions(payload);
  if (payload.model) state.lastChatModel = payload.model;
  try {
    const cfg = await getJson("/api/config");
    if (cfg?.values?.provider?.baseUrl) state.lastChatBaseUrl = cfg.values.provider.baseUrl;
  } catch { /* giữ baseUrl cũ, không chặn chat */ }
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

export async function refreshChatKicker() {
  // Re-check provider sau khi settings lưu (TASK-040): mở khóa composer
  // nếu trước đó bị boot gate chặn, refresh kicker model mới.
  try {
    const status = await getJson("/api/config/status");
    if (status && status.ready !== false && !state.chatLive) {
      state.chatLive = true;
      if (el.line) el.line.disabled = false;
      if (el.send) el.send.disabled = false;
      if (el.hint && el.hint.textContent.includes("Cần cấu hình")) {
        el.hint.textContent = "";
        el.hint.className = "hint";
      }
    }
  } catch { /* giữ trạng thái cũ */ }
  const pages = await refreshChatList();
  if (!pages) return;
  if (state.activeMode === "chat" && el.modeBreadcrumb && !el.modeBreadcrumb.classList.contains("crumb-mark")) {
    el.modeBreadcrumb.textContent = modes.chat.kicker;
  }
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
