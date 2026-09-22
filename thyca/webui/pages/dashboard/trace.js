import { getJson } from "../../shared/js/api.js";
import { rollingRange } from "../../shared/js/analytics-data.js";
import {
  cleanText,
  formatCompact,
  formatCost,
  formatDuration,
  formatInteger,
  statusLabel,
} from "../../shared/js/format.js";
import {
  collectTracePages,
  executionStepsFromDetail,
  finalAssistantText,
  firstUserText,
  formatTraceTimestamp,
  groupTraceTurns,
  traceTimeFormatter,
} from "./trace-data.js";

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

// >>> journal-pager (pure math; extracted by unit tests, no DOM here)
const JOURNAL_PAGE_SIZE = 12;
function journalPageCount(totalItems, perPage = JOURNAL_PAGE_SIZE) {
  return Math.max(1, Math.ceil(totalItems / perPage));
}
function journalClampPage(page, pages) {
  return Math.min(Math.max(page, 1), pages);
}
// <<< journal-pager

function buildPager(onStep) {
  const nav = document.createElement("nav");
  nav.className = "journal-pager";
  nav.hidden = true;
  const prev = document.createElement("button");
  prev.type = "button";
  prev.className = "screen-button journal-pager-step";
  prev.textContent = "‹ Trước";
  prev.setAttribute("aria-label", "Trang trước");
  const label = document.createElement("span");
  label.className = "journal-pager-label";
  label.setAttribute("aria-live", "polite");
  const next = document.createElement("button");
  next.type = "button";
  next.className = "screen-button journal-pager-step";
  next.textContent = "Sau ›";
  next.setAttribute("aria-label", "Trang sau");
  prev.addEventListener("click", () => onStep(-1));
  next.addEventListener("click", () => onStep(1));
  nav.append(prev, label, next);
  return { nav, prev, next, label };
}

function syncPager(pager, page, pages) {
  pager.nav.hidden = pages <= 1;
  pager.prev.disabled = page <= 1;
  pager.next.disabled = page >= pages;
  pager.label.textContent = `${page} / ${pages}`;
}

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

