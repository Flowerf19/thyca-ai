import { getJson } from "../../shared/js/http.js";
import { rollingRange } from "../../shared/js/analytics-data.js";
import {
  JOURNAL_PAGE_SIZE,
  journalPageCount,
  journalClampPage,
  buildPager,
  syncPager,
} from "../../shared/js/pager.js";
import {
  cleanText,
  formatCost,
  formatDuration,
  formatInteger,
} from "../../shared/js/format.js";
import {
  collectTracePages,
  formatTraceTimestamp,
  groupTraceTurns,
  traceTimeFormatter,
} from "./trace-data.js";
import { turnEntry, turnStateFor, invalidateTurnDetails } from "./trace-turns.js";
import {
  syncSessionUrl,
  clearTraceUrl,
  resolveDeepLink,
  settleDeepLinkReveal,
  bind,
  watchTraceViewVisibility,
  showTimezoneWarning,
  setStatus,
  messageOf,
  deepLinkUnavailable,
  updateNote,
  activeGroup,
} from "./trace-deeplink.js";

// Trace session/turn lists, navigation and boot. Turn entries, steps and the
// turn-detail lifecycle live in trace-turns.js; deep-link + URL sync in
// trace-deeplink.js; the shared journal pager in shared/js/pager.js.
// Trace is a view inside dashboard.html (TASK-025): the session list renders
// in the main area, selecting a session turns it into a journal with ONE
// entry per turn in the loaded window — no pager dots, no prev/next turn UI.
const el = {
  status: document.querySelector("#trace-status"),
  period: document.querySelector("#trace-period"),
  note: document.querySelector("#trace-note"),
  picker: document.querySelector("#trace-picker"),
  sessions: document.querySelector("#trace-sessions"),
  session: document.querySelector("#trace-session"),
  sessionTitle: document.querySelector("#trace-session-title"),
  turns: document.querySelector("#trace-turns"),
  back: document.querySelector("#trace-back"),
  copy: document.querySelector("#copy-id"),
  copyLabel: document.querySelector("#copy-label"),
};

const state = {
  groups: [],
  groupIndex: 0,
  config: null,
  time: null,
  generation: 0,
  // Session-list page (TASK-033): the outer list pages 12 sessions at a time
  // and keeps its page across back/session switches.
  sessionPage: 1,
  // Turn-list page per session, keyed by session id: returning to a session
  // restores the page it was on.
  turnPages: new Map(),
  // Pager nodes live on state, never recreated per render: re-appending the
  // same node moves it, so re-renders can never duplicate pagers.
  sessionPager: null,
  turnPager: null,
  // Per-turn view state, keyed `${sessionId}:${turn_index}`: open disclosure,
  // loaded detail, in-flight request, error, current body and steps page. A
  // re-render of the journal rebuilds from here, so open disclosures and
  // pages survive.
  turns: new Map(),
  // Resolved deep link (TASK-027): renderTurns re-asserts open + reveal for
  // the matching turn on every render, so late re-renders can never undo it.
  deepLink: null,
  // The inline note carries two independent warnings; never overwrite one
  // with the other.
  tzWarning: "",
  loadWarning: "",
};

function stampNode(value) {
  const date = document.createElement("span");
  date.className = "journal-date";
  const time = document.createElement("time");
  const stamp = formatTraceTimestamp(value, state.time);
  time.dateTime = cleanText(value) || "";
  time.title = stamp.title;
  time.textContent = stamp.text;
  date.append(time);
  return date;
}

// Session cost label with coverage: a recorded cost is a sum of the
// per-message cost_usd the backend happened to record, so even a turn with a
// non-null cost can be partial within the turn. The label therefore reports
// RECORDED-KNOWN cost and never presents a sum as a guaranteed fully priced
// total: turns without any price keep the explicit partial note, all turns
// unpriced is "chưa định giá", never $0, and a genuine zero stays $0.0000.
function noteNode(text) {
  const note = document.createElement("p");
  note.className = "trace-step-note";
  note.textContent = text;
  return note;
}

function sessionCostLabel(group) {
  const unpriced = group.turns.length - group.pricedTurns;
  if (group.costUsd == null) return "chưa định giá";
  if (unpriced > 0) {
    return `${formatCost(group.costUsd)} (một phần — ${formatInteger(unpriced)} lượt chưa định giá)`;
  }
  return `${formatCost(group.costUsd)} (đã ghi nhận)`;
}

// One session entry of the paged list; `index` is the ABSOLUTE group index
// so selection stays correct on every page.
function sessionEntry(group, index) {
  const item = document.createElement("li");
  item.className = "journal-entry";
  const body = document.createElement("div");
  body.className = "journal-body";
  const heading = document.createElement("h3");
  const open = document.createElement("button");
  open.type = "button";
  open.className = "journal-row-title trace-session-open";
  open.textContent = cleanText(group.title, group.sessionId);
  const selected = index === state.groupIndex;
  open.setAttribute("aria-current", selected ? "page" : "false");
  open.addEventListener("click", () => void selectGroup(index));
  heading.append(open);
  const meta = document.createElement("p");
  meta.className = "journal-meta";
  for (const label of [
    `${group.turns.length} lượt`,
    formatDuration(group.latencyMs),
    sessionCostLabel(group),
  ]) {
    if (!label) continue;
    const span = document.createElement("span");
    span.textContent = label;
    meta.append(span);
  }
  body.append(heading, meta);
  item.append(stampNode(group.startedAt), body);
  return item;
}

