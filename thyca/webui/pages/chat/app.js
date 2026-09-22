import { getJson } from "../../shared/js/api.js";
import { cleanText } from "../../shared/js/format.js";
import { renderConversation, renderEmpty, renderError } from "./transcript.js";
import { createLiveStatus } from "./live-status.js";
import {
  initSessionsSidebar,
  messageOf,
  refreshSessions,
  rememberActiveSession,
  renderSessions,
  revealSession,
  sessionKey,
  submitDelete,
  submitRename,
} from "./sessions-sidebar.js";
import {
  abortFollow,
  clearRunningPoll,
  followTurn,
  initTurnFollow,
  liveTurns,
  scrollToBottom,
  stopTurn,
  streamingSessions,
  updateToBottom,
  watchRunning,
} from "./turn-follow.js";
import {
  armIdle,
  clearNudge,
  fillComposerControls,
  initComposer,
  retryMessage,
  sendIdleRemember,
  sendMessage,
} from "./composer.js";

const el = {
  composer: document.querySelector("#composer"),
  input: document.querySelector("#message"),
  send: document.querySelector("#send-message"),
  model: document.querySelector("#composer-model"),
  effort: document.querySelector("#thinking-effort"),
  stop: document.querySelector("#stop-button"),
  status: document.querySelector("#composer-hint"),
  sessionList: document.querySelector("#session-list"),
  newSession: document.querySelector("#new-session"),
  messageList: document.querySelector("#message-list"),
  scroll: document.querySelector("#conversation-scroll"),
  toBottom: document.querySelector("#to-bottom"),
  idleNudge: document.querySelector("#idle-nudge"),
  idleRemember: document.querySelector("#idle-remember"),
  idleDismiss: document.querySelector("#idle-dismiss"),
  renameDialog: document.querySelector("#rename-dialog"),
  renameForm: document.querySelector("#rename-form"),
  renameId: document.querySelector("#rename-id"),
  renameName: document.querySelector("#rename-name"),
  renameStatus: document.querySelector("#rename-status"),
  renameCancel: document.querySelector("#cancel-rename"),
  deleteDialog: document.querySelector("#delete-dialog"),
  deleteForm: document.querySelector("#delete-form"),
  deleteNote: document.querySelector("#delete-note"),
  deleteStatus: document.querySelector("#delete-status"),
  deleteCancel: document.querySelector("#cancel-delete"),
  chatTitle: document.querySelector("#chat-title"),
};

const state = {
  sessions: [],
  activeId: "",
  detail: null,
  sending: false,
  running: false,
  loadGeneration: 0,
  deleteId: "",
  // True while a rename/delete request is in flight: one submission at a time.
  saving: false,
  // Page of the sidebar session list (SESSIONS_PAGE_SIZE per page).
  sessionPage: 1,
};

function setChatTitle(name) {
  if (el.chatTitle) el.chatTitle.textContent = cleanText(name, "Phiên trống");
}

// Sending blocks the session being sent to, and the session whose turn is
// running — not the whole tab. Another session stays readable *and* writable.
function composerBusy() {
  if (state.running) return true;
  if (!state.sending) return false;
  return !streamingSessions.size || streamingSessions.has(sessionKey());
}

function setSending(sending) {
  state.sending = sending;
  syncComposer();
}

function setRunning(running) {
  state.running = running;
  syncComposer();
}

function syncComposer() {
  const busy = composerBusy();
  el.input.disabled = busy;
  el.send.disabled = busy;
  el.composer.classList.toggle("is-loading", busy);
  el.messageList.setAttribute("aria-busy", String(busy));
}

