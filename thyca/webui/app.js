import { ApiError, deleteJson, getJson, getNdjson, patchJson, postJson, postNdjson } from "./backend/api.js";
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

const IDLE_MS = 15 * 60 * 1000;
const IDLE_REMEMBER = "Hãy nhớ những điều đáng giữ trong phiên này.";
// Fallback if GET /turn/stream is gone (turn just landed, or a blip):
// cheap session GET until the notebook has the reply.
const RUNNING_POLL_MS = 2000;
// A few failed polls in a row (backend restarting, network blip) unlock the
// composer instead of leaving it disabled forever.
const RUNNING_POLL_MAX_FAILURES = 5;
let idleTimer = 0;
let idleFromNudge = false;
let runningTimer = 0;
let followAbort = null;
const idleArmed = new Set();
// session_id -> live card for a turn this tab is streaming. Switching to
// another session detaches the card from the DOM, so keep the object (and the
// events it already shows) and mount it again on the way back.
const liveTurns = new Map();

const state = {
  sessions: [],
  activeId: "",
  detail: null,
  sending: false,
  // Session this tab is currently streaming from — only that session's
  // composer is blocked; any other session stays writable.
  streamSessionId: "",
  running: false,
  loadGeneration: 0,
  deleteId: "",
  // True while a rename/delete request is in flight: one submission at a time.
  saving: false,
};

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function hideIdle() {
  el.idleNudge.hidden = true;
}

// Nudge for an action that cannot say no: a short shake plus a polite hint
// for screen readers (the pill itself stays aria-label-free).
function shakeComposer() {
  el.composer.classList.remove("is-nudging");
  void el.composer.offsetWidth;
  el.composer.classList.add("is-nudging");
  el.status.textContent = "Chưa có nội dung để gửi.";
}

function clearNudge() {
  el.composer.classList.remove("is-nudging");
  if (el.status.textContent) el.status.textContent = "";
}

function sessionKey() {
  return state.activeId || "";
}

function noteSend(sessionId) {
  if (!sessionId) return;
  if (idleFromNudge) idleArmed.delete(sessionId);
  else idleArmed.add(sessionId);
  idleFromNudge = false;
}