// Session list of the loaded window, rendered in the MAIN AREA of the view,
// paged 12 sessions per page (TASK-033). The pager sits OUTSIDE the list
// (sibling right after the <ul>) and is stored on state, so re-renders move —
// never duplicate — it.
function renderSessions() {
  if (!el.sessions) return;
  const pages = journalPageCount(state.groups.length);
  state.sessionPage = journalClampPage(state.sessionPage, pages);
  const start = (state.sessionPage - 1) * JOURNAL_PAGE_SIZE;
  const nodes = state.groups
    .slice(start, start + JOURNAL_PAGE_SIZE)
    .map((group, offset) => sessionEntry(group, start + offset));
  if (!nodes.length) {
    const empty = document.createElement("li");
    empty.className = "screen-note";
    empty.textContent = "Chưa có trace nào.";
    nodes.push(empty);
  }
  el.sessions.replaceChildren(...nodes);
  if (!state.sessionPager) {
    state.sessionPager = buildPager((delta) => {
      // Recompute at click time: the pager node outlives any single pages
      // count (the list is empty when it is first created at boot).
      const total = journalPageCount(state.groups.length);
      state.sessionPage = journalClampPage(state.sessionPage + delta, total);
      renderSessions();
    });
    // Meaningful labels for this pager's landmark and buttons.
    state.sessionPager.nav.setAttribute("aria-label", "Phân trang danh sách phiên");
    state.sessionPager.prev.setAttribute("aria-label", "Trang phiên trước");
    state.sessionPager.next.setAttribute("aria-label", "Trang phiên sau");
  }
  syncPager(state.sessionPager, state.sessionPage, pages);
  el.sessions.after(state.sessionPager.nav);
}

function showSession(selected) {
  if (el.picker) el.picker.hidden = selected;
  if (el.session) el.session.hidden = !selected;
  if (el.back) el.back.hidden = !selected;
}

function renderTurns() {
  const group = activeGroup();
  if (!el.turns || !group) return;
  el.sessionTitle.textContent = cleanText(group.title, group.sessionId);
  el.copy.disabled = false;
  el.copyLabel.textContent = `ID: ${group.sessionId}`;
  // Turn list paging (TASK-033): 12 turns per page, the page kept PER SESSION
  // so back/session switches restore it. Entries are sliced with ABSOLUTE
  // indices, so data-turn-index and the deep-link lookups stay valid on every
  // page. The pager sits OUTSIDE the list (sibling after the <ul>), stored on
  // state so re-renders move — never duplicate — it.
  const pages = journalPageCount(group.turns.length);
  const page = journalClampPage(state.turnPages.get(group.sessionId) || 1, pages);
  state.turnPages.set(group.sessionId, page);
  const start = (page - 1) * JOURNAL_PAGE_SIZE;
  el.turns.replaceChildren(
    ...group.turns
      .slice(start, start + JOURNAL_PAGE_SIZE)
      .map((summary, offset) => turnEntry(group, summary, start + offset)),
  );
  if (!state.turnPager) {
    state.turnPager = buildPager((delta) => {
      // Recompute at click time from the ACTIVE session: one pager node
      // serves every session, so no captured group/pages may go stale.
      const active = activeGroup();
      if (!active) return;
      const total = journalPageCount(active.turns.length);
      const next = journalClampPage((state.turnPages.get(active.sessionId) || 1) + delta, total);
      state.turnPages.set(active.sessionId, next);
      renderTurns();
    });
    // Meaningful labels for this pager's landmark and buttons.
    state.turnPager.nav.setAttribute("aria-label", "Phân trang danh sách lượt");
    state.turnPager.prev.setAttribute("aria-label", "Trang lượt trước");
    state.turnPager.next.setAttribute("aria-label", "Trang lượt sau");
  }
  syncPager(state.turnPager, page, pages);
  el.turns.after(state.turnPager.nav);
  // Deep-link belt-and-suspenders: a later render of the same session (open
  // state already derives from the per-turn state) re-asserts the disclosure
  // and keeps the resolved turn in view.
  if (state.deepLink && state.deepLink.sessionId === group.sessionId) {
    const position = group.turns.findIndex(
      (turn) => Number(turn.turn_index) === state.deepLink.turnIndex,
    );
    if (position !== -1 && turnStateFor(`${group.sessionId}:${group.turns[position].turn_index}`).open) {
      const fold = el.turns.querySelector(`[data-turn-index="${position}"] details.trace-turn-fold`);
      if (fold) fold.open = true;
      settleDeepLinkReveal();
    }
  }
}

