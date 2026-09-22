export const PRESETS = {
  openai: "https://api.openai.com/v1",
  openrouter: "https://openrouter.ai/api/v1",
  local: "http://127.0.0.1:11434/v1",
  custom: "",
};

export const PROVIDER_ID_RE = /^[A-Za-z0-9_-]+$/;

// Mức suy luận chuẩn backend nhận trực tiếp khi model không khai thinking map.
export const STANDARD_EFFORTS = ["low", "high", "max"];

export const state = {
  values: null,
  schema: null,
  saved: null,
  meta: {},
  verified: {},
  activeProvider: "",
  activeModel: "",
  busy: false,
};

export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function presetFor(url) {
  const normalized = String(url || "").replace(/\/$/, "");
  return Object.entries(PRESETS).find(([name, value]) => name !== "custom" && value.replace(/\/$/, "") === normalized)?.[0] || "custom";
}

export function providerIds() {
  return Object.keys(state.values?.providers || {});
}

export function providerOf(modelName) {
  return state.values?.models?.[modelName]?.provider || state.values?.defaultProvider || "";
}

export function modelsOf(providerId) {
  return Object.keys(state.values?.models || {})
    .filter((name) => providerOf(name) === providerId)
    .sort((a, b) => a.localeCompare(b));
}

export function modelSpec(name) {
  return state.values?.models?.[name] || state.values?.pricing?.[name] || {};
}
