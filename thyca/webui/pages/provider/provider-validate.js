import { el } from "./provider-dom.js";
import { PROVIDER_ID_RE, STANDARD_EFFORTS, state } from "./provider-state.js";

export function positiveNumber(input, label, { min = 0, max = Number.POSITIVE_INFINITY, integer = false } = {}) {
  const value = Number(input.value);
  if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
    // Unbounded callers (prices) pass no max: never print "0–Infinity".
    if (!Number.isFinite(max)) throw new Error(`${label} phải là số lớn hơn hoặc bằng ${min}.`);
    throw new Error(`${label} phải nằm trong khoảng ${min}–${max}.`);
  }
  return value;
}

export function endpointValue() {
  const value = el.endpoint.value.trim().replace(/\/$/, "");
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("Endpoint phải là URL hợp lệ.");
  }
  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    throw new Error("Endpoint phải bắt đầu bằng http:// hoặc https://.");
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
  entry.reasoningEffort = el.providerEffort.value;
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
  const loopMax = positiveNumber(el.loopMax, "Số vòng", { min: 1, max: 200, integer: true });
  const hotTailKB = positiveNumber(el.hotTailKB, "Dung lượng nhớ nóng", { min: 1, max: 64, integer: true });
  const contextTokens = positiveNumber(el.contextTokens, "Cửa sổ ngữ cảnh", { min: 1000, max: 2_000_000, integer: true });
  const priceFields = [el.inputCost, el.cacheCost, el.outputCost];
  const hasAnyPrice = priceFields.some((field) => field.value.trim() !== "");
  if (hasAnyPrice && priceFields.some((field) => field.value.trim() === "")) {
    throw new Error("Điền đủ cả ba giá Input, Cache và Output; hoặc để trống cả ba.");
  }
  const efforts = el.reasoningEfforts.value.split(",").map((level) => level.trim()).filter(Boolean);
  if (new Set(efforts).size !== efforts.length) {
    throw new Error("Thinking map bị lặp mức.");
  }
  if (efforts.length && !efforts.includes(el.reasoning.value)) {
    throw new Error(`Mức suy luận "${el.reasoning.value}" không nằm trong thinking map.`);
  }
  if (!efforts.length && !STANDARD_EFFORTS.includes(el.reasoning.value)) {
    throw new Error(`Thinking map đang trống nên mức suy luận phải là ${STANDARD_EFFORTS.join("/")}, không phải "${el.reasoning.value}".`);
  }
  values.models = { ...(values.models || {}) };
  const previous = values.models[model] || {};
  const entry2 = {
    ...previous,
    provider: previous.provider || pid,
    reasoningEffort: el.reasoning.value,
    loopMax,
    hotTailKB,
    contextTokens,
  };
  if (hasAnyPrice) {
    entry2.input = positiveNumber(el.inputCost, "Chi phí input");
    entry2.cache = positiveNumber(el.cacheCost, "Chi phí cache");
    entry2.output = positiveNumber(el.outputCost, "Chi phí output");
    values.pricing = { ...(values.pricing || {}) };
    values.pricing[model] = { input: entry2.input, cache: entry2.cache, output: entry2.output };
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