async function selectGroup(index) {
  state.groupIndex = Math.min(Math.max(index, 0), Math.max(0, state.groups.length - 1));
  const group = activeGroup();
  showSession(true);
  // Explicit user navigation: the address bar follows the picked session
  // BEFORE rendering (a restored open turn immediately re-adds its turn
  // param), so a refresh or Back matches what is on screen even with a
  // cached turn.
  if (group) syncSessionUrl(group);
  renderSessions();
  renderTurns();
}

function backToSessions() {
  showSession(false);
  renderSessions();
  // Leaving the journal for the picker is explicit navigation too: the
  // session/turn query no longer matches anything on screen.
  clearTraceUrl();
}

// Page through the whole API window (the newest 200 session files) instead of
// stopping at the first 200 turns. collectTracePages dedupes, detects stalls,
// and reports complete=false when the window was not fully read. The period
// "all" option reads the unfiltered window (no from/to) so history older than
// the default rolling ranges stays reachable; the backend still caps it at
// the 200 newest session files.
function loadAllTurns() {
  if (el.period?.value === "all") {
    return collectTracePages(({ limit, offset }) =>
      getJson(`/api/traces?limit=${limit}&offset=${offset}`),
    );
  }
  const range = rollingRange(Number(el.period?.value) || 30);
  return collectTracePages(({ limit, offset }) =>
    getJson(`/api/traces?from=${range.from}&to=${range.to}&limit=${limit}&offset=${offset}`),
  );
}

async function boot() {
  if (!el.sessions) return; // not on dashboard.html
  bind();
  watchTraceViewVisibility();
  showSession(false);
  renderSessions();
  setStatus("Đang đọc trace backend…");
  // An explicit ?session= deep link resolves against the UNFILTERED API
  // window, independently of the default period: otherwise an older session
  // (outside the rolling range) would be reported missing even though the
  // backend window covers it. The load stays bounded by the 200-file cap.
  if (cleanText(new URLSearchParams(location.search).get("session")) && el.period) {
    el.period.value = "all";
  }
  const generation = ++state.generation;
  const [traceResult, configResult] = await Promise.allSettled([
    loadAllTurns(),
    getJson("/api/config"),
  ]);
  if (configResult.status === "fulfilled") state.config = configResult.value?.values || null;
  state.time = traceTimeFormatter(state.config?.timeline?.timezone);
  showTimezoneWarning();
  await applyTraceResult(traceResult, { deepLink: true, generation });
}

// Apply one loaded trace window to the view: shared by boot and the period
// filter, so changing the period re-runs the exact same render path. The
// deep-link resolution only ever happens on boot. `generation` rejects
// out-of-order loads: whichever load started LAST wins, and a stale earlier
// response can never overwrite a newer snapshot.
async function applyTraceResult(traceResult, { deepLink, generation }) {
  if (generation !== state.generation) return;
  state.loadWarning = "";
  updateNote();
  if (traceResult.status === "rejected") {
    renderSessions();
    setStatus(messageOf(traceResult.reason, "Không tải được trace."), "error");
    return;
  }
  const { rows, complete, error } = traceResult.value;
  if (!complete && rows.length) {
    state.loadWarning = `Không tải đủ danh sách trace (${messageOf(error, "lỗi không rõ")}) — chỉ hiển thị phần đã tải.`;
    updateNote();
  }
  state.groups = groupTraceTurns(rows);
  // Accepted snapshot: cached turn details may no longer match these rows and
  // older in-flight detail results are stale; disclosure/page state survives.
  invalidateTurnDetails();
  renderSessions();
  if (!state.groups.length) {
    const requestedSession = deepLink
      ? cleanText(new URLSearchParams(location.search).get("session"))
      : "";
    if (requestedSession) {
      deepLinkUnavailable(
        error
          ? `Không tải được cửa sổ trace để tra phiên ${requestedSession}: ${messageOf(error, "lỗi không rõ")}`
          : `Không tìm thấy phiên ${requestedSession} trong cửa sổ dữ liệu đã tải (200 phiên mới nhất).`,
      );
      return;
    }
    setStatus(
      error ? messageOf(error, "Không tải được trace.") : "Gửi một tin nhắn trong Chat để tạo trace.",
      error ? "error" : "",
    );
    return;
  }
  if (deepLink && (await resolveDeepLink())) return;
  // Plain success (no deep link): the loading status must not linger on the
  // empty #trace-status slot. Error paths above return before this line, so
  // their messages — and the timezone/partial-load notes — are untouched.
  setStatus("");
}

// Re-read the window for the selected period: same render path as boot. The
// generation bump accepts loads strictly in start order — a slow earlier load
// (including a still-running boot) can never overwrite this newer one.
function reloadTrace() {
  const generation = ++state.generation;
  showSession(false);
  renderSessions();
  setStatus("Đang đọc trace backend…");
  return loadAllTurns().then((traceResult) => applyTraceResult(
    { status: "fulfilled", value: traceResult },
    { deepLink: false, generation },
  ));
}

export { el, state, stampNode, noteNode, boot, selectGroup, backToSessions, reloadTrace };