async function showIdle() {
  const key = sessionKey();
  if (!key || composerBusy() || !idleArmed.has(key)) return;
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

// Sending blocks the session being sent to, and the session whose turn is
// running — not the whole tab. Another session stays readable *and* writable.
function composerBusy() {
  if (state.running) return true;
  if (!state.sending) return false;
  return !state.streamSessionId || state.streamSessionId === sessionKey();
}

function setSending(sending) {
  state.sending = sending;
  syncComposer();
}

function setRunning(running) {
  state.running = running;
  syncComposer();
}

function abortFollow() {
  if (!followAbort) return;
  followAbort.abort();
  followAbort = null;
}

function syncComposer() {
  const busy = composerBusy();
  el.input.disabled = busy;
  el.send.disabled = busy;
  el.composer.classList.toggle("is-loading", busy);
  el.messageList.setAttribute("aria-busy", String(busy));
}

// Row action: a word, not a glyph. What it says and which row it belongs to
// both live in the label; the look is .session-action in styles.css.
function rowAction(className, label, text, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", onClick);
  return button;
}

function sessionButton(session) {
  // Buttons cannot nest, so the row is a button and the two actions sit next
  // to it inside one positioned wrapper.
  const row = document.createElement("div");
  row.className = "session-row";
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
  time.textContent = sessionMeta(session);
  // Same shape as Hồ sơ / Nhật ký: icon and name on the item. The time line
  // is the shared second row of .session-item, flush right under the name.
  button.append(icon, name, time);
  button.addEventListener("click", () => void loadSession(button.dataset.sessionId));

  const title = cleanText(session.title, "Phiên trống");
  const actions = document.createElement("span");
  actions.className = "session-actions";
  actions.append(
    rowAction(
      "session-action",
      `Đặt tên cho phiên ${title}`,
      "Đổi tên",
      () => openRename(session),
    ),
    rowAction("session-action is-delete", `Xóa phiên ${title}`, "Xóa", () =>
      openDelete(session),
    ),
  );
  row.append(button, actions);
  return row;
}

// Second line of a row: when it happened, then how many turns it holds. The
// turn count reads the same way Trace counts them (one per user message).
function sessionMeta(session) {
  const when = formatSessionTime(session.updated_at);
  const turns = Number(session.turns);
  if (!Number.isFinite(turns) || turns <= 0) return when;
  return `${when} · ${turns} lượt`;
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

// Titles are the user's own text: it is stored as written and returned to the
// row verbatim, so the dialog reopens showing exactly what was saved.
function openRename(session) {
  const id = String(session.id || "");
  if (!id) return;
  el.renameId.value = id;
  el.renameName.value = cleanText(session.title, "");
  el.renameStatus.textContent = "";
  el.renameDialog.showModal();
  el.renameName.select();
}

async function submitRename() {
  // One submission at a time: a double Enter would send the same title twice.
  if (state.saving) return;
  const id = el.renameId.value;
  const title = el.renameName.value.trim();
  if (!id || !title) {
    el.renameStatus.textContent = "Tên phiên không được để trống.";
    return;
  }
  state.saving = true;
  el.renameStatus.textContent = "Đang lưu…";
  try {
    await patchJson(`/api/sessions/${encodeURIComponent(id)}`, { title });
  } catch (error) {
    el.renameStatus.textContent = messageOf(error, "Không đổi được tên phiên.");
    return;
  } finally {
    state.saving = false;
  }
  el.renameDialog.close();
  if (id === state.activeId) setChatTitle(title);
  await refreshSessions();
}

function openDelete(session) {
  const id = String(session.id || "");
  if (!id) return;
  state.deleteId = id;
  el.deleteNote.textContent = `“${cleanText(session.title, "Phiên trống")}” sẽ mất hẳn khỏi sổ. Những trang nhật ký đã ghi từ phiên này vẫn còn.`;
  el.deleteStatus.textContent = "";
  el.deleteDialog.showModal();
}

async function submitDelete() {
  if (state.saving) return;
  const id = state.deleteId;
  if (!id) return;
  state.saving = true;
  el.deleteStatus.textContent = "Đang xóa…";
  try {
    await deleteJson(`/api/sessions/${encodeURIComponent(id)}`);
  } catch (error) {
    el.deleteStatus.textContent = messageOf(error, "Không xóa được phiên.");
    return;
  } finally {
    state.saving = false;
  }
  el.deleteDialog.close();
  if (state.activeId === id) newSession();
  await refreshSessions();
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

function setChatTitle(name) {
  if (el.chatTitle) el.chatTitle.textContent = cleanText(name, "Phiên trống");
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
    // the events it has been collecting are still on it. A turn started
    // elsewhere (or before a reload) gets a fresh one; followTurn then
    // attaches the live stream to that same card.
    const live = liveTurns.get(state.activeId);
    if (live) el.messageList.append(live.article);
    else liveTurns.set(state.activeId, createLiveStatus(el.messageList));
  } else if (state.streamSessionId !== state.activeId) {
    liveTurns.delete(state.activeId);
  }
  renderSessions();
  requestAnimationFrame(() => scrollToBottom("auto"));
}

// Another tab started this turn: replay + tail its NDJSON so the card
// updates the same way the starter's does. Poll is only the fallback.
async function followTurn(sessionId) {
  const controller = new AbortController();
  followAbort = controller;
  let live = liveTurns.get(sessionId);
  if (live) {
    // Replay will rebuild the usage row from the hub log; keep the card.
    live.active.clear();
    live.completed.length = 0;
    live.thinking?.reset();
    live.article.querySelectorAll(".usage-row").forEach((node) => node.remove());
  } else {
    live = createLiveStatus(el.messageList);
    liveTurns.set(sessionId, live);
  }
  try {
    const detail = await getNdjson(
      `/api/sessions/${encodeURIComponent(sessionId)}/turn/stream`,
      (event) => {
        updateLiveStatus(live, event);
        if (state.activeId === sessionId) scrollToBottom();
      },
      { signal: controller.signal },
    );
    if (state.activeId === sessionId) renderDetail(detail);
    await refreshSessions();
    if (state.activeId === sessionId) armIdle();
  } catch (error) {
    if (error?.name === "AbortError") return;
    if (state.activeId !== sessionId) return;
    try {
      const detail = await getJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
      if (state.activeId !== sessionId) return;
      renderDetail(detail);
      if (detail.running === true) watchRunning(sessionId);
      else {
        await refreshSessions();
        armIdle();
      }
    } catch {
      watchRunning(sessionId);
    }
  } finally {
    const replaced = followAbort !== null && followAbort !== controller;
    if (followAbort === controller) followAbort = null;
    if (replaced) return;
    if (state.streamSessionId !== sessionId) liveTurns.delete(sessionId);
  }
}

// Reload mid-turn when follow is unavailable: keep asking until the
// backend says it landed, then render the transcript the turn produced.
function watchRunning(sessionId) {
  window.clearTimeout(runningTimer);
  runningTimer = 0;
  if (!state.running || !sessionId) return;
  const key = sessionId;
  let failures = 0;
  const tick = async () => {
    runningTimer = 0;
    if (state.activeId !== key || state.streamSessionId === key) return;
    let detail = null;
    try {
      detail = await getJson(`/api/sessions/${encodeURIComponent(key)}`);
    } catch {
      // One failed GET must not strand the composer behind a "running" state
      // nobody is watching any more: retry, then give up and unlock.
      failures += 1;
      if (failures >= RUNNING_POLL_MAX_FAILURES) {
        setRunning(false);
        return;
      }
      runningTimer = window.setTimeout(() => void tick(), RUNNING_POLL_MS * failures);
      return;
    }
    failures = 0;
    if (state.activeId !== key) return;
    if (detail && detail.running === true) {
      runningTimer = window.setTimeout(() => void tick(), RUNNING_POLL_MS);
      return;
    }
    renderDetail(detail);
    await refreshSessions();
    armIdle();
  };
  runningTimer = window.setTimeout(() => void tick(), RUNNING_POLL_MS);
}

async function loadSession(sessionId) {
  if (!sessionId) return;
  const generation = ++state.loadGeneration;
  abortFollow();
  window.clearTimeout(runningTimer);
  runningTimer = 0;
  state.activeId = sessionId;
  const preview = state.sessions.find((session) => String(session.id) === sessionId);
  if (preview) setChatTitle(preview.title);
  renderSessions();
  el.messageList.setAttribute("aria-busy", "true");
  try {
    const detail = await getJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (generation !== state.loadGeneration) return;
    renderDetail(detail);
    if (state.running && state.streamSessionId !== sessionId) void followTurn(sessionId);
    else watchRunning(sessionId);
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
  window.clearTimeout(runningTimer);
  runningTimer = 0;
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
    shakeComposer();
    el.input.focus();
    return;
  }
  if (composerBusy()) return;

  clearNudge();
  hideIdle();
  window.clearTimeout(idleTimer);
  setSending(true);
  el.input.value = "";
  let sessionId = "";
  try {
    sessionId = await ensureSession();
    state.streamSessionId = sessionId;
    syncComposer();
    const previous = state.detail?.id === sessionId && Array.isArray(state.detail.messages)
      ? state.detail.messages
      : [];
    const optimistic = [...previous, { role: "user", content: text, ts: new Date().toISOString() }];
    renderConversation(el.messageList, optimistic);
    const live = createLiveStatus(el.messageList);
    liveTurns.set(sessionId, live);
    scrollToBottom();
    const detail = await postNdjson(
      `/api/sessions/${encodeURIComponent(sessionId)}/turn/stream`,
      { text },
      (event) => {
        updateLiveStatus(live, event);
        if (state.activeId === sessionId) scrollToBottom();
      },
    );
    // The user may have switched to another session mid-turn: only the
    // session still on screen gets re-rendered.
    if (state.activeId === sessionId) renderDetail(detail);
    await refreshSessions();
    noteSend(sessionId);
    if (state.activeId === sessionId) armIdle();
  } catch (error) {
    idleFromNudge = false;
    const refused = error instanceof ApiError && error.status === 409;
    if (state.activeId === sessionId) {
      if (refused) {
        // The session is mid-turn: drop the optimistic bubble and follow the
        // turn that is actually running, instead of pretending ours started.
        renderDetail({ ...(state.detail || {}), running: true });
        watchRunning(sessionId);
      } else {
        const live = el.messageList.querySelector(".live-status:last-of-type");
        if (live) setChatBrand(live, { state: "error", status: SEND_ERROR_STATUS });
      }
    }
    // Hand the text back unless the user already started the next message.
    if (!el.input.value) el.input.value = text;
  } finally {
    if (sessionId) liveTurns.delete(sessionId);
    state.streamSessionId = "";
    setSending(false);
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
    if (el.input.value.trim()) clearNudge();
    armIdle();
  });
  el.composer.addEventListener("animationend", (event) => {
    if (event.animationName === "composer-nudge") el.composer.classList.remove("is-nudging");
  });
  el.idleRemember.addEventListener("click", () => {
    hideIdle();
    idleFromNudge = true;
    el.input.value = IDLE_REMEMBER;
    void sendMessage();
  });
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

void boot();
