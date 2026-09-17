import { getJson } from "./backend/api.js";
import {
  cleanText,
  formatCompact,
  formatCost,
  formatDateTime,
  formatDuration,
  formatInteger,
  providerLabel,
  statusLabel,
} from "./backend/format.js";
import {
  activityStepsFromDetail,
  finalAssistantText,
  firstUserText,
  formatRecordText,
  groupTraceTurns,
  selectedModelConfig,
  tokenCost,
} from "./backend/trace-data.js";

const el = {
  list: document.querySelector("#trace-list"),
  content: document.querySelector(".trace-content"),
  detail: document.querySelector("#turn-detail"),
  status: document.querySelector("#trace-status"),
  crumb: document.querySelector("#trace-crumb"),
  copy: document.querySelector("#copy-id"),
  copyLabel: document.querySelector("#copy-label"),
  progress: document.querySelector("#turn-progress"),
  previous: document.querySelector("#page-prev"),
  next: document.querySelector("#page-next"),
  recordFlow: document.querySelector("#record-flow"),
  toolDialog: document.querySelector("#tool-dialog"),
  toolDialogTitle: document.querySelector("#tool-dialog-title"),
  toolDialogMeta: document.querySelector("#tool-dialog-meta"),
  toolDialogBody: document.querySelector("#tool-dialog-body"),
  toolDialogClose: document.querySelector("#tool-dialog-close"),
};

const compact = matchMedia("(max-width: 56rem)");
const state = {
  groups: [],
  groupIndex: 0,
  turnIndex: 0,
  detail: null,
  config: null,
  toolOpener: null,
  generation: 0,
  chosen: false,
};

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = String(value ?? "—");
}

function activeGroup() {
  return state.groups[state.groupIndex] || null;
}

function activeSummary() {
  return activeGroup()?.turns[state.turnIndex] || null;
}

function renderSidebar() {
  if (!state.groups.length) {
    const empty = document.createElement("p");
    empty.className = "sidebar-state";
    empty.textContent = "Chưa có trace nào.";
    el.list.replaceChildren(empty);
    return;
  }
  const nodes = state.groups.map((group, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-item";
    const selected = index === state.groupIndex;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
    if (selected) button.setAttribute("aria-current", "page");
    const icon = document.createElement("span");
    icon.className = "session-icon";
    icon.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "session-name";
    name.textContent = cleanText(group.title, group.sessionId);
    const meta = document.createElement("time");
    meta.dateTime = group.startedAt;
    meta.textContent = `${group.turns.length} lượt · ${formatDuration(group.latencyMs)}`;
    button.append(icon, name, meta);
    button.addEventListener("click", () => void selectGroup(index));
    return button;
  });
  el.list.replaceChildren(...nodes);
}

function focusToolOpener() {
  const opener = state.toolOpener;
  state.toolOpener = null;
  opener?.focus();
}

function closeToolDialog() {
  if (el.toolDialog?.open) {
    el.toolDialog.close();
    return;
  }
  focusToolOpener();
}

function discardToolDialog() {
  state.toolOpener = null;
  if (el.toolDialog?.open) el.toolDialog.close();
}

function syncPicking() {
  const picking = compact.matches && !state.chosen;
  document.querySelector(".trace-shell")?.classList.toggle("is-picking", picking);
}

function emptyDetail(message) {
  discardToolDialog();
  state.detail = null;
  el.detail.hidden = true;
  el.crumb.textContent = `Trace › ${message}`;
  el.recordFlow?.replaceChildren();
  el.copyLabel.textContent = "ID: —";
  el.copy.disabled = true;
  el.progress?.replaceChildren();
}

function renderProgress() {
  const group = activeGroup();
  if (!group || !el.progress) return;
  const dots = group.turns.map((turn, index) => {
    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "trace-dot";
    dot.setAttribute("aria-label", `Mở lượt ${index + 1}`);
    dot.setAttribute("aria-current", String(index === state.turnIndex));
    dot.title = `Lượt ${index + 1}`;
    dot.classList.toggle("is-active", index === state.turnIndex);
    dot.addEventListener("click", () => void selectTurn(index));
    return dot;
  });
  el.progress.replaceChildren(...dots);
}

function providerFor(model) {
  const modelConfig = selectedModelConfig(state.config, model);
  const baseUrl = modelConfig?.baseUrl || state.config?.provider?.baseUrl || "";
  return providerLabel(baseUrl);
}

function rateFor(model) {
  return selectedModelConfig(state.config, model) || {};
}

// Input/output of one call, as the record blocks the trace screen uses.
function recordBody(call) {
  const body = document.createElement("div");
  body.className = "call-body";
  for (const [label, value] of [["Input", call.arguments], ["Output", call.output]]) {
    const block = document.createElement("div");
    block.className = "record-block";
    const title = document.createElement("h3");
    title.textContent = label;
    const text = document.createElement("p");
    text.textContent = formatRecordText(value);
    block.append(title, text);
    body.append(block);
  }
  return body;
}

function callLabel(call) {
  const parts = [`#${call.order}`];
  if (call.id) parts.push(`id ${call.id.slice(0, 8)}`);
  parts.push(formatDuration(call.latencyMs));
  return parts.join(" · ");
}