function turnStateFor(key) {
  let turnState = state.turns.get(key);
  if (!turnState) {
    turnState = {
      open: false,
      detail: null,
      pending: null,
      error: "",
      // Live body of this turn's disclosure: a detail arriving after a
      // re-render (list paging, session switch) refreshes THIS node, never a
      // detached one.
      body: null,
      stepsPage: 1,
      // Open state of the per-step payload disclosures, keyed ABSOLUTE step
      // index — survives steps paging and re-renders of the turn.
      stepOpen: new Set(),
      // Bumped on every snapshot refresh: a detail fetch started before the
      // refresh must not write its stale result into the refreshed state.
      detailToken: 0,
    };
    state.turns.set(key, turnState);
  }
  return turnState;
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

// One journal entry per turn of the loaded window: time gutter, title,
// status chip, description and meta, plus ONE disclosure per turn.
function turnEntry(group, summary, position) {
  const item = document.createElement("li");
  item.className = "journal-entry trace-turn";
  item.dataset.turnIndex = String(position);

  const body = document.createElement("div");
  body.className = "journal-body";

  const heading = document.createElement("h3");
  const title = document.createElement("span");
  title.className = "trace-turn-title";
  title.textContent = `Lượt ${Number(summary.turn_index) + 1}`;
  heading.append(title);
  const status = document.createElement("span");
  status.className = `journal-status${summary.status === "completed" ? "" : " is-error"}`;
  status.textContent = statusLabel(summary.status);
  if (summary.status === "completed") status.textContent = `✓ ${status.textContent}`;
  else if (summary.status === "failed") status.textContent = `! ${status.textContent}`;
  heading.append(status);

  const description = document.createElement("p");
  description.className = "trace-turn-description";
  description.textContent = cleanText(summary.model, "—");

  let errorLine = null;
  if (summary.error && typeof summary.error.message === "string" && summary.error.message) {
    errorLine = document.createElement("p");
    errorLine.className = "trace-turn-error";
    errorLine.textContent = summary.error.message;
  }

  const meta = document.createElement("p");
  meta.className = "journal-meta";
  for (const label of [
    formatDuration(summary.latency_ms),
    `${formatInteger(summary.total_tokens)} token`,
    summary.cost_usd == null ? null : formatCost(summary.cost_usd),
  ]) {
    if (!label) continue;
    const span = document.createElement("span");
    span.textContent = label;
    meta.append(span);
  }

  const key = `${group.sessionId}:${summary.turn_index}`;
  const turnState = turnStateFor(key);
  const fold = document.createElement("details");
  fold.className = "trace-turn-fold";
  fold.open = turnState.open;
  const summaryLine = document.createElement("summary");
  summaryLine.textContent = "Xem các bước & dữ liệu";
  const foldBody = document.createElement("div");
  foldBody.className = "trace-turn-body";
  fold.append(summaryLine, foldBody);
  const fill = () => void fillTurnBody(group, summary, turnState, foldBody);
  if (turnState.open) fill();
  fold.addEventListener("toggle", () => {
    turnState.open = fold.open;
    if (fold.open) fill();
  });

  body.append(heading, description, ...(errorLine ? [errorLine] : []), meta, fold);
  item.append(stampNode(summary.started_at), body);
  return item;
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

function stepTitle(step) {
  if (step.type === "input") return "Tin nhắn";
  if (step.type === "thinking") return "Suy nghĩ";
  if (step.type === "output") return "Phản hồi";
  return step.skill || step.name;
}

// Payload text for one code block: strings render verbatim — including a
// literal "—" and the empty string; everything else (arguments object,
// numeric/boolean/null tool output) via JSON.stringify so nested null, false,
// 0 and HTML survive as data, never as markup.
function payloadCode(value) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

// One labelled code block inside the step disclosure. value == null with a
// missingText renders the explicit placeholder (a missing output must not
// pose as an empty one); value == null without one drops the whole section.
function payloadSection(label, value, missingText) {
  if (value == null && missingText == null) return null;
  const labelNode = document.createElement("p");
  labelNode.className = "trace-io-label";
  labelNode.textContent = label;
  const pre = document.createElement("pre");
  pre.className = "trace-code";
  pre.textContent = value == null ? missingText : payloadCode(value);
  const wrap = document.createElement("div");
  wrap.className = "trace-step-io";
  wrap.append(labelNode, pre);
  return wrap;
}

// Small native <details> under each step: the compact line stays, the recorded
// payload is inspectable on demand. Open state persists on the turn state keyed
// by the absolute step index, so paging and re-renders never collapse it.
function stepDataFold(step, index, turnState) {
  const fold = document.createElement("details");
  fold.className = "trace-step-data";
  fold.open = turnState.stepOpen.has(index);
  const summary = document.createElement("summary");
  summary.textContent = "Xem dữ liệu bước";
  fold.append(summary);
  const sections = step.type === "tool"
    ? [
      payloadSection("Input", step.arguments),
      // null output = no result message recorded, never faked as empty/zero.
      payloadSection("Output", step.output, "Chưa ghi nhận output"),
      step.parseError ? payloadSection("Lỗi cú pháp", step.parseError) : null,
    ]
    : [
      payloadSection(
        step.type === "input" ? "Input" : step.type === "output" ? "Output" : "Nội dung",
        step.content,
      ),
    ];
  const body = document.createElement("div");
  body.replaceChildren(...sections.filter(Boolean));
  fold.append(body);
  fold.addEventListener("toggle", () => {
    if (fold.open) turnState.stepOpen.add(index);
    else turnState.stepOpen.delete(index);
  });
  return fold;
}

// Numbered step "01 — Tên bước" with a meta line (duration · detail); tool
// rows indent under the round that called them (CSS .is-nested). Errors are
// muted red via the shared .journal-status.is-error kit.
function stepEntry(step, index, turnState) {
  const item = document.createElement("li");
  item.className = "trace-step";
  if (step.type === "tool") item.classList.add("is-nested");

  const line = document.createElement("div");
  line.className = "trace-step-line";
  const num = document.createElement("span");
  num.className = "trace-step-num";
  num.textContent = String(index + 1).padStart(2, "0");
  const title = document.createElement("span");
  title.className = "trace-step-title";
  title.textContent = stepTitle(step);
  line.append(num, title);

  if (step.type === "tool") {
    if (step.parseError) {
      const status = document.createElement("span");
      status.className = "journal-status is-error";
      status.textContent = "! Lỗi cú pháp";
      line.append(status);
    } else if (step.isError) {
      // meta.is_error is written by the agent loop for a failed call.
      const status = document.createElement("span");
      status.className = "journal-status is-error";
      status.textContent = "✗ Thực thi lỗi";
      line.append(status);
    }
  }

  const meta = document.createElement("div");
  meta.className = "journal-meta trace-step-meta";
  for (const label of [
    // Recorded wall-clock time of the step (raw clock when the configured
    // zone could not be applied) — a diagnostic the redesign had dropped.
    step.ts ? formatTraceTimestamp(step.ts, state.time).text : null,
    formatDuration(step.latencyMs),
    step.type === "tool" && step.skill && step.skill !== step.name ? `tool: ${step.name}` : null,
  ]) {
    if (!label) continue;
    const span = document.createElement("span");
    span.textContent = label;
    meta.append(span);
  }

  item.append(line, meta, stepDataFold(step, index, turnState));
  return item;
}

// ONE code block area at the end of the disclosure with labelled Input /
// Output sections (the turn's prompt in, final reply out), textContent only.
function ioBlock(detail) {
  const wrap = document.createElement("div");
  wrap.className = "trace-io";
  for (const [label, text] of [
    ["Input", firstUserText(detail)],
    ["Output", finalAssistantText(detail)],
  ]) {
    const labelNode = document.createElement("p");
    labelNode.className = "trace-io-label";
    labelNode.textContent = label;
    const pre = document.createElement("pre");
    pre.className = "trace-code";
    pre.textContent = text;
    wrap.append(labelNode, pre);
  }
  return wrap;
}

function renderStepsInto(turnState, body) {
  const detail = turnState.detail;
  body.replaceChildren();
  if (!detail) {
    const note = document.createElement("p");
    note.className = "trace-step-note";
    note.textContent = turnState.error || "Đang tải các bước & dữ liệu…";
    body.append(note);
    return;
  }
  const diag = turnDiagnostics(detail);
  if (diag) body.append(diag);
  const steps = executionStepsFromDetail(detail);
  const pages = journalPageCount(steps.length);
  turnState.stepsPage = journalClampPage(turnState.stepsPage, pages);
  const start = (turnState.stepsPage - 1) * JOURNAL_PAGE_SIZE;
  const list = document.createElement("ol");
  list.className = "trace-steps";
  // Slice with absolute indices: the step number printed by stepEntry is
  // index + 1 of the WHOLE turn, so numbering continues across pages.
  const nodes = steps
    .slice(start, start + JOURNAL_PAGE_SIZE)
    .map((step, offset) => stepEntry(step, start + offset, turnState));
  if (!nodes.length) {
    const empty = document.createElement("li");
    empty.className = "trace-steps-empty";
    empty.textContent = "Không có bước nào được ghi trong trace này.";
    nodes.push(empty);
  }
  list.replaceChildren(...nodes);
  body.append(list);
  // ONE pager per turn, kept on the turn state: appending the same nav node
  // moves it, so re-renders can never accumulate .journal-pager nodes inside
  // one disclosure. The step count is fixed per turn (detail is cached), so
  // the captured `pages` stays correct for the pager's lifetime.
  turnState.pagerBody = body;
  if (!turnState.pager) {
    turnState.pager = buildPager((delta) => {
      turnState.stepsPage = journalClampPage(turnState.stepsPage + delta, pages);
      renderStepsInto(turnState, turnState.pagerBody);
    });
  }
  syncPager(turnState.pager, turnState.stepsPage, pages);
  body.append(turnState.pager.nav);
  body.append(ioBlock(detail));
}

// Compact per-turn diagnostics the redesign had dropped, restored at the top
// of the existing disclosure: the recorded input/cache/output token split and
// the assistant round count. Missing values are skipped, never faked as 0.
function turnDiagnostics(detail) {
  const parts = [
    ["Vào", detail.prompt_tokens],
    ["Cache", detail.cached_tokens],
    ["Ra", detail.completion_tokens],
  ]
    .filter(([, value]) => Number.isFinite(Number(value)))
    .map(([label, value]) => `${label}: ${formatInteger(Number(value))}`);
  const rounds = Number(detail.rounds);
  if (Number.isFinite(rounds) && rounds > 0) parts.push(`${formatInteger(rounds)} vòng`);
  if (!parts.length) return null;
  const line = document.createElement("p");
  line.className = "journal-meta trace-turn-diag";
  line.textContent = parts.join(" · ");
  return line;
}

// The detail of one turn loads the first time its disclosure opens and is
// then cached on the turn state, so re-renders and re-opens stay cheap.
async function ensureDetail(group, summary, turnState) {
  if (turnState.detail || turnState.pending) return;
  const token = (turnState.detailToken += 1);
  turnState.pending = (async () => {
    try {
      const detail = await getJson(
        `/api/traces/${encodeURIComponent(group.sessionId)}/${encodeURIComponent(summary.turn_index)}`,
      );
      if (token !== turnState.detailToken) return; // snapshot refreshed mid-flight
      turnState.detail = detail;
      turnState.error = "";
    } catch (error) {
      if (token !== turnState.detailToken) return;
      turnState.error = messageOf(error, "Không tải được các bước của lượt này.");
    } finally {
      if (token === turnState.detailToken) turnState.pending = null;
    }
  })();
  await turnState.pending;
}

// A fresh list snapshot was accepted: cached details may no longer match the
// rows on screen and older in-flight detail results are stale. Drop the
// caches (an in-flight load is orphaned via detailToken) while keeping the
// disclosure/page state — a re-opened turn simply refetches.
function invalidateTurnDetails() {
  for (const turnState of state.turns.values()) {
    turnState.detailToken += 1; // orphan any in-flight detail result
    turnState.detail = null;
    turnState.pending = null;
    turnState.error = "";
  }
}

async function fillTurnBody(group, summary, turnState, body) {
  turnState.body = body;
  let fresh = false;
  if (!turnState.detail && !turnState.pending) {
    fresh = true;
    body.replaceChildren();
    body.append(noteNode("Đang tải các bước & dữ liệu…"));
    await ensureDetail(group, summary, turnState);
    // The turn may have been re-rendered while the detail was in flight
    // (turn paging, session switch): the arrival refreshes THIS body if it
    // is still connected, and a detached body is never touched.
    const live = turnState.body?.isConnected ? turnState.body : null;
    if (!live) return;
    renderStepsInto(turnState, live);
  } else {
    // Cached detail (turn reopened): render immediately — and fall through
    // to the same global side effects as a fresh load, so the address bar
    // follows the reopened turn too.
    renderStepsInto(turnState, body);
  }
  // Status and URL are GLOBAL side effects, so isConnected is not enough:
  // a hidden-but-connected body (dashboard switched to Cost, or the user
  // went back to the session picker) is still connected. A late arrival
  // must neither write a turn status into the picker nor overwrite the
  // URL of another view — only speak when this turn is the context on
  // screen: #trace view visible, the session section visible, and this
  // turn's session still the active one.
  const view = document.querySelector("#trace");
  if (view?.hidden || el.session?.hidden || activeGroup()?.sessionId !== group.sessionId) return;
  if (fresh) {
    if (turnState.error) setStatus(turnState.error, "error");
    else {
      const tokens = Number(turnState.detail?.total_tokens);
      if (Number.isFinite(tokens)) setStatus(`${formatCompact(tokens)} token trong lượt này`, "success");
    }
  }
  syncUrl(group, summary);
}

function noteNode(text) {
  const note = document.createElement("p");
  note.className = "trace-step-note";
  note.textContent = text;
  return note;
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

void boot();
