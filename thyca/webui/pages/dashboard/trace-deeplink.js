import {
  JOURNAL_PAGE_SIZE,
  journalPageCount,
  journalClampPage,
} from "../../shared/js/pager.js";
import { cleanText } from "../../shared/js/format.js";
import { el, state, selectGroup, backToSessions, reloadTrace } from "./trace-view.js";
import { turnStateFor, fillTurnBody } from "./trace-turns.js";

// Trace deep-link (?session=&turn=), address-bar sync and reveal.
// Deep-link reveal honors prefers-reduced-motion (same pattern as app.js).
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

function revealScrollOptions() {
  return { block: "start", behavior: reducedMotion ? "auto" : "smooth" };
}

// A display:none ancestor (dashboard.js switches the #trace view in parallel
// with this boot) collapses the rect to 0×0 and turns scrollIntoView into a
// silent no-op — the reveal must then be retried once the view is visible.
function isRendered(entry) {
  const rect = entry.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function isInViewport(entry) {
  const rect = entry.getBoundingClientRect();
  return rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight;
}

// Resolves the pending deep link to its rendered turn entry, or null while
// the session/turn is missing from the current render.
function deepLinkEntry() {
  if (!state.deepLink) return null;
  const group = state.groups.find((item) => item.sessionId === state.deepLink.sessionId);
  if (!group) return null;
  const position = group.turns.findIndex(
    (turn) => Number(turn.turn_index) === state.deepLink.turnIndex,
  );
  if (position === -1) return null;
  return el.turns?.querySelector(`[data-turn-index="${position}"]`) || null;
}

// rAF monitor: smooth scrolling animates for a few hundred ms (and another
// view switch may reset the scroll surface), so the viewport check is polled
// for a bounded budget and the scroll re-asserted until the turn really
// shows up. If the entry is not rendered (hidden view), polling stops and
// the #trace visibility watcher retries later.
let revealMonitor = 0;

function watchReveal(entry) {
  if (revealMonitor) cancelAnimationFrame(revealMonitor);
  let frames = 120; // ~2s at 60fps
  const tick = () => {
    revealMonitor = 0;
    if (!state.deepLink) return;
    frames -= 1;
    if (isInViewport(entry)) {
      state.deepLink = null; // confirmed: later renders stop re-asserting
      return;
    }
    if (frames > 0 && isRendered(entry)) {
      entry.scrollIntoView(revealScrollOptions());
      revealMonitor = requestAnimationFrame(tick);
    }
  };
  revealMonitor = requestAnimationFrame(tick);
}

// Attempts the pending deep-link reveal. Safe to call any time (renders,
// view switches): it no-ops without a pending deep link or when the turn is
// not rendered yet, and state.deepLink survives until the entry is verified
// inside the viewport.
function settleDeepLinkReveal() {
  const entry = deepLinkEntry();
  if (!entry) return;
  entry.scrollIntoView(revealScrollOptions());
  watchReveal(entry);
}

// #trace visibility watcher: dashboard.js toggles [hidden] on the view when
// switching, in parallel with this boot. When the Trace view becomes
// visible, retry a pending deep-link reveal that was a no-op inside the
// hidden subtree.
function watchTraceViewVisibility() {
  const view = document.querySelector("#trace");
  if (!view || typeof MutationObserver === "undefined") return;
  new MutationObserver(() => {
    if (!view.hidden) settleDeepLinkReveal();
  }).observe(view, { attributes: true, attributeFilter: ["hidden"] });
}

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  if (!el.status) return;
  el.status.textContent = message;
  el.status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function setNote(message = "") {
  if (!el.note) return;
  el.note.hidden = !message;
  el.note.textContent = message;
}

function updateNote() {
  setNote([state.tzWarning, state.loadWarning].filter(Boolean).join(" "));
}

function activeGroup() {
  return state.groups[state.groupIndex] || null;
}

// Keep the address bar in sync with the open turn via replaceState: refresh
// reopens the same context and Back returns to the calling page instead of
// replaying every disclosure.
function syncUrl(group, summary) {
  const params = new URLSearchParams(location.search);
  params.set("session", group.sessionId);
  params.set("turn", String(summary.turn_index));
  history.replaceState(null, "", `${location.pathname}?${params}#trace`);
}

// Session picked without (or before) a turn: keep the session on the URL and
// drop the stale turn param until one is actually opened.
function syncSessionUrl(group) {
  const params = new URLSearchParams(location.search);
  params.set("session", group.sessionId);
  params.delete("turn");
  history.replaceState(null, "", `${location.pathname}?${params}#trace`);
}

// Back at the session picker nothing below ?session remains on screen: drop
// both deep-link params so the URL cannot silently reopen a turn.
function clearTraceUrl() {
  const params = new URLSearchParams(location.search);
  if (!params.has("session") && !params.has("turn")) return;
  params.delete("session");
  params.delete("turn");
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}#trace`);
}