function arrow() {
  const node = document.createElement("span");
  node.className = "trace-flow-arrow";
  node.textContent = "→";
  node.setAttribute("aria-hidden", "true");
  return node;
}

function textBody(value) {
  const block = document.createElement("div");
  block.className = "record-block";
  const text = document.createElement("p");
  text.textContent = value;
  block.append(text);
  return block;
}

function countedLabel(name, count) {
  return count == null ? name : `${name} ×${count}`;
}

function flowPill(label, extraClass = "") {
  const node = document.createElement("button");
  node.type = "button";
  node.className = `trace-flow-node trace-flow-pill${extraClass ? ` ${extraClass}` : ""}`;
  node.setAttribute("aria-haspopup", "dialog");
  node.setAttribute("aria-controls", "tool-dialog");
  node.title = label;
  const name = document.createElement("span");
  name.className = "trace-flow-name";
  name.textContent = label;
  node.append(name);
  return node;
}

function flowNode(label, value, className = "") {
  const node = flowPill(label, className);
  node.addEventListener("click", () => openFlowDialog({
    title: label,
    body: textBody(value),
    opener: node,
  }));
  return node;
}

function thinkingNode(step) {
  const node = flowPill("thinking", "trace-flow-thinking");
  node.addEventListener("click", () => openFlowDialog({
    title: "thinking",
    meta: formatDuration(step.latencyMs),
    body: textBody(step.content || "—"),
    opener: node,
  }));
  return node;
}

function toolFlowNode(group) {
  const node = flowPill(countedLabel(group.name, group.count), "trace-flow-tool");
  node.addEventListener("click", () => openToolDialog(group, node));
  return node;
}

function parallelFlowNode(groups) {
  const label = groups.map((group) => countedLabel(group.name, group.count)).join(" · ");
  const node = flowPill(label, "trace-flow-tool");
  node.addEventListener("click", () => openFlowDialog({
    title: label,
    meta: groups.map((group) => `${countedLabel(group.name, group.count)} · ${formatDuration(group.latencyMs)}`).join(" · "),
    body: parallelBody(groups),
    opener: node,
  }));
  return node;
}

function parallelBody(groups) {
  const body = document.createElement("div");
  body.className = "trace-tool-dialog-content";
  for (const group of groups) {
    const heading = document.createElement("p");
    heading.className = "trace-tool-call-label";
    heading.textContent = `${countedLabel(group.name, group.count)} · ${formatDuration(group.latencyMs)}`;
    body.append(heading, toolBody(group));
  }
  return body;
}

function activityFlowSteps() {
  return activityStepsFromDetail(state.detail).flatMap((step) => {
    if (step.type === "thinking") return [thinkingNode(step)];
    const groups = step.groups || [];
    if (groups.length > 1) return [parallelFlowNode(groups)];
    return groups[0] ? [toolFlowNode(groups[0])] : [];
  });
}

function flowFromSteps(steps) {
  const flow = document.createElement("div");
  flow.className = "trace-flow";
  steps.forEach((step, index) => {
    const item = document.createElement("span");
    item.className = "trace-flow-step";
    item.append(step);
    if (index < steps.length - 1) item.append(arrow());
    flow.append(item);
  });
  return flow;
}

function renderRecord() {
  if (!el.recordFlow) return;
  const record = document.createElement("div");
  record.className = "trace-record";
  record.append(flowFromSteps([
    flowNode("Input", firstUserText(state.detail), "trace-flow-input"),
    ...activityFlowSteps(),
    flowNode("Output", finalAssistantText(state.detail), "trace-flow-output"),
  ]));
  el.recordFlow.replaceChildren(record);
}

function toolBody(group) {
  const body = document.createElement("div");
  body.className = "trace-tool-dialog-content";
  if (group.calls.length === 1) {
    body.append(recordBody(group.calls[0]));
    return body;
  }
  for (const call of group.calls) {
    const fold = document.createElement("details");
    fold.className = "trace-tool-fold";
    const summary = document.createElement("summary");
    summary.className = "trace-tool-fold-summary";
    summary.textContent = callLabel(call);
    fold.append(summary, recordBody(call));
    body.append(fold);
  }
  return body;
}

function openFlowDialog({ title, meta = "", body, opener }) {
  closeToolDialog();
  state.toolOpener = opener;
  el.toolDialogTitle.textContent = title;
  el.toolDialogMeta.textContent = meta;
  el.toolDialogBody.replaceChildren(body);
  el.toolDialog.showModal();
  el.toolDialogClose.focus();
}

function openToolDialog(group, opener) {
  openFlowDialog({
    title: group.name,
    meta: `${group.count} lần gọi · ${formatDuration(group.latencyMs)}`,
    body: toolBody(group),
    opener,
  });
}