function renderDetail(detail) {
  state.detail = detail;
  state.activeId = String(detail?.id || state.activeId || "");
  rememberActiveSession(state.activeId);
  setChatTitle(detail?.title);
  const messages = Array.isArray(detail?.messages) ? detail.messages : [];
  if (!renderConversation(el.messageList, messages)) renderEmpty(el.messageList);
  setRunning(detail?.running === true);
  if (state.running) {
    // A turn streaming in this tab gets its own card back — same object, so
    // the events it has been collecting are still on it; resume() restarts
    // the clock tick that stopped while the card was detached. A turn started
    // elsewhere (or before a reload) gets a fresh one, counted from the
    // turn's real start (started_at) instead of this remount; followTurn then
    // attaches the live stream to that same card.
    const live = liveTurns.get(state.activeId);
    if (live && (!live.startedAt || live.startedAt === detail.started_at)) {
      el.messageList.append(live.article);
      live.thinking?.resume();
    } else {
      liveTurns.set(state.activeId, createLiveStatus(el.messageList, detail.started_at));
    }
  } else if (!streamingSessions.has(state.activeId)) {
    liveTurns.delete(state.activeId);
  }
  renderSessions();
  requestAnimationFrame(() => scrollToBottom("auto"));
}

async function loadSession(sessionId) {
  if (!sessionId) return;
  const generation = ++state.loadGeneration;
  abortFollow();
  clearRunningPoll();
  state.activeId = sessionId;
  const preview = state.sessions.find((session) => String(session.id) === sessionId);
  if (preview) setChatTitle(preview.title);
  revealSession(sessionId); // opening a session shows the page that holds it
  renderSessions();
  el.messageList.setAttribute("aria-busy", "true");
  try {
    const detail = await getJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (generation !== state.loadGeneration) return;
    renderDetail(detail);
    if (state.running && !streamingSessions.has(sessionId)) {
      void followTurn(sessionId, detail.started_at);
    } else watchRunning(sessionId);
    armIdle();
  } catch (error) {
    if (generation !== state.loadGeneration) return;
    renderError(el.messageList, messageOf(error, "Không mở được phiên."), () => void loadSession(sessionId));
  } finally {
    if (generation === state.loadGeneration) syncComposer();
  }
}

function newSession() {
  abortFollow();
  clearRunningPoll();
  ++state.loadGeneration;
  state.activeId = "";
  state.detail = null;
  rememberActiveSession("");
  setChatTitle("Phiên trống");
  renderSessions();
  renderEmpty(el.messageList);
  setRunning(false);
  armIdle();
  el.input.focus();
}

function bind() {
  el.newSession.addEventListener("click", newSession);
  el.stop.addEventListener("click", () => void stopTurn());
  el.model.addEventListener("change", async () => {
    try {
      fillComposerControls(await getJson("/api/config"), { keepSelection: true });
    } catch {
      // Keep the current options when the config fetch fails.
    }
  });
  el.messageList.addEventListener("click", (event) => {
    if (!event.target.closest(".again")) return;
    void retryMessage();
  });
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
    if (el.input.value.trim()) clearNudge();
    armIdle();
  });
  el.composer.addEventListener("animationend", (event) => {
    if (event.animationName === "composer-nudge") el.composer.classList.remove("is-nudging");
  });
  el.idleRemember.addEventListener("click", sendIdleRemember);
  el.idleDismiss.addEventListener("click", armIdle);
  el.renameCancel.addEventListener("click", () => el.renameDialog.close());
  el.renameForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitRename();
  });
  el.deleteCancel.addEventListener("click", () => el.deleteDialog.close());
  el.deleteDialog.addEventListener("close", () => {
    state.deleteId = "";
  });
  el.deleteForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitDelete();
  });
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
    fillComposerControls(await getJson("/api/config"));
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
    el.messageList.setAttribute("aria-busy", "false");
    el.input.disabled = true;
    el.send.disabled = true;
  }
}

// Shared wiring for the split modules: entry-owned state plus the functions each module needs.
const hub = {
  state,
  el,
  loadSession,
  newSession,
  setChatTitle,
  sessionKey,
  rememberActiveSession,
  renderDetail,
  scrollToBottom,
  updateToBottom,
  composerBusy,
  setSending,
  setRunning,
  syncComposer,
  armIdle,
};
initSessionsSidebar(hub);
initTurnFollow(hub);
initComposer(hub);

void boot();
