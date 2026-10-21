import { getJson } from "../../shared/js/http.js";
import { formatCompact, formatCost, formatInteger } from "../../shared/js/format.js";
import { botLookFor, botMark } from "../../shared/js/bot-avatar.js";
import { tintVarFor } from "./team-data.js";

// The drawer stays read-only; operational data is scoped to the current room.
let hub = null;
let selectedBotId = "";
let currentContext = null;
let operationsNode = null;
let requestSequence = 0;
let controller = null;
let closeBoundTo = null;

function drawer() {
  return document.querySelector("#bot-drawer");
}

function invalidateRequest() {
  requestSequence++;
  controller?.abort();
  controller = null;
  selectedBotId = "";
  currentContext = null;
  operationsNode = null;
}

function closeBotDrawer() {
  invalidateRequest();
  const dialog = drawer();
  if (dialog?.open) dialog.close();
}

function closeButton(dialog) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-button bot-drawer-close";
  button.setAttribute("aria-label", "Đóng hồ sơ bot");
  button.textContent = "✕";
  button.addEventListener("click", closeBotDrawer);
  return button;
}

function textNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = String(text ?? "");
  return node;
}

function validCount(value) {
  return Number.isInteger(value) && value >= 0;
}

function countText(value) {
  return validCount(value) ? formatInteger(value) : "—";
}

function compactCountText(value) {
  return validCount(value) ? formatCompact(value) : "—";
}

function moneyText(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? formatCost(value) : "—";
}

function costText(summary) {
  if (summary?.cost_usd != null) return moneyText(summary.cost_usd);
  const known = summary?.known_cost_usd;
  const missing = validCount(summary?.unpriced_requests)
    ? summary.unpriced_requests : 0;
  if (known != null && missing > 0) {
    return `Đã ghi nhận ${moneyText(known)}; còn ${formatInteger(missing)} request chưa có giá`;
  }
  if (known != null) return moneyText(known);
  if (missing > 0) {
    return `Chưa đủ dữ liệu; còn ${formatInteger(missing)} request chưa có giá`;
  }
  return "Chưa đủ dữ liệu";
}

function row(list, label, value) {
  const term = textNode("dt", "", label);
  const definition = textNode("dd", "", value);
  list.append(term, definition);
}

function block(parent, title) {
  const section = document.createElement("section");
  section.className = "bot-drawer-section bot-ops-block";
  section.append(textNode("h3", "", title));
  parent.append(section);
  return section;
}

function details(parent, entries) {
  const list = document.createElement("dl");
  list.className = "bot-drawer-specs bot-ops-values";
  for (const [label, value] of entries) row(list, label, value);
  parent.append(list);
}

function appendUsage(parent, title, summary, actualModel = "") {
  const section = block(parent, title);
  if (!summary) {
    section.append(textNode("p", "screen-note", "Chưa có lượt model nào được ghi nhận."));
    return;
  }
  const usage = summary.usage || {};
  const rows = [
    ["Request", countText(summary.requests)],
    ["Token vào", countText(usage.prompt_tokens)],
    ["Cache", countText(usage.cached_tokens)],
    ["Token ra", countText(usage.completion_tokens)],
    ["Tổng token", countText(usage.total_tokens)],
    ["Chi phí", costText(summary)],
  ];
  if (actualModel) rows.unshift(["Model đã dùng", actualModel]);
  details(section, rows);
}

function appendContext(parent, context) {
  const section = block(parent, "Ngữ cảnh hiện tại (ước tính)");
  const ratio = context?.ratio;
  const hasRatio = typeof ratio === "number" && Number.isFinite(ratio);
  const percent = hasRatio
    ? `${(ratio * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%`
    : "—";
  details(section, [
    ["Đã ước tính", `${compactCountText(context?.estimated_tokens)} token`],
    ["Transcript", `${countText(context?.transcript_tokens)} token`],
    ["Overhead", `${countText(context?.overhead_tokens)} token`],
    ["Giới hạn", `${countText(context?.context_tokens)} token`],
    ["Tỷ lệ", percent],
    ["Hiệu chỉnh", typeof context?.calibration === "number"
      && Number.isFinite(context.calibration) ? String(context.calibration) : "—"],
  ]);
  if (hasRatio) {
    const progress = document.createElement("progress");
    progress.className = "bot-ops-progress";
    progress.max = 100;
    progress.value = Math.min(Math.max(ratio * 100, 0), 100);
    progress.setAttribute("aria-label", `Tỷ lệ ngữ cảnh ước tính ${percent}`);
    section.append(progress);
  }
  section.append(textNode(
    "p", "screen-note",
    "Ước tính, chưa gồm tin nhắn sắp gửi và recall theo câu hỏi.",
  ));
}

