// Shared settings schema state + field helpers + persistence.
import { postJson } from "../shared/util.js";
import { modes } from "../shared/data.js";
import { state } from "../shared/state.js";
import { el } from "../shared/dom.js";
import { renderPage } from "../render.js";

let schema = null;
let schemaValues = {};
let hasStoredKey = false;
let modelOptions = [];
let modelOptionsEndpoint = "";
const modelCache = new Map();
let modelFetching = false;
let defaultModel = "";

export { schema, schemaValues, hasStoredKey, modelOptions, modelOptionsEndpoint, modelCache, modelFetching, defaultModel };

export function setDefaultModel(next) { defaultModel = next || ""; }
export function setModelOptions(next) { modelOptions = next; }
export function setModelOptionsEndpoint(next) { modelOptionsEndpoint = next; }
export function setModelFetching(next) { modelFetching = Boolean(next); }

export function esc(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

export function nested(obj, dotted) {
  return dotted.split(".").reduce((acc, part) => (acc == null ? acc : acc[part]), obj);
}

export function fieldHtml(field) {
  const value = nested(schemaValues, field.key);
  const shown = value !== undefined && value !== null ? String(value) : field.default !== undefined && field.default !== null ? String(field.default) : "";
  const hint = field.hint ? `<p class="settings-hint">${esc(field.hint)}</p>` : "";
  if (field.choices) {
    const opts = field.choices
      .map((choice) => `<option value="${esc(choice)}"${choice === shown ? " selected" : ""}>${esc(choice)}</option>`)
      .join("");
    return `<label class="settings-field"><span>${esc(field.label)}</span><select data-key="${field.key}">${opts}</select>${hint}</label>`;
  }
  const type = field.type === "number" ? "number" : field.secret ? "password" : "text";
  const minMax = field.type === "number" && field.min !== undefined ? ` min="${field.min}" max="${field.max}"` : "";
  const autoComplete = field.secret ? ' autocomplete="new-password"' : "";
  const placeholder =
    hasStoredKey ? "•••••••• (đã lưu — để trống để giữ)" : shown;
  return `<label class="settings-field"><span>${esc(field.label)}</span>
      <input class="settings-input" type="${type}" data-key="${field.key}" value="${esc(field.secret ? "" : shown)}" placeholder="${esc(placeholder)}"${minMax}${autoComplete} spellcheck="false" />${hint}</label>`;
}

export function costInputs(prefix, model) {
  const m = model || {};
  const cells = [
    ["input", "Input"],
    ["cache", "Cache"],
    ["output", "Output"],
  ];
  // Label lives INSIDE the box as placeholder; no external span.
  const val = (v) => (m[v] !== undefined && m[v] !== null ? String(m[v]) : "");
  return `<div class="settings-cost-row">${cells
    .map(
      ([key, label]) =>
        `<input type="number" min="0" step="any" class="settings-input" data-${prefix}-cost="${key}" value="${val(key)}" placeholder="${label}" title="${label} (USD / 1M tokens)" aria-label="${label}" />`,
    )
    .join("")}</div>`;
}

export function readCostInputs(root, attr) {
  const costs = {};
  for (const input of root.querySelectorAll(`[data-${attr}-cost]`)) {
    costs[input.dataset[`${attr}Cost`]] = Number(input.value) || 0;
  }
  return costs;
}


export async function hydrateSettings() {
  const response = await fetch("/api/config", { cache: "no-store" });
  if (!response.ok) {
    return;
  }
  const payload = await response.json();
  schema = payload.schema;
  schemaValues = payload.values;
  hasStoredKey = Boolean(payload.meta?.hasApiKey);
  defaultModel = schemaValues.provider?.model || "";
  const registered = schemaValues.models || {};
  const { addModelPage } = await import("./provider.js");
  const { modelsPage } = await import("./models.js");
  const pages = [addModelPage(payload.meta || {}), modelsPage(Object.keys(registered).length)];
  modes.settings = {
    label: "Cài đặt",
    listLabel: "Cấu hình",
    kicker: "~/.thyca · config.json",
    note: "",
    chips: [],
    pages,
  };
  const count = el.modeList.querySelector('[data-mode="settings"] .mode-count');
  if (count) count.textContent = String(pages.length);
}

export function settingsForm(root) {
  // Guard: after a render/reload `root` may no longer host the settings DOM.
  return root && typeof root.querySelector === "function" ? root.querySelector("#settings-form") : null;
}

export function setStatus(root, message, kind = "") {
  const status =
    (settingsForm(root)?.querySelector(".settings-status") ||
      (root && typeof root.querySelector === "function" ? root.querySelector(".settings-status") : null));
  if (status) {
    status.textContent = message;
    status.className = `settings-status${kind ? ` is-${kind}` : ""}`;
  }
}

export function collectValues(root) {
  const values = JSON.parse(JSON.stringify(schemaValues));
  const form = settingsForm(root);
  if (!form) return values; // settings DOM gone — keep stored values
  for (const section of schema.sections) {
    for (const field of section.fields) {
      // provider.baseUrl/apiKey come from the provider cards instead.
      if (field.key === "provider.baseUrl" || field.key === "provider.apiKey") continue;
      const input = root.querySelector(`[data-key="${field.key}"]`);
      if (!input) continue;
      if (field.secret && input.value === "") continue; // keep stored key
      const dotted = field.key.split(".");
      let node = values;
      for (const part of dotted.slice(0, -1)) node = node[part] || (node[part] = {});
      node[dotted.at(-1)] = field.type === "number" ? Number(input.value) : input.value;
    }
  }
  // Provider baseUrl/key are written by saveProviderCard, not here.
  // Keep legacy pricing in sync with models so older consumers still work
  // (backend migration preserves pricing; deleting it here would wipe it).
  if (values.models && Object.keys(values.models).length) {
    values.pricing = {};
    for (const [name, m] of Object.entries(values.models)) {
      values.pricing[name] = { input: m.input, cache: m.cache, output: m.output };
    }
  }
  return values;
}

export async function persist(root, { models, providerModel, providerBaseUrl, providerApiKey }) {
  const values = collectValues(root);
  if (models !== undefined) values.models = models;
  if (providerModel !== undefined || providerBaseUrl !== undefined || providerApiKey !== undefined) {
    values.provider = { ...(values.provider || {}) };
    if (providerModel !== undefined) values.provider.model = providerModel;
    if (providerBaseUrl !== undefined) values.provider.baseUrl = providerBaseUrl;
    if (providerApiKey !== undefined) values.provider.apiKey = providerApiKey;
  }
  const payload = await postJson("/api/config", values);
  // Giữ contract "rỗng = giữ key cũ": server luôn trả apiKey="",
  // nên ép rỗng trước khi merge vào schemaValues (TASK-022).
  if (values.provider) values.provider.apiKey = "";
  schemaValues = { ...schemaValues, ...values };
  // Đồng bộ hasStoredKey 2 chiều từ server: ready true nghĩa là đã có key
  // dùng được; ready false nghĩa là hết key (đã xóa) → placeholder phải
  // về "dán API key" thay vì kẹt "••••" (trước chỉ set 1 chiều true).
  hasStoredKey = Boolean(payload.ready);
  if (providerModel !== undefined) defaultModel = values.provider?.model || defaultModel;
  if (!payload.ready) {
    setStatus(root, "Đã lưu nhưng provider chưa dùng được — kiểm tra API key.", "error");
  }
  // Model cache theo endpoint: đổi baseUrl global là cache cũ sai.
  modelCache.clear();
  modelOptions = [];
  modelOptionsEndpoint = "";
  // Chỉ reset chat khi patch thật sự đổi model/baseUrl (TASK-041):
  // sửa giá/limits không có 2 field này → giữ nguyên phiên đang xem.
  // So với snapshot trước khi refreshChatKicker cập nhật nó.
  const nextModel = providerModel !== undefined ? values.provider?.model : undefined;
  const nextUrl = providerBaseUrl !== undefined ? values.provider?.baseUrl : undefined;
  notifyProviderChanged(root, values.provider, { nextModel, nextUrl });
  return payload;
}

// Provider đổi (baseUrl/model/key mới) → chat phải reset theo (TASK-040/041):
// backend hot-reload config mỗi turn rồi, frontend chỉ cần mở khóa composer,
// refresh kicker model + về phiên mới khi model/baseUrl đổi.
function notifyProviderChanged(root, provider, changed = {}) {
  if (state.activeMode === "chat" || !provider) return;
  const { nextModel, nextUrl } = changed;
  void import("../chat/index.js").then(async ({ resetToNewChatPage, refreshChatKicker }) => {
    const prevModel = state.lastChatModel;
    const prevUrl = state.lastChatBaseUrl;
    try {
      if (typeof refreshChatKicker === "function") await refreshChatKicker();
    } catch { /* giữ kicker cũ, không chặn settings */ }
    const modelChanged = nextModel !== undefined && nextModel !== prevModel;
    const urlChanged = nextUrl !== undefined && nextUrl !== prevUrl;
    if (modelChanged || urlChanged) resetToNewChatPage();
  });
}

export async function refreshPages(root, page = 0) {
  await hydrateSettings();
  renderPage(page);
}
