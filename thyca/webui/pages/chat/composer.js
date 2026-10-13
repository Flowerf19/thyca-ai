import { ApiError, getJson, postJson } from "../../shared/js/http.js";
import { postNdjson } from "../../shared/js/streams.js";
import { effortChoicesFor, effortDefaultFor, fillEffortSelect } from "../../shared/js/reasoning-effort.js";
import { sendErrorMessage } from "./chat-status.js";
import { renderConversation } from "./transcript.js";
import { createLiveStatus, updateLiveStatus } from "./live-status.js";
import { setChatBrand } from "./chat-view.js";
import { refreshSessions } from "./sessions-sidebar.js";
import { liveTurns, showTurnDetail, streamingSessions, watchRunning } from "./turn-follow.js";

// Composer: send/stop/retry + idle-nudge + context-nudge. Entry bindings via init; registries from turn-follow.js.
const IDLE_MS = 15 * 60 * 1000;
export const IDLE_REMEMBER = "Hãy nhớ những điều đáng giữ trong phiên này.";
let idleTimer = 0;
let idleFromNudge = false;
const idleArmed = new Set();
// Context hint thresholds mirror the backend guards: shrink starts at 80%,
// auto-compact/backstop at 95%. Warn colors the label, suggest shows the
// chip, urgent rewords it. Ratios come from detail.context (server wire
// estimate), never from a client-side recount.
export const CONTEXT_WARN_RATIO = 0.7;
export const CONTEXT_SUGGEST_RATIO = 0.8;
export const CONTEXT_URGENT_RATIO = 0.9;
const contextDismissed = new Map();
let contextBusy = false;
let contextNote = null;

let composerDeps = null;
export function initComposer(hub) {
  composerDeps = hub;
}
function hideIdle() {
  composerDeps.el.idleNudge.hidden = true;
}

export function hideContext() {
  if (composerDeps?.el.contextNudge) composerDeps.el.contextNudge.hidden = true;
}

function contextRatio(detail) {
  const ratio = detail?.context?.ratio;
  return typeof ratio === "number" && Number.isFinite(ratio) ? ratio : null;
}

function contextPct(ratio) {
  return Math.round(ratio * 100);
}

// Meter + suggestion for one landed detail. Called from renderDetail, so
// loads, turn landings, polls and compact responses all stay in sync.
// Quiet by design: a small label always, a chip only past the suggest
// threshold, and never while a turn is running.
export function syncContext(detail) {
  if (!composerDeps) return;
  const { el } = composerDeps;
  const ratio = contextRatio(detail);
  const sessionId = String(detail?.id || "");
  if (!el.contextMeter || ratio === null || !sessionId) {
    if (el.contextMeter) el.contextMeter.hidden = true;
    hideContext();
    return;
  }
  const pct = contextPct(ratio);
  el.contextMeter.hidden = false;
  el.contextMeter.textContent = `Ngữ cảnh ~${pct}%`;
  const est = Number(detail.context.estimated_tokens || 0);
  const cap = Number(detail.context.context_tokens || 0);
  el.contextMeter.title = `Khoảng ${est.toLocaleString("vi-VN")} / ${cap.toLocaleString("vi-VN")} token (ước lượng)`;
  const calibration = Number(detail.context.calibration || 1);
  if (Number.isFinite(calibration) && calibration !== 1) {
    el.contextMeter.title += ` · đã hiệu chuẩn ×${calibration.toFixed(2)} theo số token thật`;
  }
  el.contextMeter.classList.toggle("is-warn", ratio >= CONTEXT_WARN_RATIO && ratio < CONTEXT_URGENT_RATIO);
  el.contextMeter.classList.toggle("is-urgent", ratio >= CONTEXT_URGENT_RATIO);
  if (!el.contextNudge) return;
  if (contextNote && contextNote.sessionId === sessionId) {
    el.contextNudgeText.textContent = contextNote.text;
    if (el.contextFocus) el.contextFocus.hidden = !contextNote.showCompact;
    el.contextCompact.hidden = !contextNote.showCompact;
    el.contextNew.hidden = !contextNote.showNew;
    el.contextNudge.hidden = false;
    hideIdle();
    return;
  }
  if (el.contextFocus) el.contextFocus.hidden = false;
  el.contextCompact.hidden = false;
  el.contextNew.hidden = false;
  if (detail.running === true || composerDeps.composerBusy() || ratio < CONTEXT_SUGGEST_RATIO) {
    if (ratio < CONTEXT_SUGGEST_RATIO) contextDismissed.delete(sessionId);
    el.contextNudge.hidden = true;
    return;
  }
  const dismissed = contextDismissed.get(sessionId);
  const reshow = dismissed !== undefined && ratio >= dismissed + 0.1;
  const urgentNew = ratio >= CONTEXT_URGENT_RATIO && dismissed !== undefined && dismissed < CONTEXT_URGENT_RATIO;
  if (dismissed !== undefined && !reshow && !urgentNew) {
    el.contextNudge.hidden = true;
    return;
  }
  el.contextNudgeText.textContent = ratio >= CONTEXT_URGENT_RATIO
    ? `Phiên sắp đầy ngữ cảnh (~${pct}%) — sắp tự gọn ở 95%. Gọn ngay?`
    : `Phiên đang dài (~${pct}% ngữ cảnh) — gọn bớt phần cũ?`;
  el.contextNudge.hidden = false;
  hideIdle();
}

