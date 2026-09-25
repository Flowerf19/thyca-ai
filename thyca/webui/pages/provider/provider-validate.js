import { el, isEffortTouched, markField, selectedReasoningEffort, setStatus } from "./provider-dom.js";
import { PROVIDER_ID_RE, STANDARD_EFFORTS, state } from "./provider-state.js";

// Lỗi gắn với một input: UI khoanh đỏ + focus đúng trường gây lỗi.
export class FieldError extends Error {
  constructor(message, fieldId = "") {
    super(message);
    this.name = "FieldError";
    this.fieldId = fieldId;
  }
}

export function positiveNumber(input, label, { min = 0, max = Number.POSITIVE_INFINITY, integer = false } = {}) {
  const value = Number(input.value);
  if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
    // Unbounded callers (prices) pass no max: never print "0–Infinity".
    if (!Number.isFinite(max)) throw new FieldError(`${label} phải là số lớn hơn hoặc bằng ${min}.`, input.id || "");
    throw new FieldError(`${label} phải nằm trong khoảng ${min}–${max}.`, input.id || "");
  }
  return value;
}

export function endpointValue() {
  const value = el.endpoint.value.trim().replace(/\/$/, "");
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new FieldError("Endpoint phải là URL hợp lệ.", "provider-endpoint");
  }
  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    throw new FieldError("Endpoint phải bắt đầu bằng http:// hoặc https://.", "provider-endpoint");
  }
  return value;
}

export function askProviderId(title, initial = "") {
  const raw = window.prompt(title, initial);
  if (raw === null) return null;
  const id = raw.trim();
  if (!id) throw new Error("Tên provider không được để trống.");
  if (!PROVIDER_ID_RE.test(id)) throw new Error("Tên provider chỉ gồm chữ, số, _ và -.");
  return id;
}

// Ghi các field đang hiện vào state.values (chưa POST). Ném Error khi invalid.
export function applyFormToState(values) {
  const pid = state.activeProvider;
  const entry = values.providers?.[pid];
  if (!entry) throw new Error("Chưa có provider nào — bấm Thêm trước.");
  entry.baseUrl = endpointValue();
  // entry.reasoningEffort intentionally untouched: the level belongs to the
  // model; the stored provider value is preserved as the inherit anchor.
  entry.api = el.providerApi.value === "openai_responses" ? "openai_responses" : "openai_chat";
  const typedKey = el.apiKey.value.trim();
  if (typedKey) entry.apiKey = typedKey;
  // apiKey để trống nghĩa là giữ key đã lưu: backend merge theo id.

  values.limits = {
    ...(values.limits || {}),
    loopMax: positiveNumber(el.limitsLoopMax, "Giới hạn chung: số vòng", { min: 1, max: 200, integer: true }),
    hotTailKB: positiveNumber(el.limitsHotTailKB, "Giới hạn chung: nhớ nóng", { min: 1, max: 64, integer: true }),
    contextTokens: positiveNumber(el.limitsContextTokens, "Giới hạn chung: ngữ cảnh", { min: 1000, max: 2_000_000, integer: true }),
    softTimeoutS: positiveNumber(el.limitsSoftTimeoutS, "Giới hạn chung: soft timeout", { min: 1, max: 300, integer: true }),
  };

  const model = el.model.value.trim();
  if (!model) return values;
  return applyModelFormToState(values, model);
}

// Ba ô giá: để trống cả ba = dùng giá backend, ngược lại phải đủ 3 số.
function parsePriceFields() {
  const fields = [el.inputCost, el.cacheCost, el.outputCost];
  const raws = fields.map((field) => field.value.trim());
  if (raws.every((raw) => raw === "")) return null;
  const emptyIndex = raws.findIndex((raw) => raw === "");
  if (emptyIndex >= 0) {
    throw new FieldError("Điền đủ cả ba giá Input, Cache và Output; hoặc để trống cả ba.", fields[emptyIndex].id);
  }
  const nums = raws.map(Number);
  const badIndex = nums.findIndex((n) => !Number.isFinite(n) || n < 0);
  if (badIndex >= 0) {
    throw new FieldError("Giá phải là số lớn hơn hoặc bằng 0.", fields[badIndex].id);
  }
  const [input, cache, output] = nums;
  return { input, cache, output };
}

