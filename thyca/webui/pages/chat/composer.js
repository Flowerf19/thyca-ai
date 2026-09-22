import { ApiError, getJson, postJson } from "../../shared/js/http.js";
import { postNdjson } from "../../shared/js/streams.js";
import { effortChoicesFor, effortDefaultFor, fillEffortSelect } from "../../shared/js/reasoning-effort.js";
import { sendErrorMessage } from "./chat-status.js";
import { renderConversation } from "./transcript.js";
import { createLiveStatus, updateLiveStatus } from "./live-status.js";
import { setChatBrand } from "./chat-view.js";
import { refreshSessions } from "./sessions-sidebar.js";
import { liveTurns, showTurnDetail, streamingSessions, watchRunning } from "./turn-follow.js";

// Composer: send/stop/retry + idle-nudge. Entry bindings via init; registries from turn-follow.js.
const IDLE_MS = 15 * 60 * 1000;
export const IDLE_REMEMBER = "Hãy nhớ những điều đáng giữ trong phiên này.";
let idleTimer = 0;
let idleFromNudge = false;
const idleArmed = new Set();

let composerDeps = null;
export function initComposer(hub) {
  composerDeps = hub;
}
function hideIdle() {
  composerDeps.el.idleNudge.hidden = true;
}

// Nudge for an action that cannot say no: a short shake plus a polite hint
// for screen readers (the pill itself stays aria-label-free).
function shakeComposer() {
  const { el } = composerDeps;
  el.composer.classList.remove("is-nudging");
  void el.composer.offsetWidth;
  el.composer.classList.add("is-nudging");
  el.status.textContent = "Chưa có nội dung để gửi.";
}
export function clearNudge() {
  const { el } = composerDeps;
  el.composer.classList.remove("is-nudging");
  if (el.status.textContent) el.status.textContent = "";
}
function noteSend(sessionId) {
  if (!sessionId) return;
  if (idleFromNudge) idleArmed.delete(sessionId);
  else idleArmed.add(sessionId);
  idleFromNudge = false;
}
async function showIdle() {
  const key = composerDeps.sessionKey();
  if (!key || composerDeps.composerBusy() || !idleArmed.has(key)) return;
  try {
    const detail = await getJson(`/api/sessions/${encodeURIComponent(key)}`);
    if (composerDeps.sessionKey() !== key || !idleArmed.has(key) || detail?.ask_remember !== true) return;
    composerDeps.el.idleNudge.hidden = false;
  } catch {
    // An idle hint is optional and must not disrupt chat.
  }
}
export function armIdle() {
  hideIdle();
  window.clearTimeout(idleTimer);
  idleTimer = 0;
  if (!idleArmed.has(composerDeps.sessionKey())) return;
  idleTimer = window.setTimeout(() => void showIdle(), IDLE_MS);
}
async function ensureSession() {
  if (composerDeps.state.activeId) return composerDeps.state.activeId;
  const detail = await postJson("/api/sessions", {});
  composerDeps.state.activeId = String(detail.id || "");
  composerDeps.state.detail = detail;
  composerDeps.rememberActiveSession(composerDeps.state.activeId);
  return composerDeps.state.activeId;
}
function composerTurn(extra) {
  const body = { ...extra };
  if (composerDeps.el.model.value) body.model = composerDeps.el.model.value;
  if (composerDeps.el.effort.value) body.effort = composerDeps.el.effort.value;
  return body;
}