function appendRates(parent, rates) {
  const section = block(parent, "Đơn giá");
  if (rates == null) {
    section.append(textNode("p", "screen-note", "Chưa cấu hình"));
    return;
  }
  const unit = `USD / ${countText(rates.per_tokens)} token`;
  details(section, [
    ["Đầu vào", `${moneyText(rates.input)} · ${unit}`],
    ["Đầu ra", `${moneyText(rates.output)} · ${unit}`],
    ["Cache", `${moneyText(rates.cache)} · ${unit}`],
  ]);
}

function appendTrace(parent, trace) {
  const section = block(parent, "Trace");
  const valid = trace && typeof trace.session_id === "string" && trace.session_id
    && Number.isInteger(trace.turn_index) && trace.turn_index >= 0;
  if (!valid) {
    section.append(textNode("p", "screen-note", "Chưa có lượt Trace để mở."));
    return;
  }
  const link = document.createElement("a");
  link.className = "bot-ops-trace-link";
  link.href = `/dashboard.html?session=${encodeURIComponent(trace.session_id)}`
    + `&turn=${encodeURIComponent(String(trace.turn_index))}#trace`;
  link.textContent = "Mở lượt trong Trace";
  section.append(link);
}

function renderOperations(target, data) {
  target.replaceChildren();
  target.classList.remove("is-status");
  const model = data.model || {};
  const modelBlock = block(target, "Model cho lượt tiếp theo");
  details(modelBlock, [
    ["Model", model.id || "—"],
    ["Provider", model.provider || "—"],
    ["Reasoning effort", model.reasoning_effort || "—"],
  ]);
  appendContext(target, data.context);
  appendUsage(target, "Lượt đã ghi nhận gần nhất", data.last_turn, data.last_turn?.model || "");
  if (data.last_turn && !data.trace) {
    const note = textNode("p", "screen-note", "Chưa có lượt Trace liên kết với room này.");
    target.append(note);
  } else {
    appendTrace(target, data.trace);
  }
  appendUsage(target, "Toàn cuộc trò chuyện", data.conversation);
  appendRates(target, data.rates);
}

function showMessage(target, message, { error = false, retry = false } = {}) {
  target.replaceChildren();
  target.classList.add("is-status");
  const status = document.createElement("div");
  status.className = "bot-ops-status";
  const note = textNode("p", error ? "screen-status is-error" : "screen-note", message);
  if (error) note.setAttribute("role", "alert");
  status.append(note);
  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "screen-button bot-ops-retry";
    button.textContent = "Thử lại";
    button.addEventListener("click", refreshOpenBotOperations);
    status.append(button);
  }
  target.append(status);
}

function captureContext(botId) {
  const state = hub?.state;
  const detail = state?.detail;
  const roomId = String(detail?.id || "");
  const participants = Array.isArray(detail?.bots)
    ? detail.bots.map(String) : ["thyca"];
  if (!roomId || String(state?.activeId || "") !== roomId) {
    return { error: "room" };
  }
  if (!participants.includes(botId)) return { error: "participant" };
  const options = hub?.botOperationOptions?.() || {};
  return {
    botId,
    roomId,
    loadGeneration: state.loadGeneration,
    options: {
      model: typeof options.model === "string" ? options.model : "",
      effort: typeof options.effort === "string" ? options.effort : "",
    },
  };
}

function stillCurrent(sequence, context, dialog) {
  const state = hub?.state;
  return sequence === requestSequence
    && dialog?.open === true
    && selectedBotId === context.botId
    && currentContext?.botId === context.botId
    && currentContext?.roomId === context.roomId
    && context.roomId === String(state?.activeId || "")
    && context.roomId === String(state?.detail?.id || "")
    && context.loadGeneration === state?.loadGeneration;
}

function apiUrl(context) {
  const query = new URLSearchParams();
  if (context.options.model) query.set("model", context.options.model);
  if (context.options.effort) query.set("effort", context.options.effort);
  const suffix = query.size ? `?${query.toString()}` : "";
  return `/api/bots/${encodeURIComponent(context.botId)}`
    + `/rooms/${encodeURIComponent(context.roomId)}/operations${suffix}`;
}