// An explicit deep link that cannot be resolved must be reported, never
// silently replaced by another session or turn.
function deepLinkUnavailable(message) {
  setStatus(message, "error");
}

// dashboard.html?session=<id>&turn=<turn_index>#trace; without turn the
// newest turn of the session opens. The resolved turn scrolls into view with
// its disclosure opened. The full snapshot is already loaded at this point
// (boot), so the session/turn PAGES are selected BEFORE rendering and the
// reveal always targets a rendered entry (TASK-033).
async function resolveDeepLink() {
  const params = new URLSearchParams(location.search);
  const sessionId = cleanText(params.get("session"));
  if (!sessionId) return false;
  const groupIndex = state.groups.findIndex((group) => group.sessionId === sessionId);
  if (groupIndex === -1) {
    deepLinkUnavailable(
      `Không tìm thấy phiên ${sessionId} trong cửa sổ dữ liệu đã tải (200 phiên mới nhất).`,
    );
    return true;
  }
  const group = state.groups[groupIndex];
  const rawTurn = params.get("turn");
  let position = -1;
  if (rawTurn == null || rawTurn === "") {
    position = group.turns.length - 1;
  } else if (/^\d+$/.test(rawTurn)) {
    position = group.turns.findIndex((turn) => Number(turn.turn_index) === Number(rawTurn));
  }
  if (position !== -1) {
    state.sessionPage = journalClampPage(
      Math.floor(groupIndex / JOURNAL_PAGE_SIZE) + 1,
      journalPageCount(state.groups.length),
    );
    state.turnPages.set(group.sessionId, Math.floor(position / JOURNAL_PAGE_SIZE) + 1);
  }
  // selectGroup syncs the picked session into the URL; a deep link that then
  // fails to resolve has its ORIGINAL URL restored so the failed link stays
  // inspectable on refresh (reported, never silently replaced).
  const deepLinkUrl = `${location.pathname}${location.search}#trace`;
  await selectGroup(groupIndex);
  if (rawTurn != null && rawTurn !== "" && !/^\d+$/.test(rawTurn)) {
    deepLinkUnavailable(`Tham số turn “${rawTurn}” không phải số lượt hợp lệ.`);
    history.replaceState(null, "", deepLinkUrl);
    return true;
  }
  if (position === -1) {
    deepLinkUnavailable(`Lượt ${Number(rawTurn)} không có trong dữ liệu đã tải của phiên ${sessionId}.`);
    history.replaceState(null, "", deepLinkUrl);
    return true;
  }
  const wanted = Number(group.turns[position].turn_index);
  state.deepLink = { sessionId: group.sessionId, turnIndex: wanted };
  await openTurn(group, position);
  return true;
}

async function openTurn(group, position) {
  const summary = group.turns[position];
  if (!summary) return;
  const key = `${group.sessionId}:${summary.turn_index}`;
  const turnState = turnStateFor(key);
  turnState.open = true;
  const node = el.turns?.querySelector(`[data-turn-index="${position}"] details.trace-turn-fold`);
  if (node) {
    node.open = true;
    settleDeepLinkReveal();
    return; // the toggle handler fills the body and the URL
  }
  // No DOM yet (should not happen after renderTurns): fill directly.
  const body = el.turns?.querySelector(`[data-turn-index="${position}"] .trace-turn-body`);
  if (body) {
    await fillTurnBody(group, summary, turnState, body);
    settleDeepLinkReveal();
  }
}

// /api/config carries values.timeline.timezone; when it is missing or not a
// valid IANA zone, say so instead of quietly rendering browser-local time.
function showTimezoneWarning() {
  if (state.time) {
    state.tzWarning = "";
  } else {
    const zone = cleanText(state.config?.timeline?.timezone);
    state.tzWarning = zone
      ? `Múi giờ cấu hình “${zone}” không hợp lệ — giờ hiển thị theo dữ liệu gốc, chưa quy đổi múi giờ.`
      : "Không đọc được múi giờ từ cấu hình — giờ hiển thị theo dữ liệu gốc, chưa quy đổi múi giờ.";
  }
  updateNote();
}

function bind() {
  el.back?.addEventListener("click", () => backToSessions());
  el.period?.addEventListener("change", () => void reloadTrace());
  el.copy?.addEventListener("click", async () => {
    const id = activeGroup()?.sessionId;
    if (!id) return;
    try {
      await navigator.clipboard.writeText(id);
      el.copyLabel.textContent = "Đã sao chép ID";
      setTimeout(() => {
        if (activeGroup()?.sessionId === id) el.copyLabel.textContent = `ID: ${id}`;
      }, 1400);
    } catch {
      el.copyLabel.textContent = `ID: ${id} (không thể sao chép)`;
    }
  });
}

export {
  messageOf,
  setStatus,
  updateNote,
  activeGroup,
  syncUrl,
  syncSessionUrl,
  clearTraceUrl,
  deepLinkUnavailable,
  resolveDeepLink,
  settleDeepLinkReveal,
  watchTraceViewVisibility,
  showTimezoneWarning,
  bind,
};
