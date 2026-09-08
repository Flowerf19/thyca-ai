import { getJson, postJson } from "./backend/api.js";

const PRESETS = {
  openai: "https://api.openai.com/v1",
  openrouter: "https://openrouter.ai/api/v1",
  local: "http://127.0.0.1:11434/v1",
  custom: "",
};

const el = {
  form: document.querySelector("#provider-form"),
  provider: document.querySelector("#provider"),
  endpoint: document.querySelector("#provider-endpoint"),
  apiKey: document.querySelector("#provider-key"),
  model: document.querySelector("#model"),
  modelOptions: document.querySelector("#model-options"),
  modelBadge: document.querySelector("#model-badge"),
  modelNote: document.querySelector("#model-note-text"),
  reasoning: document.querySelector("#reasoning-effort"),
  loopMax: document.querySelector("#loop-max"),
  hotTailKB: document.querySelector("#hot-tail-kb"),
  contextTokens: document.querySelector("#context-window"),
  inputCost: document.querySelector("#input-cost"),
  cacheCost: document.querySelector("#cache-input-cost"),
  outputCost: document.querySelector("#output-cost"),
  status: document.querySelector("#provider-status"),
  verify: document.querySelector("#verify-provider"),
  save: document.querySelector("#save-provider"),
  reset: document.querySelector(".settings-reset"),
};

const state = {
  values: null,
  saved: null,
  meta: {},
  verifiedModels: [],
  busy: false,
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function setBusy(busy) {
  state.busy = busy;
  for (const control of [el.provider, el.endpoint, el.apiKey, el.model, el.reasoning, el.loopMax, el.hotTailKB, el.contextTokens, el.inputCost, el.cacheCost, el.outputCost, el.verify, el.save, el.reset]) {
    control.disabled = busy;
  }
}

function presetFor(url) {
  const normalized = String(url || "").replace(/\/$/, "");
  return Object.entries(PRESETS).find(([name, value]) => name !== "custom" && value.replace(/\/$/, "") === normalized)?.[0] || "custom";
}

function syncProviderUi({ replaceEndpoint = false } = {}) {
  const preset = el.provider.value;
  const custom = preset === "custom";
  el.endpoint.readOnly = !custom;
  if (replaceEndpoint && !custom) el.endpoint.value = PRESETS[preset];
}

function modelSpec(name) {
  return state.values?.models?.[name] || state.values?.pricing?.[name] || {};
}

function fillModelOptions(extra = []) {
  const values = state.values || {};
  const names = new Set([
    values.provider?.model,
    ...Object.keys(values.models || {}),
    ...Object.keys(values.pricing || {}),
    ...extra,
  ].filter(Boolean));
  const options = [...names].sort((a, b) => a.localeCompare(b)).map((name) => {
    const option = document.createElement("option");
    option.value = name;
    return option;
  });
  el.modelOptions.replaceChildren(...options);
}

function applyModel(name) {
  const values = state.values;
  if (!values) return;
  const spec = modelSpec(name);
  el.reasoning.value = spec.reasoningEffort || values.provider?.reasoningEffort || "high";
  el.loopMax.value = spec.loopMax ?? values.limits?.loopMax ?? 200;
  el.hotTailKB.value = spec.hotTailKB ?? values.limits?.hotTailKB ?? 4;
  el.contextTokens.value = spec.contextTokens ?? values.limits?.contextTokens ?? 272000;
  const registered = Boolean(values.models?.[name] || values.pricing?.[name]);
  el.inputCost.value = registered ? (spec.input ?? 0) : "";
  el.cacheCost.value = registered ? (spec.cache ?? 0) : "";
  el.outputCost.value = registered ? (spec.output ?? 0) : "";
  const active = name === values.provider?.model;
  el.modelBadge.hidden = !active;
  el.modelNote.textContent = registered
    ? "Đã đăng ký giá riêng trong cấu hình Thyca."
    : "Để trống giá để backend dùng bảng giá mặc định (nếu có).";
}

function applyValues(values) {
  state.values = clone(values);
  const provider = state.values.provider || {};
  el.provider.value = presetFor(provider.baseUrl);
  el.endpoint.value = provider.baseUrl || "";
  syncProviderUi();
  el.apiKey.value = "";
  el.apiKey.placeholder = state.meta.hasApiKey
    ? "•••••••• (đã lưu — để trống để giữ)"
    : "Dán API key";
  el.model.value = provider.model || "";
  fillModelOptions(state.verifiedModels);
  applyModel(el.model.value);
}

function positiveNumber(input, label, { min = 0, max = Number.POSITIVE_INFINITY, integer = false } = {}) {
  const value = Number(input.value);
  if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
    throw new Error(`${label} phải nằm trong khoảng ${min}–${max}.`);
  }
  return value;
}