function renderDetail() {
  discardToolDialog();
  const summary = activeSummary();
  const detail = state.detail;
  if (!summary || !detail) return;
  const group = activeGroup();
  const model = cleanText(detail.model, "unknown");
  const rates = rateFor(model);
  const total = group.turns.length;

  el.detail.hidden = false;
  renderProgress();
  el.copy.disabled = false;
  el.crumb.textContent = `Trace › ${cleanText(group.title, group.sessionId)}`;
  el.copyLabel.textContent = `ID: ${group.sessionId}`;
  setText("#detail-title", `Lượt ${Number(summary.turn_index) + 1}`);
  setText("#detail-type", "TURN");
  setText("#detail-time", formatDateTime(detail.started_at));
  setText("#detail-model", model);
  setText("#detail-provider", providerFor(model));
  setText("#detail-image", formatInteger(detail.rounds));
  setText("#detail-status", statusLabel(detail.status));
  setText("#detail-duration", formatDuration(detail.latency_ms));
  setText("#detail-cost", formatCost(detail.cost_usd));
  const promptTokens = Number(detail.prompt_tokens) || 0;
  const cacheTokens = Math.min(Math.max(Number(detail.cached_tokens) || 0, 0), Math.max(promptTokens, 0));
  const inputTokens = Math.max(promptTokens - cacheTokens, 0);
  setText("#input-count", `${formatInteger(inputTokens)} tokens`);
  setText("#output-count", `${formatInteger(detail.completion_tokens)} tokens`);
  setText("#cache-count", `${formatInteger(cacheTokens)} tokens`);
  setText("#input-cost-line", formatCost(tokenCost(inputTokens, rates.input)));
  setText("#output-cost-line", formatCost(tokenCost(detail.completion_tokens, rates.output)));
  setText("#cache-cost-line", formatCost(tokenCost(cacheTokens, rates.cache)));
  setText("#page-status", `${state.turnIndex + 1} / ${total}`);
  el.previous.disabled = state.turnIndex === 0;
  el.next.disabled = state.turnIndex >= total - 1;
  renderRecord();
  el.content.scrollTop = 0;
}

async function loadTurn() {
  const summary = activeSummary();
  if (!summary) {
    emptyDetail("Chưa có dữ liệu");
    return;
  }
  const generation = ++state.generation;
  setStatus("Đang tải chi tiết lượt…");
  el.detail.setAttribute("aria-busy", "true");
  try {
    const detail = await getJson(
      `/api/traces/${encodeURIComponent(summary.session_id)}/${encodeURIComponent(summary.turn_index)}`,
    );
    if (generation !== state.generation) return;
    state.detail = detail;
    renderDetail();
    setStatus(`${formatCompact(detail.total_tokens)} token trong lượt này`, "success");
  } catch (error) {
    if (generation !== state.generation) return;
    emptyDetail("Không mở được lượt");
    setStatus(messageOf(error, "Không tải được trace."), "error");
  } finally {
    if (generation === state.generation) el.detail.setAttribute("aria-busy", "false");
  }
}

async function selectGroup(index) {
  state.groupIndex = Math.min(Math.max(index, 0), Math.max(0, state.groups.length - 1));
  state.turnIndex = 0;
  state.chosen = true;
  syncPicking();
  renderSidebar();
  await loadTurn();
}

function backToSessions() {
  state.chosen = false;
  syncPicking();
}

async function selectTurn(index) {
  const last = Math.max(0, (activeGroup()?.turns.length || 1) - 1);
  state.turnIndex = Math.min(Math.max(index, 0), last);
  await loadTurn();
}

function bind() {
  document.querySelector("#trace-back")?.addEventListener("click", () => backToSessions());
  compact.addEventListener("change", () => {
    if (!compact.matches && state.groups.length && !state.chosen) void selectGroup(0);
    else syncPicking();
  });
  el.previous.addEventListener("click", () => void selectTurn(state.turnIndex - 1));
  el.next.addEventListener("click", () => void selectTurn(state.turnIndex + 1));
  el.toolDialogClose.addEventListener("click", () => closeToolDialog());
  el.toolDialog.addEventListener("click", (event) => {
    if (event.target === el.toolDialog) closeToolDialog();
  });
  el.toolDialog.addEventListener("close", () => focusToolOpener());
  el.copy.addEventListener("click", async () => {
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
  bind();
  syncPicking();
  emptyDetail("Đang tải…");
  setStatus("Đang đọc trace backend…");
  const [traceResult, configResult] = await Promise.allSettled([
    getJson("/api/traces?limit=200"),
    getJson("/api/config"),
  ]);
  if (configResult.status === "fulfilled") state.config = configResult.value.values || null;
  if (traceResult.status === "rejected") {
    renderSidebar();
    emptyDetail("Trace chưa sẵn sàng");
    setStatus(messageOf(traceResult.reason, "Không tải được trace."), "error");
    syncPicking();
    return;
  }
  state.groups = groupTraceTurns(traceResult.value.traces);
  renderSidebar();
  if (!state.groups.length) {
    emptyDetail("Chưa có trace nào");
    setStatus("Gửi một tin nhắn trong Chat để tạo trace.");
    syncPicking();
    return;
  }
  if (compact.matches) {
    state.chosen = false;
    emptyDetail("Chọn một phiên");
    setStatus("Chọn phiên để xem trace.");
    syncPicking();
    return;
  }
  await selectGroup(0);
}

void boot();
