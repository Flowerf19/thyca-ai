import { getJson } from "../../shared/js/http.js";
import {
  JOURNAL_PAGE_SIZE,
  journalPageCount,
  journalClampPage,
  buildPager,
  syncPager,
} from "../../shared/js/pager.js";
import {
  cleanText,
  formatCompact,
  formatCost,
  formatDuration,
  formatInteger,
  statusLabel,
} from "../../shared/js/format.js";
import {
  executionStepsFromDetail,
  finalAssistantText,
  firstUserText,
  formatTraceTimestamp,
} from "./trace-data.js";
import { el, state, stampNode, noteNode } from "./trace-view.js";
import { activeGroup, messageOf, setStatus, syncUrl } from "./trace-deeplink.js";

// Trace turn entries, numbered steps and the per-turn detail lifecycle.
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

export { turnStateFor, turnEntry, fillTurnBody, invalidateTurnDetails };
