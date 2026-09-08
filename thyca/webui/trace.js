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
  groupTraceTurns,
  selectedModelConfig,
  tokenCost,
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
  previous: document.querySelector("#page-prev"),
  next: document.querySelector("#page-next"),
  toolSection: document.querySelector("#tool-calls"),
  toolSelect: document.querySelector("#tool-select"),
  rawButton: document.querySelector("#raw-json"),
  rawDialog: document.querySelector("#raw-dialog"),
  rawContent: document.querySelector("#raw-content"),
};

const state = {
  groups: [],
  groupIndex: 0,
  turnIndex: 0,
  detail: null,
  config: null,
  tools: [],
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

function pretty(value) {
  return JSON.stringify(value, null, 2);
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
    const name = document.createElement("span");
    name.className = "session-name";
    name.textContent = cleanText(group.title, group.sessionId);
    const meta = document.createElement("time");
    meta.dateTime = group.startedAt;
    meta.textContent = `${group.turns.length} lượt · ${formatDuration(group.latencyMs)}`;
    button.append(name, meta);
    button.addEventListener("click", () => void selectGroup(index));
    return button;
  });
  el.list.replaceChildren(...nodes);
}

function emptyDetail(message) {
  state.detail = null;
  state.tools = [];
  el.detail.hidden = true;
  el.crumb.textContent = `Trace › ${message}`;
  el.copyLabel.textContent = "ID: —";
  el.copy.disabled = true;
}

function providerFor(model) {
  const modelConfig = selectedModelConfig(state.config, model);
  const baseUrl = modelConfig?.baseUrl || state.config?.provider?.baseUrl || "";
  return providerLabel(baseUrl);
}

function rateFor(model) {
  return selectedModelConfig(state.config, model) || {};
}

function parsedOutput(value) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function renderTool(index = 0) {
  const tool = state.tools[index];
  el.toolSection.hidden = !tool;
  if (!tool) return;
  setText("#tool-duration", formatDuration(tool.latencyMs));
  setText("#json-input", pretty({
    call_id: tool.id || null,
    arguments: "Đã ẩn bởi backend",
  }));
  setText("#json-output", pretty(parsedOutput(tool.output)));
}

function renderTools() {
  state.tools = toolsFromDetail(state.detail);
  const options = state.tools.map((tool, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = tool.name;
    return option;
  });
  el.toolSelect.replaceChildren(...options);
  renderTool(0);
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
  setText("#metadata-json", pretty({
    session_id: detail.session_id,
    turn_index: detail.turn_index,
    started_at: detail.started_at,
    ended_at: detail.ended_at,
    requests: detail.requests,
    rounds: detail.rounds,
    input: firstUserText(detail),
    output: finalAssistantText(detail),
  }));
  el.previous.disabled = state.turnIndex === 0;
  el.next.disabled = state.turnIndex >= total - 1;
  renderTools();
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
  el.toolSelect.addEventListener("change", () => renderTool(Number(el.toolSelect.value) || 0));
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
  el.rawButton.addEventListener("click", () => {
    if (!state.detail) return;
    el.rawContent.textContent = pretty(state.detail);
    el.rawDialog.showModal();
  });
  el.rawDialog.addEventListener("close", () => el.rawButton.focus());
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
