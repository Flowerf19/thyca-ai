import { effortChoicesFor, fillEffortSelect } from "../../shared/js/reasoning-effort.js";
import {
  PRESETS,
  STANDARD_EFFORTS,
  modelSpec,
  modelsOf,
  presetFor,
  providerIds,
  providerOf,
  state,
} from "./provider-state.js";

export const el = {
  form: document.querySelector("#provider-form"),
  providerId: document.querySelector("#provider-id"),
  providerBadge: document.querySelector("#provider-badge"),
  providerNote: document.querySelector("#provider-note-text"),
  providerAdd: document.querySelector("#provider-add"),
  providerRename: document.querySelector("#provider-rename"),
  providerDelete: document.querySelector("#provider-delete"),
  defaultProvider: document.querySelector("#default-provider"),
  provider: document.querySelector("#provider"),
  endpoint: document.querySelector("#provider-endpoint"),
  apiKey: document.querySelector("#provider-key"),
  providerEffort: document.querySelector("#provider-effort"),
  providerApi: document.querySelector("#provider-api"),
  modelList: document.querySelector("#model-list"),
  modelAdd: document.querySelector("#model-add"),
  modelDefault: document.querySelector("#model-default"),
  modelDelete: document.querySelector("#model-delete"),
  model: document.querySelector("#model"),
  modelOptions: document.querySelector("#model-options"),
  modelBadge: document.querySelector("#model-badge"),
  modelNote: document.querySelector("#model-note-text"),
  reasoning: document.querySelector("#reasoning-effort"),
  reasoningEfforts: document.querySelector("#reasoning-efforts"),
  loopMax: document.querySelector("#loop-max"),
  hotTailKB: document.querySelector("#hot-tail-kb"),
  contextTokens: document.querySelector("#context-window"),
  inputCost: document.querySelector("#input-cost"),
  cacheCost: document.querySelector("#cache-input-cost"),
  outputCost: document.querySelector("#output-cost"),
  limitsLoopMax: document.querySelector("#limits-loop-max"),
  limitsHotTailKB: document.querySelector("#limits-hot-tail-kb"),
  limitsContextTokens: document.querySelector("#limits-context-window"),
  status: document.querySelector("#provider-status"),
  verify: document.querySelector("#verify-provider"),
  test: document.querySelector("#test-provider"),
  save: document.querySelector("#save-provider"),
  reset: document.querySelector(".settings-reset"),
};

export function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function allControls() {
  return [
    el.providerId, el.providerAdd, el.providerRename, el.providerDelete,
    el.defaultProvider, el.provider, el.endpoint, el.apiKey, el.providerEffort, el.providerApi,
    el.modelList, el.modelAdd, el.modelDefault, el.modelDelete, el.model,
    el.reasoning, el.reasoningEfforts, el.loopMax, el.hotTailKB, el.contextTokens,
    el.inputCost, el.cacheCost, el.outputCost,
    el.limitsLoopMax, el.limitsHotTailKB, el.limitsContextTokens,
    el.verify, el.test, el.save, el.reset,
  ];
}

export function setBusy(busy) {
  state.busy = busy;
  for (const control of allControls()) control.disabled = busy;
}

export function syncProviderUi({ replaceEndpoint = false } = {}) {
  const preset = el.provider.value;
  const custom = preset === "custom";
  el.endpoint.readOnly = !custom;
  if (replaceEndpoint && !custom) el.endpoint.value = PRESETS[preset];
}

export function fillEffortOptions(selected) {
  fillEffortSelect(
    el.reasoning,
    effortChoicesFor(state.schema, state.values, el.model.value.trim()),
    selected,
    state.schema,
  );
}

export function fillProviderEffort(selected) {
  fillEffortSelect(el.providerEffort, STANDARD_EFFORTS, selected, state.schema);
}

export function syncProviderApi(entry) {
  el.providerApi.value = entry.api === "openai_responses" ? "openai_responses" : "openai_chat";
}

export function fillDatalist() {
  const values = state.values || {};
  const names = new Set([
    ...(state.verified[state.activeProvider] || []),
    ...Object.keys(values.models || {}),
    ...Object.keys(values.pricing || {}),
  ].filter(Boolean));
  el.modelOptions.replaceChildren(...[...names].sort((a, b) => a.localeCompare(b)).map((name) => {
    const option = document.createElement("option");
    option.value = name;
    return option;
  }));
}