export async function sendMessage() {
  const { state, el, composerBusy } = composerDeps;
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
  el.input.value = "";
  const done = await runTurn({ text });
  if (done) {
    const { sessionId, pageAtSend } = done;
    // Reveal the row only if the user is still where the turn left them.
    // Stays per send path: both paths guard the reveal.
    await refreshSessions(
      state.activeId === sessionId && state.sessionPage === pageAtSend ? sessionId : "",
    );
    noteSend(sessionId);
    if (state.activeId === sessionId) armIdle();
  }
}
export async function retryMessage() {
  const { state } = composerDeps;
  if (composerDeps.composerBusy()) return;

  clearNudge();
  hideIdle();
  window.clearTimeout(idleTimer);
  const done = await runTurn({ text: null });
  if (done) {
    const { sessionId, pageAtSend } = done;
    // Same reveal rule as a fresh send.
    await refreshSessions(
      state.activeId === sessionId && state.sessionPage === pageAtSend ? sessionId : "",
    );
    noteSend(sessionId);
    if (state.activeId === sessionId) armIdle();
  }
}
// Shared live-turn lifecycle for send and retry (they were ~50 duplicated
// lines): mount the card, stream the turn, settle the transcript; follow or
// brand the error; always release the registries. Returns the settlement for
// the caller, which owns the sidebar reveal guard — or null on failure.
async function runTurn({ text }) {
  // Unpacked to keep the pre-split names the webui tests pin by substring.
  const { state, el, scrollToBottom } = composerDeps;
  composerDeps.setSending(true);
  let sessionId = "";
  const pageAtSend = state.sessionPage; // sidebar page the user chose
  try {
    sessionId = await ensureSession();
    streamingSessions.add(sessionId);
    composerDeps.syncComposer();
    if (text !== null) {
      const previous = state.detail?.id === sessionId && Array.isArray(state.detail.messages)
        ? state.detail.messages
        : [];
      const optimistic = [...previous, { role: "user", content: text, ts: new Date().toISOString() }];
      renderConversation(el.messageList, optimistic);
    }
    const live = createLiveStatus(el.messageList);
    liveTurns.set(sessionId, live);
    scrollToBottom();
    const detail = await postNdjson(
      `/api/sessions/${encodeURIComponent(sessionId)}/turn/stream`,
      text === null ? composerTurn({ retry: true }) : composerTurn({ text }),
      (event) => {
        updateLiveStatus(live, event);
        if (state.activeId === sessionId) scrollToBottom();
      },
    );
    // The user may have switched to another session mid-turn: only the
    // session still on screen gets re-rendered.
    if (state.activeId === sessionId) await showTurnDetail(sessionId, detail);
    return { sessionId, pageAtSend };
  } catch (error) {
    idleFromNudge = false;
    const refused = error instanceof ApiError && error.status === 409;
    if (state.activeId === sessionId) {
      if (refused) {
        // The session is mid-turn: drop the optimistic bubble and follow the
        // turn that is actually running, instead of pretending ours started.
        composerDeps.renderDetail({ ...(state.detail || {}), running: true });
        watchRunning(sessionId);
      } else {
        const live = el.messageList.querySelector(".live-status:last-of-type");
        if (live) setChatBrand(live, { state: "error", status: sendErrorMessage(error) });
      }
    }
    // Hand the text back unless the user already started the next message.
    if (text !== null && !el.input.value) el.input.value = text;
    return null;
  } finally {
    if (sessionId) {
      liveTurns.delete(sessionId);
      streamingSessions.delete(sessionId);
    }
    composerDeps.setSending(false);
    el.input.focus();
    composerDeps.updateToBottom();
  }
}
export function sendIdleRemember() {
  hideIdle();
  idleFromNudge = true;
  composerDeps.el.input.value = IDLE_REMEMBER;
  void sendMessage();
}

function hostOf(baseUrl) {
  try { return new URL(String(baseUrl || "")).host; } catch { return ""; }
}

export function fillComposerControls(payload, { keepSelection = false } = {}) {
  const { el } = composerDeps;
  const values = payload?.values || {};
  const defaultModel = values.defaultModel || "";
  const models = values.models && typeof values.models === "object" ? values.models : {};
  const providers = values.providers && typeof values.providers === "object" ? values.providers : {};
  const catalog = [...new Set([defaultModel, ...Object.keys(models)].filter((id) => typeof id === "string" && id))];
  const providerOf = (id) => models[id]?.provider || values.defaultProvider || "default";
  const groups = new Map();
  for (const id of catalog) {
    const pid = providerOf(id);
    if (!groups.has(pid)) groups.set(pid, []);
    groups.get(pid).push(id);
  }
  const current = el.model.value;
  el.model.replaceChildren(
    ...[...groups.entries()].map(([pid, ids]) => {
      const group = document.createElement("optgroup");
      const host = hostOf(providers[pid]?.baseUrl);
      group.label = host ? `${pid} — ${host}` : pid;
      for (const id of ids) {
        const option = document.createElement("option");
        option.value = id;
        option.textContent = id;
        group.append(option);
      }
      return group;
    }),
  );
  if (!catalog.length) {
    const option = document.createElement("option");
    option.value = "";
    option.disabled = true;
    option.selected = true;
    option.textContent = "Model";
    el.model.append(option);
  } else if (keepSelection && current && catalog.includes(current)) {
    // A per-turn override survives re-fetch; defaultModel is only the initial value.
    el.model.value = current;
  } else if (defaultModel) {
    el.model.value = defaultModel;
  }
  const schema = payload?.schema;
  fillEffortSelect(
    el.effort,
    effortChoicesFor(schema, values, el.model.value),
    effortDefaultFor(values, el.model.value),
    schema,
  );
}