async function loadOperations(context, target) {
  const dialog = drawer();
  const sequence = ++requestSequence;
  controller?.abort();
  const requestController = new AbortController();
  controller = requestController;
  selectedBotId = context.botId;
  currentContext = context;
  operationsNode = target;
  showMessage(target, "Đang tải thông tin vận hành…");
  try {
    const payload = await getJson(apiUrl(context), { signal: requestController.signal });
    if (!stillCurrent(sequence, context, dialog) || target !== operationsNode) return;
    renderOperations(target, payload);
  } catch {
    if (!stillCurrent(sequence, context, dialog) || target !== operationsNode) return;
    showMessage(target, "Không tải được thông tin vận hành.", { error: true, retry: true });
  }
}

function openWithContext(bot, context) {
  const rendered = renderBotDrawer(bot);
  if (!rendered) return;
  selectedBotId = String(bot?.id || "");
  operationsNode = rendered.operations;
  if (!bot) {
    showMessage(operationsNode, "Chưa có hồ sơ bot để xem.");
    return;
  }
  if (context.error === "room") {
    currentContext = null;
    showMessage(operationsNode, "Chọn cuộc trò chuyện để xem thông tin vận hành.");
    return;
  }
  if (context.error === "participant") {
    currentContext = null;
    showMessage(operationsNode, "Bot này không tham gia cuộc trò chuyện hiện tại.");
    return;
  }
  void loadOperations(context, operationsNode);
}

export function renderBotDrawer(bot) {
  const dialog = drawer();
  const body = document.querySelector("#bot-drawer-body");
  if (!dialog || !body) return null;
  const wasOpen = dialog.open;
  body.replaceChildren();
  const data = bot && typeof bot === "object" ? bot : null;
  const head = document.createElement("header");
  head.className = "bot-drawer-head";
  head.style.setProperty("--team-tint", tintVarFor(data?.id));
  if (data) {
    const avatar = document.createElement("span");
    avatar.className = "bot-drawer-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.innerHTML = botMark(data);
    head.append(avatar);
  }
  const heading = document.createElement("div");
  heading.className = "bot-drawer-heading";
  const name = document.createElement("h2");
  name.id = "bot-drawer-name";
  name.textContent = data?.name || data?.id || "Chưa có hồ sơ bot";
  heading.append(name);
  head.append(heading, closeButton(dialog));
  body.append(head);

  const operations = document.createElement("div");
  operations.className = "bot-drawer-operations";
  operations.setAttribute("aria-live", "polite");
  body.append(operations);

  const foot = document.createElement("footer");
  foot.className = "bot-drawer-foot";
  const cta = document.createElement("button");
  cta.type = "button";
  cta.className = "screen-button is-primary";
  cta.textContent = "Nhắn Thyca";
  cta.addEventListener("click", () => {
    closeBotDrawer();
    hub?.messageThycaAbout?.(data?.id || "");
  });
  foot.append(cta, textNode(
    "p", "screen-note", "Muốn đổi gì ở bot này thì nhắn Thyca — hồ sơ ở đây chỉ để đọc.",
  ));
  body.append(foot);

  if (!wasOpen && typeof dialog.showModal === "function") dialog.showModal();
  return { dialog, operations };
}

export function openBotDrawer(botId) {
  const id = String(botId || "");
  const bot = hub?.teamRoster?.().find?.((item) => item.id === id) || null;
  invalidateRequest();
  const context = captureContext(id);
  openWithContext(bot, context);
}

export function refreshOpenBotOperations() {
  const dialog = drawer();
  if (!dialog?.open || !selectedBotId || !operationsNode || !currentContext) return;
  const context = captureContext(selectedBotId);
  if (context.error) {
    closeBotDrawerForNavigation();
    return;
  }
  void loadOperations(context, operationsNode);
}

export function refreshBotDrawerForRoom(roomId) {
  if (!drawer()?.open || !currentContext) return;
  if (currentContext.roomId !== String(roomId || "")) return;
  refreshOpenBotOperations();
}

export function closeBotDrawerForNavigation() {
  closeBotDrawer();
}

export function initTeamDrawer(entryHub) {
  hub = entryHub;
  const dialog = drawer();
  if (dialog && closeBoundTo !== dialog) {
    closeBoundTo = dialog;
    dialog.addEventListener("close", invalidateRequest);
  }
  for (const control of [hub?.el?.model, hub?.el?.effort]) {
    control?.addEventListener("change", refreshOpenBotOperations);
  }
}