export function dismissContext() {
  const detail = composerDeps.state.detail;
  const ratio = contextRatio(detail);
  const key = composerDeps.sessionKey();
  if (key && ratio !== null && !contextNote) contextDismissed.set(key, ratio);
  contextNote = null;
  hideContext();
}

export function newFromContext() {
  contextNote = null;
  if (composerDeps.el.contextFocus) composerDeps.el.contextFocus.value = "";
  hideContext();
  composerDeps.newSession();
}

export async function compactFromContext() {
  if (contextBusy || composerDeps.composerBusy()) return;
  const sessionId = composerDeps.sessionKey();
  if (!sessionId) return;
  const { el } = composerDeps;
  const beforeRatio = contextRatio(composerDeps.state.detail);
  contextBusy = true;
  contextNote = null;
  for (const button of [el.contextCompact, el.contextNew, el.contextDismiss]) {
    if (button) button.disabled = true;
  }
  if (el.contextFocus) el.contextFocus.disabled = true;
  try {
    const body = el.model.value ? { model: el.model.value } : {};
    const focus = (el.contextFocus?.value || "").trim();
    if (focus) body.instructions = focus;
    const detail = await postJson(`/api/sessions/${encodeURIComponent(sessionId)}/compact`, body);
    const afterRatio = contextRatio(detail);
    const done = detail?.compaction?.compacted === true;
    const beforePct = beforeRatio === null ? "?" : contextPct(beforeRatio);
    const afterPct = afterRatio === null ? "?" : contextPct(afterRatio);
    if (done && el.contextFocus) el.contextFocus.value = "";
    contextNote = done
      ? {
          sessionId,
          text: detail.compaction.summary === "llm"
            ? `Đã gọn phiên bằng bản tóm tắt: từ ~${beforePct}% còn ~${afterPct}% (${detail.compaction.messages_before} → ${detail.compaction.messages_after} tin nhắn).`
            : `Đã gọn phiên: từ ~${beforePct}% còn ~${afterPct}% (${detail.compaction.messages_before} → ${detail.compaction.messages_after} tin nhắn).`,
          showCompact: false,
          showNew: false,
        }
      : {
          sessionId,
          text: `Phiên chưa gọn thêm được (~${afterPct}%) — mở phiên mới nếu muốn bắt đầu sạch.`,
          showCompact: false,
          showNew: true,
        };
    contextDismissed.delete(sessionId);
    if (composerDeps.sessionKey() === sessionId) composerDeps.renderDetail(detail);
    await refreshSessions();
  } catch (error) {
    const message = error instanceof Error && error.message.trim() ? error.message.trim() : "thử lại";
    contextNote = {
      sessionId,
      text: `Chưa gọn được — ${message}.`,
      showCompact: true,
      showNew: true,
    };
    if (composerDeps.sessionKey() === sessionId) syncContext(composerDeps.state.detail);
  } finally {
    contextBusy = false;
    for (const button of [el.contextCompact, el.contextNew, el.contextDismiss]) {
      if (button) button.disabled = false;
    }
    if (el.contextFocus) el.contextFocus.disabled = false;
  }
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
  // One nudge at a time, same composer slot: a visible context hint wins.
  if (composerDeps.el.contextNudge && !composerDeps.el.contextNudge.hidden) return;
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
  hideContext();
  contextNote = null;
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
  hideContext();
  contextNote = null;
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
        // Last .live-status in document order: :last-of-type matches by
        // element type, so any later <article> would steal the error brand.
        const lives = el.messageList.querySelectorAll(".live-status");
        const live = lives.length ? lives[lives.length - 1] : null;
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

export function fillComposerControls(payload, { keepSelection = false } = {}) {
  const { el } = composerDeps;
  const values = payload?.values || {};
  const defaultModel = values.defaultModel || "";
  const models = values.models && typeof values.models === "object" ? values.models : {};
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
      group.label = pid;
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
