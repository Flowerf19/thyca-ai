import { getJson, postJson, postNdjson } from "./backend/api.js";
import { SEND_ERROR_STATUS } from "./backend/chat-status.js";
import { cleanText, formatSessionTime } from "./backend/format.js";
import {
  createLiveStatus,
  renderConversation,
  renderEmpty,
  renderError,
  setChatBrand,
  updateLiveStatus,
} from "./backend/chat-view.js";

const el = {
  composer: document.querySelector("#composer"),
  input: document.querySelector("#message"),
  send: document.querySelector("#send-message"),
  status: document.querySelector("#composer-status"),
  sessionList: document.querySelector("#session-list"),
  newSession: document.querySelector("#new-session"),
  messageList: document.querySelector("#message-list"),
  scroll: document.querySelector("#conversation-scroll"),
  end: document.querySelector("#conversation-end"),
  toBottom: document.querySelector("#to-bottom"),
  label: document.querySelector("#conversation-label"),
  idleNudge: document.querySelector("#idle-nudge"),
  idleRemember: document.querySelector("#idle-remember"),
  idleDismiss: document.querySelector("#idle-dismiss"),
};

const IDLE_MS = 15 * 60 * 1000;
const IDLE_REMEMBER = "Hãy nhớ những điều đáng giữ trong phiên này.";
let idleTimer = 0;
let idleFromNudge = false;
const idleArmed = new Set();

const state = {
  sessions: [],
  activeId: "",
  detail: null,
  busy: false,
  loadGeneration: 0,
};

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `composer-status${kind ? ` is-${kind}` : ""}`;
}

function hideIdle() {
  el.idleNudge.hidden = true;
}

function sessionKey() {
  return state.activeId || "";
}

function noteSend() {
  const key = sessionKey();
  if (!key) return;
  if (idleFromNudge) idleArmed.delete(key);
  else idleArmed.add(key);
  idleFromNudge = false;
}

async function showIdle() {
  const key = sessionKey();
  if (!key || state.busy || !idleArmed.has(key)) return;
  try {
    const detail = await getJson(`/api/sessions/${encodeURIComponent(key)}`);
    if (sessionKey() !== key || !idleArmed.has(key) || detail?.ask_remember !== true) return;
    el.idleNudge.hidden = false;
  } catch {
    // An idle hint is optional and must not disrupt chat.
  }
}

function armIdle() {
  hideIdle();
  window.clearTimeout(idleTimer);
  idleTimer = 0;
  if (!idleArmed.has(sessionKey())) return;
  idleTimer = window.setTimeout(() => void showIdle(), IDLE_MS);
}

function setBusy(busy) {
  state.busy = busy;
  el.input.disabled = busy;
  el.send.disabled = busy;
  el.newSession.disabled = busy;
  el.composer.classList.toggle("is-loading", busy);
  el.sessionList.querySelectorAll("button").forEach((button) => {
    button.disabled = busy;
  });
  el.messageList.setAttribute("aria-busy", String(busy));
}

function sessionButton(session) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-item";
  button.dataset.sessionId = String(session.id || "");
  const selected = button.dataset.sessionId === state.activeId;
  button.classList.toggle("is-active", selected);
  if (selected) button.setAttribute("aria-current", "page");

  const icon = document.createElement("span");
  icon.className = "session-icon";
  icon.setAttribute("aria-hidden", "true");
  const name = document.createElement("span");
  name.className = "session-name";
  name.textContent = cleanText(session.title, "Phiên trống");
  const time = document.createElement("time");
  time.dateTime = String(session.updated_at || "");
  time.textContent = formatSessionTime(session.updated_at);
  const body = document.createElement("span");
  body.className = "session-body";
  body.append(icon, name, time);
  button.append(body);
  button.addEventListener("click", () => void loadSession(button.dataset.sessionId));
  return button;
}

function renderSessions() {
  if (!state.sessions.length) {
    const empty = document.createElement("p");
    empty.className = "sidebar-state";
    empty.textContent = "Chưa có phiên đã lưu.";
    el.sessionList.replaceChildren(empty);
    return;
  }
  el.sessionList.replaceChildren(...state.sessions.map(sessionButton));
}

async function refreshSessions() {
  const payload = await getJson("/api/sessions");
  state.sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
  renderSessions();
  return payload;
}

function rememberActiveSession(id) {
  try {
    if (id) sessionStorage.setItem("thyca.activeSessionId", id);
    else sessionStorage.removeItem("thyca.activeSessionId");
  } catch {
    // Storage may be blocked; the current tab still works.
  }
}

function scrollToBottom(behavior = "smooth") {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scroll.scrollTo({
    top: el.scroll.scrollHeight,
    behavior: reduced ? "auto" : behavior,
  });
  updateToBottom();
}

function updateToBottom() {
  const distance = el.scroll.scrollHeight - el.scroll.scrollTop - el.scroll.clientHeight;
  const scrollable = el.scroll.scrollHeight > el.scroll.clientHeight + 1;
  el.toBottom.hidden = !scrollable || distance <= 120;
}