// Lint khi rời ô: khoanh đỏ + dòng nhắc nhẹ, không cướp focus.
export function lintPrices() {
  try {
    parsePriceFields();
  } catch (error) {
    if (error instanceof FieldError) markField(error.fieldId);
    setStatus(error instanceof Error ? error.message : "Giá chưa đúng.", "");
    return false;
  }
  for (const field of [el.inputCost, el.cacheCost, el.outputCost]) field.removeAttribute("aria-invalid");
  return true;
}

export function lintContextWindow() {
  try {
    positiveNumber(el.contextTokens, "Cửa sổ ngữ cảnh", { min: 1000, max: 2_000_000, integer: true });
  } catch (error) {
    markField("context-window");
    setStatus(error instanceof Error ? error.message : "Ngữ cảnh chưa đúng.", "");
    return false;
  }
  el.contextTokens.removeAttribute("aria-invalid");
  return true;
}

// Ghi các field model đang hiện vào state.values (dùng chung cho Lưu và Tạo
// model). Ném FieldError khi invalid.
export function applyModelFormToState(values, model) {
  const pid = state.activeProvider;
  const loopMax = positiveNumber(el.loopMax, "Số vòng", { min: 1, max: 200, integer: true });
  const hotTailKB = positiveNumber(el.hotTailKB, "Dung lượng nhớ nóng", { min: 1, max: 64, integer: true });
  const contextTokens = positiveNumber(el.contextTokens, "Cửa sổ ngữ cảnh", { min: 1000, max: 2_000_000, integer: true });
  const prices = parsePriceFields();
  const efforts = el.reasoningEfforts.value.split(",").map((level) => level.trim()).filter(Boolean);
  if (new Set(efforts).size !== efforts.length) {
    throw new FieldError("Thinking map bị lặp mức.", "reasoning-efforts");
  }
  // Blank (Mặc định) pins no level: no match check, key omitted on save.
  // A custom pin always lands in the map at commit time, so mismatch here
  // means state drifted — still guarded.
  const effort = selectedReasoningEffort();
  if (effort && efforts.length && !efforts.includes(effort)) {
    throw new FieldError(`Mức suy luận "${effort}" không nằm trong thinking map.`, "reasoning-effort");
  }
  if (effort && !efforts.length && !STANDARD_EFFORTS.includes(effort)) {
    throw new FieldError(`Thinking map đang trống nên mức suy luận phải là ${STANDARD_EFFORTS.join("/")}, không phải "${effort}".`, "reasoning-effort");
  }
  values.models = { ...(values.models || {}) };
  const previous = values.models[model] || {};
  const entry2 = {
    ...previous,
    provider: previous.provider || pid,
    loopMax,
    hotTailKB,
    contextTokens,
  };
  if (effort) entry2.reasoningEffort = effort;
  // Untouched + blank display keeps the stored value (no silent migration
  // when a standard level normalizes to Mặc định on render).
  else if (isEffortTouched()) delete entry2.reasoningEffort;
  if (prices) {
    entry2.input = prices.input;
    entry2.cache = prices.cache;
    entry2.output = prices.output;
    values.pricing = { ...(values.pricing || {}) };
    values.pricing[model] = { input: prices.input, cache: prices.cache, output: prices.output };
  } else {
    delete entry2.input;
    delete entry2.cache;
    delete entry2.output;
    // Same as deleteModel: a price-less model must not keep a pricing mirror.
    if (values.pricing) delete values.pricing[model];
  }
  if (efforts.length) entry2.reasoningEfforts = efforts;
  else delete entry2.reasoningEfforts;
  values.models[model] = entry2;
  return values;
}