function endpointValue() {
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

function collectValues() {
  const values = clone(state.values);
  const model = el.model.value.trim();
  if (!model) throw new Error("Cần Model ID.");
  const baseUrl = endpointValue();
  const loopMax = positiveNumber(el.loopMax, "Số vòng", { min: 1, max: 200, integer: true });
  const hotTailKB = positiveNumber(el.hotTailKB, "Dung lượng nhớ nóng", { min: 1, max: 64, integer: true });
  const contextTokens = positiveNumber(el.contextTokens, "Cửa sổ ngữ cảnh", { min: 1000, max: 2_000_000, integer: true });
  const priceFields = [el.inputCost, el.cacheCost, el.outputCost];
  const hasAnyPrice = priceFields.some((field) => field.value.trim() !== "");
  if (hasAnyPrice && priceFields.some((field) => field.value.trim() === "")) {
    throw new Error("Điền đủ cả ba giá Input, Cache và Output; hoặc để trống cả ba.");
  }
  const input = hasAnyPrice ? positiveNumber(el.inputCost, "Chi phí input") : null;
  const cache = hasAnyPrice ? positiveNumber(el.cacheCost, "Chi phí cache") : null;
  const output = hasAnyPrice ? positiveNumber(el.outputCost, "Chi phí output") : null;

  values.provider = {
    ...(values.provider || {}),
    baseUrl,
    model,
    reasoningEffort: el.reasoning.value,
    apiKey: el.apiKey.value.trim(),
  };
  values.limits = {
    ...(values.limits || {}),
    loopMax,
    hotTailKB,
    contextTokens,
  };
  if (hasAnyPrice) {
    values.models = { ...(values.models || {}) };
    values.models[model] = {
      ...(values.models[model] || {}),
      baseUrl: values.models[model]?.baseUrl || "",
      input,
      cache,
      output,
      reasoningEffort: el.reasoning.value,
      loopMax,
      hotTailKB,
      contextTokens,
    };
    values.pricing = { ...(values.pricing || {}) };
    values.pricing[model] = { input, cache, output };
  }
  return values;
}

async function verifyProvider() {
  setBusy(true);
  setStatus("Đang kiểm tra provider…");
  try {
    const payload = await postJson("/api/onboarding/verify", {
      baseUrl: endpointValue(),
      apiKey: el.apiKey.value.trim(),
    });
    state.verifiedModels = Array.isArray(payload.models) ? payload.models : [];
    fillModelOptions(state.verifiedModels);
    if (!el.model.value && state.verifiedModels.length) {
      el.model.value = state.verifiedModels[0];
      applyModel(el.model.value);
    }
    setStatus(
      state.verifiedModels.length
        ? `Kết nối thành công · tìm thấy ${state.verifiedModels.length} model.`
        : "Kết nối thành công · provider không trả danh sách model.",
      "success",
    );
  } catch (error) {
    setStatus(messageOf(error, "Không kiểm tra được provider."), "error");
  } finally {
    setBusy(false);
  }
}

async function saveProvider() {
  setBusy(true);
  setStatus("Đang lưu cấu hình…");
  try {
    const values = collectValues();
    const result = await postJson("/api/config", values);
    values.provider.apiKey = "";
    state.values = clone(values);
    state.saved = clone(values);
    state.meta.hasApiKey = Boolean(result.ready);
    el.apiKey.value = "";
    el.apiKey.placeholder = result.ready
      ? "•••••••• (đã lưu — để trống để giữ)"
      : "Dán API key";
    fillModelOptions(state.verifiedModels);
    applyModel(values.provider.model);
    setStatus(
      result.ready ? "Đã lưu. Provider sẵn sàng cho Chat." : "Đã lưu nhưng provider chưa sẵn sàng.",
      result.ready ? "success" : "error",
    );
  } catch (error) {
    setStatus(messageOf(error, "Không lưu được cấu hình."), "error");
  } finally {
    setBusy(false);
  }
}

function bind() {
  el.provider.addEventListener("change", () => syncProviderUi({ replaceEndpoint: true }));
  el.endpoint.addEventListener("input", () => {
    el.provider.value = presetFor(el.endpoint.value);
    syncProviderUi();
  });
  el.model.addEventListener("change", () => applyModel(el.model.value.trim()));
  el.verify.addEventListener("click", () => void verifyProvider());
  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    void saveProvider();
  });
  el.reset.addEventListener("click", () => {
    if (!state.saved) return;
    state.verifiedModels = [];
    applyValues(state.saved);
    setStatus("Đã hoàn tác các thay đổi chưa lưu.");
  });
}

async function boot() {
  bind();
  setBusy(true);
  setStatus("Đang đọc cấu hình backend…");
  try {
    const payload = await getJson("/api/config");
    state.meta = payload.meta || {};
    state.saved = clone(payload.values || {});
    applyValues(state.saved);
    const required = new URLSearchParams(location.search).has("required");
    setStatus(
      required && !state.meta.hasApiKey
        ? "Cần cấu hình provider trước khi bắt đầu Chat."
        : "Cấu hình đã được tải từ backend.",
      required && !state.meta.hasApiKey ? "error" : "success",
    );
  } catch (error) {
    setStatus(messageOf(error, "Không tải được cấu hình."), "error");
  } finally {
    setBusy(false);
  }
}

void boot();