function renderDetail(detail) {
  state.detail = detail;
  state.activeId = String(detail?.id || state.activeId || "");
  rememberActiveSession(state.activeId);
  const messages = Array.isArray(detail?.messages) ? detail.messages : [];
  if (!renderConversation(el.messageList, messages)) renderEmpty(el.messageList);
  el.label.textContent = cleanText(detail?.title, "Hôm nay");
  renderSessions();
  requestAnimationFrame(() => scrollToBottom("auto"));
}

async function loadSession(sessionId) {
  if (!sessionId || state.busy) return;
  const generation = ++state.loadGeneration;
  state.activeId = sessionId;
  renderSessions();
  setStatus("Đang mở phiên…");
  el.messageList.setAttribute("aria-busy", "true");
  try {
    const detail = await getJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (generation !== state.loadGeneration) return;
    renderDetail(detail);
    setStatus();
    armIdle();
  } catch (error) {
    if (generation !== state.loadGeneration) return;
    renderError(el.messageList, messageOf(error, "Không mở được phiên."), () => void loadSession(sessionId));
    setStatus(messageOf(error, "Không mở được phiên."), "error");
  } finally {
    if (generation === state.loadGeneration) el.messageList.setAttribute("aria-busy", "false");
  }
}

function newSession() {
  if (state.busy) return;
  ++state.loadGeneration;
  state.activeId = "";
  state.detail = null;
  rememberActiveSession("");
  renderSessions();
  renderEmpty(el.messageList);
  el.label.textContent = "Phiên trống";
  armIdle();
  setStatus("Phiên mới đã sẵn sàng.", "success");
  el.input.focus();
}

async function ensureSession() {
  if (state.activeId) return state.activeId;
  const detail = await postJson("/api/sessions", {});
  state.activeId = String(detail.id || "");
  state.detail = detail;
  rememberActiveSession(state.activeId);
  return state.activeId;
}

async function sendMessage() {
  const text = el.input.value.trim();
  if (!text) {
    setStatus("Viết một câu trước khi gửi.", "error");
    el.input.focus();
    return;
  }
  if (state.busy) return;

  hideIdle();
  window.clearTimeout(idleTimer);
  setBusy(true);
  setStatus("Đang xử lý…");
  el.input.value = "";
  try {
    const sessionId = await ensureSession();
    const previous = Array.isArray(state.detail?.messages) ? state.detail.messages : [];
    const optimistic = [...previous, { role: "user", content: text, ts: new Date().toISOString() }];
    renderConversation(el.messageList, optimistic);
    const live = createLiveStatus(el.messageList);
    scrollToBottom();
    const detail = await postNdjson(
      `/api/sessions/${encodeURIComponent(sessionId)}/turn/stream`,
      { text },
      (event) => {
        updateLiveStatus(live, event);
        scrollToBottom();
      },
    );
    renderDetail(detail);
    await refreshSessions();
    setStatus("Đã nhận trả lời.", "success");
    noteSend();
    armIdle();
  } catch (error) {
    idleFromNudge = false;
    const live = el.messageList.querySelector(".live-status:last-of-type");
    if (live) setChatBrand(live, { state: "error", status: SEND_ERROR_STATUS });
    setStatus(SEND_ERROR_STATUS, "error");
  } finally {
    setBusy(false);
    el.input.focus();
    updateToBottom();
  }
}

function bind() {
  el.newSession.addEventListener("click", newSession);
  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    void sendMessage();
  });
  el.input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    el.composer.requestSubmit();
  });
  el.input.addEventListener("input", () => {
    if (el.input.value.trim()) setStatus();
    armIdle();
  });
  el.idleRemember.addEventListener("click", () => {
    hideIdle();
    idleFromNudge = true;
    el.input.value = IDLE_REMEMBER;
    void sendMessage();
  });
  el.idleDismiss.addEventListener("click", armIdle);
  el.scroll.addEventListener("scroll", updateToBottom, { passive: true });
  el.toBottom.addEventListener("click", () => scrollToBottom());
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(updateToBottom);
    observer.observe(el.scroll);
    observer.observe(el.messageList);
  }
}

async function boot() {
  bind();
  renderEmpty(el.messageList, "Đang mở sổ…", "Thyca đang tìm những phiên đã lưu.");
  try {
    const provider = await getJson("/api/config/status");
    if (provider.ready === false) {
      location.replace("./provider.html?required=1");
      return;
    }
    await refreshSessions();
    let saved = "";
    try {
      saved = sessionStorage.getItem("thyca.activeSessionId") || "";
    } catch {
      saved = "";
    }
    const initial = state.sessions.find((item) => item.id === saved) || state.sessions[0];
    if (initial?.id) await loadSession(String(initial.id));
    else {
      state.activeId = "";
      state.detail = null;
      renderEmpty(el.messageList);
      el.messageList.setAttribute("aria-busy", "false");
      el.input.focus();
    }
  } catch (error) {
    renderError(el.messageList, messageOf(error, "Backend chưa sẵn sàng."), () => location.reload());
    setStatus(messageOf(error, "Backend chưa sẵn sàng."), "error");
    el.messageList.setAttribute("aria-busy", "false");
    el.input.disabled = true;
    el.send.disabled = true;
  }
}

void boot();