export function fillSelect(select, options, selected) {
  select.replaceChildren(...options.map((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    return option;
  }));
  if (options.includes(selected)) select.value = selected;
}

export function renderProviders() {
  const ids = providerIds();
  if (!ids.includes(state.activeProvider)) state.activeProvider = state.values?.defaultProvider || ids[0] || "";
  fillSelect(el.providerId, ids, state.activeProvider);
  fillSelect(el.defaultProvider, ids, state.values?.defaultProvider);
  const entry = state.values?.providers?.[state.activeProvider] || {};
  el.provider.value = presetFor(entry.baseUrl);
  el.endpoint.value = entry.baseUrl || "";
  syncProviderUi();
  el.apiKey.value = "";
  el.apiKey.placeholder = state.meta.providers?.[state.activeProvider]
    ? "•••••••• (đã lưu — để trống để giữ)"
    : "Dán API key";
  fillProviderEffort(entry.reasoningEffort);
  syncProviderApi(entry);
  const isDefault = state.activeProvider === state.values?.defaultProvider;
  el.providerBadge.hidden = !isDefault;
  const count = modelsOf(state.activeProvider).length;
  el.providerNote.textContent = `${count} model thuộc provider này.`;
}

export function renderModels() {
  const names = modelsOf(state.activeProvider);
  if (!names.includes(state.activeModel)) {
    state.activeModel = names.includes(state.values?.defaultModel)
      ? state.values.defaultModel
      : (names[0] || "");
  }
  fillSelect(el.modelList, names, state.activeModel);
  el.model.value = state.activeModel;
  applyModel(state.activeModel);
}

export function applyModel(name) {
  const values = state.values;
  if (!values) return;
  const spec = modelSpec(name);
  const providerEntry = values.providers?.[state.activeProvider] || {};
  fillEffortOptions(spec.reasoningEffort || providerEntry.reasoningEffort);
  el.reasoningEfforts.value = (spec.reasoningEfforts || []).join(", ");
  el.loopMax.value = spec.loopMax ?? values.limits?.loopMax ?? 200;
  el.hotTailKB.value = spec.hotTailKB ?? values.limits?.hotTailKB ?? 4;
  el.contextTokens.value = spec.contextTokens ?? values.limits?.contextTokens ?? 272000;
  const registered = Boolean(values.models?.[name]);
  el.inputCost.value = registered && spec.input != null ? spec.input : "";
  el.cacheCost.value = registered && spec.cache != null ? spec.cache : "";
  el.outputCost.value = registered && spec.output != null ? spec.output : "";
  const isDefault = Boolean(name) && name === values.defaultModel;
  el.modelBadge.hidden = !isDefault;
  el.modelNote.textContent = !name
    ? "Nhập Model ID rồi bấm Thêm để tạo model mới."
    : registered
      ? `Thuộc provider ${providerOf(name)}.`
      : "Model chưa đăng ký — bấm Thêm để tạo.";
  renderLimits();
}

export function renderLimits() {
  const limits = state.values?.limits || {};
  el.limitsLoopMax.value = limits.loopMax ?? 200;
  el.limitsHotTailKB.value = limits.hotTailKB ?? 4;
  el.limitsContextTokens.value = limits.contextTokens ?? 272000;
}

export function renderAll() {
  renderProviders();
  renderModels();
  const entry = state.values?.providers?.[state.activeProvider];
  if (entry) syncProviderApi(entry);
  fillDatalist();
}

// Popup thông báo sau khi lưu thành công và provider sẵn sàng.
let readyDialog = null;

export function showReadyPopup() {
  if (!readyDialog) {
    readyDialog = document.createElement("dialog");
    readyDialog.className = "screen-dialog";
    readyDialog.setAttribute("aria-labelledby", "provider-ready-title");
    readyDialog.innerHTML = `
      <div class="screen-heading">
        <h2 id="provider-ready-title">Provider đã sẵn sàng</h2>
        <button class="icon-button" type="button" aria-label="Đóng thông báo">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>
        </button>
      </div>
      <p>Provider đã sẵn sàng — bắt đầu trò chuyện được rồi.</p>
      <div class="screen-toolbar">
        <a class="screen-button is-primary" href="./index.html">Mở Trò chuyện</a>
        <button class="screen-button" type="button">Đóng</button>
      </div>`;
    for (const button of readyDialog.querySelectorAll("button")) {
      button.addEventListener("click", () => readyDialog.close());
    }
    document.body.append(readyDialog);
  }
  readyDialog.showModal();
}
