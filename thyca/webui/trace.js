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
  finalAssistantText,
  firstUserText,
  formatRecordText,
  groupToolCalls,
  groupTraceTurns,
  selectedModelConfig,
  tokenCost,
  toolBatchesFromDetail,
  toolsFromDetail,
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
  toolSection: document.querySelector("#tool-calls"),
  toolList: document.querySelector("#tool-list"),
  turnMetaFold: document.querySelector("#turn-meta-fold"),
  turnMetaBody: document.querySelector("#turn-meta-body"),
};

const state = {
  groups: [],
  groupIndex: 0,
  turnIndex: 0,
  detail: null,
  config: null,
  tools: [],
  toolGroups: [],
  toolBatches: [],
  generation: 0,
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

function emptyDetail(message) {
  state.detail = null;
  state.tools = [];
  state.toolGroups = [];
  state.toolBatches = [];
  el.detail.hidden = true;
  el.crumb.textContent = `Trace › ${message}`;
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

function flowNode(label, value, className = "") {
  const node = document.createElement("details");
  node.className = `trace-flow-node${className ? ` ${className}` : ""}`;
  const summary = document.createElement("summary");
  summary.className = "trace-flow-pill";
  const name = document.createElement("span");
  name.className = "trace-flow-name";
  name.textContent = label;
  summary.append(name);
  const body = document.createElement("div");
  body.className = "trace-flow-body";
  body.textContent = value;
  node.append(summary, body);
  return node;
}

function toolFlowNode(group) {
  const node = document.createElement("details");
  node.className = "trace-flow-node trace-flow-tool";
  const summary = document.createElement("summary");
  summary.className = "trace-flow-pill";
  const name = document.createElement("span");
  name.className = "trace-flow-name";
  name.textContent = `${group.name} ×${group.count}`;
  const stat = document.createElement("span");
  stat.className = "trace-flow-stat";
  stat.textContent = formatDuration(group.latencyMs);
  summary.append(name, stat);
  node.append(summary, toolBody(group));
  return node;
}

function flowGroups(batch) {
  return groupToolCalls(batch);
}

function toolFlow() {
  const flow = document.createElement("div");
  flow.className = "trace-flow";
  for (const [index, batch] of state.toolBatches.entries()) {
    const groups = flowGroups(batch);
    if (index) flow.append(arrow());
    if (groups.length > 1) {
      const parallel = document.createElement("div");
      parallel.className = "trace-parallel";
      parallel.setAttribute("role", "group");
      parallel.setAttribute("aria-label", "Các tool chạy song song");
      const label = document.createElement("span");
      label.className = "trace-parallel-label";
      label.textContent = "song song";
      const nodes = document.createElement("div");
      nodes.className = "trace-parallel-nodes";
      groups.forEach((group) => nodes.append(toolFlowNode(group)));
      parallel.append(label, nodes);
      flow.append(parallel);
    } else if (groups[0]) {
      flow.append(toolFlowNode(groups[0]));
    }
  }
  return flow;
}

function renderRecord() {
  if (!el.turnMetaBody) return;
  const record = document.createElement("div");
  record.className = "trace-record";
  record.append(flowNode("Input", firstUserText(state.detail), "trace-flow-input"));
  if (state.toolBatches.length) record.append(arrow(), toolFlow());
  record.append(arrow(), flowNode("Output", finalAssistantText(state.detail), "trace-flow-output"));
  el.turnMetaBody.replaceChildren(record);
}

// One catalog entry: a single call opens straight to its record, several calls
// fold into one row each so a long turn stays readable.
function toolBody(group) {
  const body = document.createElement("div");
  body.className = "fold-row-body";
  if (group.calls.length === 1) {
    body.append(recordBody(group.calls[0]));
    return body;
  }
  for (const call of group.calls) {
    const fold = document.createElement("details");
    fold.className = "call-fold";
    const summary = document.createElement("summary");
    summary.className = "call-summary";
    summary.textContent = callLabel(call);
    fold.append(summary, recordBody(call));
    body.append(fold);
  }
  return body;
}

function renderTools() {
  state.tools = toolsFromDetail(state.detail);
  state.toolBatches = toolBatchesFromDetail(state.detail);
  state.toolGroups = groupToolCalls(state.tools);
  el.toolSection.hidden = !state.toolGroups.length;
  const flow = toolFlow();
  el.toolList.replaceChildren(flow);
}

function renderDetail() {
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
  renderTools();
  renderRecord();
  if (el.turnMetaFold) el.turnMetaFold.open = false;
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
  renderSidebar();
  await loadTurn();
}

async function selectTurn(index) {
  const last = Math.max(0, (activeGroup()?.turns.length || 1) - 1);
  state.turnIndex = Math.min(Math.max(index, 0), last);
  await loadTurn();
}

function bind() {
  el.previous.addEventListener("click", () => void selectTurn(state.turnIndex - 1));
  el.next.addEventListener("click", () => void selectTurn(state.turnIndex + 1));
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
    return;
  }
  state.groups = groupTraceTurns(traceResult.value.traces);
  renderSidebar();
  if (!state.groups.length) {
    emptyDetail("Chưa có trace nào");
    setStatus("Gửi một tin nhắn trong Chat để tạo trace.");
    return;
  }
  await selectGroup(0);
}

void boot();
