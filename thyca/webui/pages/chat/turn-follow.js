import { ApiError, getJson, postJson } from "../../shared/js/http.js";
import { getNdjson } from "../../shared/js/streams.js";
import { createLiveStatus, resetLiveStatus, updateLiveStatus } from "./live-status.js";
import { refreshSessions } from "./sessions-sidebar.js";

// Live-turn ownership plus keeping the transcript pinned to the stream:
// every streamed event scrolls the session on screen (follow behavior),
// so the scroll helpers live here too. Registries live here (not in the
// entry): two turns can stream at once, and finishing one must not orphan
// the other's reader. Entry-owned bindings arrive via init (followHub) and
// via init (followHub) — never by importing app.js back.
const RUNNING_POLL_MS = 2000;
const RUNNING_POLL_MAX_FAILURES = 5;
let runningTimer = 0;
let followAbort = null;
// session_id -> live card for a turn this tab is streaming. Switching to
// another session detaches the card from the DOM, so keep the object (and the
// events it already shows) and mount it again on the way back.
export const liveTurns = new Map();
// Sessions this tab is streaming a turn from. A Set, not one id: two turns
// can stream at once (send in A, switch to B, send there), and finishing B
// must not orphan A's reader.
export const streamingSessions = new Set();

let followHub = null;

export function initTurnFollow(hub) {
  followHub = hub;
}

export function scrollToBottom(behavior = "smooth") {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  followHub.el.scroll.scrollTo({
    top: followHub.el.scroll.scrollHeight,
    behavior: reduced ? "auto" : behavior,
  });
  updateToBottom();
}

export function updateToBottom() {
  const { el } = followHub;
  const distance = el.scroll.scrollHeight - el.scroll.scrollTop - el.scroll.clientHeight;
  const scrollable = el.scroll.scrollHeight > el.scroll.clientHeight + 1;
  el.toBottom.hidden = !scrollable || distance <= 120;
}

export function abortFollow() {
  if (!followAbort) return;
  followAbort.abort();
  followAbort = null;
}

export function clearRunningPoll() {
  window.clearTimeout(runningTimer);
  runningTimer = 0;
}

export async function showTurnDetail(sessionId, detail) {
  if (followHub.state.activeId !== sessionId) return;
  if (!detail) detail = await getJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (followHub.state.activeId !== sessionId) return;
  followHub.renderDetail(detail);
}

// Another tab started this turn: replay + tail its NDJSON so the card
// updates the same way the starter's does. Poll is only the fallback.
export async function followTurn(sessionId, startedAt) {
  const controller = new AbortController();
  followAbort = controller;
  let polling = false;
  let live = liveTurns.get(sessionId);
  if (live) {
    // Replay rebuilds usage rows and segments from the hub log (reset also
    // clears the per-round tool record), so the replay does not duplicate
    // notes an earlier attempt added.
    resetLiveStatus(live, startedAt);
    live.article.querySelectorAll(".usage-row").forEach((node) => node.remove());
  } else {
    live = createLiveStatus(followHub.el.messageList, startedAt);
    liveTurns.set(sessionId, live);
  }
  try {
    const detail = await getNdjson(
      `/api/sessions/${encodeURIComponent(sessionId)}/turn/stream`,
      (event) => {
        updateLiveStatus(live, event);
        if (followHub.state.activeId === sessionId) followHub.scrollToBottom();
      },
      { signal: controller.signal },
    );
    if (followHub.state.activeId === sessionId) await showTurnDetail(sessionId, detail);
    await refreshSessions();
    if (followHub.state.activeId === sessionId) followHub.armIdle();
  } catch (error) {
    if (error?.name === "AbortError") return;
    if (followHub.state.activeId !== sessionId) return;
    try {
      const detail = await getJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
      if (followHub.state.activeId !== sessionId) return;
      if (detail.running === true) {
        renderPolledProgress(detail);
        polling = true;
        watchRunning(sessionId);
      } else {
        followHub.renderDetail(detail);
        await refreshSessions();
        followHub.armIdle();
      }
    } catch {
      polling = true;
      watchRunning(sessionId);
    }
  } finally {
    const replaced = followAbort !== null && followAbort !== controller;
    if (followAbort === controller) followAbort = null;
    if (replaced) return;
    // Polling still owns the mounted card and its clock. Don't orphan it
    // just because the stream failed; renderDetail reuses it on each poll.
    if (!polling && !streamingSessions.has(sessionId)) liveTurns.delete(sessionId);
  }
}

// Keep the transcript, selection, collapsed panels and scroll position intact.
// Only reasoning appended since the last full render belongs in the live card.
export function renderPolledProgress(detail) {
  const live = liveTurns.get(detail.id);
  if (!live || (live.startedAt && live.startedAt !== detail.started_at)) {
    followHub.renderDetail(detail);
    return;
  }
  const rendered = followHub.state.detail?.id === detail.id ? (followHub.state.detail.messages?.length || 0) : 0;
  const reasoning = (detail.messages || []).slice(rendered)
    .filter((message) => message.role === "assistant" && typeof message.reasoning === "string")
    .map((message) => message.reasoning).filter(Boolean).join("\n\n");
  if (reasoning) live.thinking?.sync(reasoning);
}

// Follow unavailable: show persisted progress without waiting for Stop.
export function watchRunning(sessionId) {
  window.clearTimeout(runningTimer);
  runningTimer = 0;
  if (!followHub.state.running || !sessionId) return;
  const key = sessionId;
  let failures = 0;
  const tick = async () => {
    runningTimer = 0;
    if (followHub.state.activeId !== key || streamingSessions.has(key)) return;
    let detail = null;
    try {
      detail = await getJson(`/api/sessions/${encodeURIComponent(key)}`);
    } catch {
      // One failed GET must not strand the composer behind a "running" state
      // nobody is watching any more: retry, then give up and unlock.
      failures += 1;
      if (failures >= RUNNING_POLL_MAX_FAILURES) {
        followHub.setRunning(false);
        return;
      }
      runningTimer = window.setTimeout(() => void tick(), RUNNING_POLL_MS * failures);
      return;
    }
    failures = 0;
    if (followHub.state.activeId !== key) return;
    if (detail && detail.running === true) {
      renderPolledProgress(detail);
      runningTimer = window.setTimeout(() => void tick(), RUNNING_POLL_MS);
      return;
    }
    followHub.renderDetail(detail);
    await refreshSessions();
    followHub.armIdle();
  };
  runningTimer = window.setTimeout(() => void tick(), RUNNING_POLL_MS);
}

export async function stopTurn() {
  const { composerBusy, sessionKey } = followHub;
  if (!composerBusy()) return;
  // Prefer the turn in the session on screen; else any turn this tab is
  // streaming; else the current session (a turn another tab started).
  const current = sessionKey();
  const sessionId = streamingSessions.has(current)
    ? current
    : [...streamingSessions][0] || current;
  if (!sessionId) return;
  try {
    await postJson(`/api/sessions/${encodeURIComponent(sessionId)}/turn/cancel`, {});
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) return;
  }
}
