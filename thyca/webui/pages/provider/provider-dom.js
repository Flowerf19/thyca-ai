import { effortChoicesFor, fillEffortSelect } from "../../shared/js/reasoning-effort.js";
import { makeSetStatus } from "../../shared/js/status.js";
import {
  PRESETS,
  STANDARD_EFFORTS,
  modelSpec,
  modelsOf,
  presetFor,
  providerIds,
  state,
} from "./provider-state.js";

export const el = {
  form: document.querySelector("#provider-form"),
  providerList: document.querySelector("#provider-list"),
  providerAdd: document.querySelector("#provider-add"),
  providerCount: document.querySelector("#provider-count"),
  providerDetailName: document.querySelector("#provider-detail-name"),
  providerDetailBadge: document.querySelector("#provider-detail-badge"),
  providerNameLabel: document.querySelector("#provider-name-label"),
  providerName: document.querySelector("#provider-name"),
  providerCreate: document.querySelector("#provider-create"),
  providerCancel: document.querySelector("#provider-cancel"),
  provider: document.querySelector("#provider"),
  endpoint: document.querySelector("#provider-endpoint"),
  apiKey: document.querySelector("#provider-key"),
  providerEffort: document.querySelector("#provider-effort"),
  providerApi: document.querySelector("#provider-api"),
  modelListbox: document.querySelector("#model-listbox"),
  modelAdd: document.querySelector("#model-add"),
  modelCount: document.querySelector("#model-count"),
  modelProviderName: document.querySelector("#model-provider-name"),
  modelAddHead: document.querySelector("#model-add-head"),
  modelCreateRow: document.querySelector("#model-create-row"),
  modelCreate: document.querySelector("#model-create"),
  modelCancel: document.querySelector("#model-cancel"),
  model: document.querySelector("#model"),
  modelSuggest: document.querySelector("#model-suggest"),
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
  limitsSoftTimeoutS: document.querySelector("#limits-soft-timeout"),
  status: document.querySelector("#provider-status"),
  verify: document.querySelector("#verify-provider"),
  test: document.querySelector("#test-provider"),
  save: document.querySelector("#save-provider"),
  reset: document.querySelector(".settings-reset"),
};

// Status reuses the shared .thyca-nudge banner (same CSS as the chat 15-minute
// idle reminder); an empty nudge hides itself via :empty. Non-errors are
// transient: the banner clears itself so routine notes don't squat on the
// footer. Errors stay until the next action.
const renderStatus = makeSetStatus(el.status, "thyca-nudge provider-nudge");
let statusTimer = 0;
export function setStatus(message = "", kind = "") {
  window.clearTimeout(statusTimer);
  statusTimer = 0;
  renderStatus(message, kind);
  if (message && kind !== "error") {
    statusTimer = window.setTimeout(() => {
      if (el.status.textContent === message) renderStatus("");
    }, 6000);
  }
}

export function setBusy(busy) {
  state.busy = busy;
  setSuggestOpen(false);
  for (const control of el.form.querySelectorAll("input, select, button, textarea")) control.disabled = busy;
}

export function clearFieldErrors() {
  for (const bad of el.form.querySelectorAll('[aria-invalid="true"]')) bad.removeAttribute("aria-invalid");
}

export function flagField(id) {
  if (!id) return;
  const node = el.form.querySelector(`#${CSS.escape(id)}`);
  if (!node) return;
  node.setAttribute("aria-invalid", "true");
  if (typeof node.focus === "function") node.focus({ preventScroll: false });
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

// Model ID suggest: same source the native datalist used (verified +
// registered + pricing names), rendered as a styled listbox instead.
let suggestNames = [];
let suggestActive = -1;
let suggestOpen = false;

function suggestMatches() {
  const query = el.model.value.trim().toLowerCase();
  if (!query) return suggestNames;
  return suggestNames.filter((name) => name.toLowerCase().includes(query));
}

function renderModelSuggest() {
  const names = suggestMatches();
  if (suggestActive >= names.length) suggestActive = -1;
  el.modelSuggest.replaceChildren(...names.map((name, index) => {
    const li = document.createElement("li");
    li.setAttribute("role", "option");
    li.dataset.name = name;
    const label = document.createElement("span");
    label.textContent = name;
    label.title = name;
    li.append(label);
    if (state.values?.models?.[name]) {
      const meta = document.createElement("span");
      meta.className = "pick-meta";
      meta.textContent = "đã có";
      li.append(meta);
    }
    const on = index === suggestActive;
    li.classList.toggle("is-active", on);
    li.setAttribute("aria-selected", on ? "true" : "false");
    return li;
  }));
  const open = suggestOpen && names.length > 0;
  el.modelSuggest.hidden = !open;
  el.model.setAttribute("aria-expanded", open ? "true" : "false");
}

function setSuggestOpen(open) {
  suggestOpen = open && suggestMatches().length > 0;
  if (!open) suggestActive = -1;
  renderModelSuggest();
}

function pickSuggest(name) {
  state.activeModel = name;
  el.model.value = name;
  el.model.removeAttribute("aria-invalid");
  setSuggestOpen(false);
  applyModel(name);
  el.model.focus();
}

export function refreshModelSuggest() {
  const values = state.values || {};
  suggestNames = [...new Set([
    ...(state.verified[state.activeProvider] || []),
    ...Object.keys(values.models || {}),
    ...Object.keys(values.pricing || {}),
  ].filter(Boolean))].sort((a, b) => a.localeCompare(b));
  renderModelSuggest();
}

export function bindModelSuggest() {
  el.model.setAttribute("role", "combobox");
  el.model.setAttribute("aria-autocomplete", "list");
  el.model.setAttribute("aria-controls", "model-suggest");
  el.model.setAttribute("aria-expanded", "false");
  el.model.addEventListener("focus", () => setSuggestOpen(true));
  el.model.addEventListener("input", () => setSuggestOpen(true));
  el.model.addEventListener("keydown", (event) => {
    const names = suggestMatches();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!names.length) return;
      event.preventDefault();
      if (!suggestOpen) {
        suggestActive = -1;
        setSuggestOpen(true);
      }
      const dir = event.key === "ArrowDown" ? 1 : -1;
      suggestActive = suggestActive < 0
        ? (dir > 0 ? 0 : names.length - 1)
        : (suggestActive + dir + names.length) % names.length;
      renderModelSuggest();
      el.modelSuggest.children[suggestActive]?.scrollIntoView({ block: "nearest" });
    } else if (event.key === "Enter") {
      // Only an arrow-highlighted row hijacks Enter; typing a brand-new ID
      // and hitting Enter still submits the form (save) as before.
      if (suggestOpen && suggestActive >= 0 && names[suggestActive]) {
        event.preventDefault();
        pickSuggest(names[suggestActive]);
      }
    } else if (event.key === "Escape") {
      if (suggestOpen) {
        event.preventDefault();
        setSuggestOpen(false);
      }
    }
  });
  // mousedown fires before blur, so picking here keeps focus stable.
  el.modelSuggest.addEventListener("mousedown", (event) => {
    const li = event.target?.closest?.("li[data-name]");
    if (!li) return;
    event.preventDefault();
    pickSuggest(li.dataset.name);
  });
  el.modelSuggest.addEventListener("mousemove", (event) => {
    const li = event.target?.closest?.("li[data-name]");
    if (!li) return;
    const index = [...el.modelSuggest.children].indexOf(li);
    if (index >= 0 && index !== suggestActive) {
      suggestActive = index;
      renderModelSuggest();
    }
  });
  el.model.addEventListener("blur", () => setSuggestOpen(false));
  document.addEventListener("click", (event) => {
    if (!event.target?.closest?.(".model-id-wrap")) setSuggestOpen(false);
  });
}

// Rows are built with textContent (model names are user input — never innerHTML).
function rowMenu(actions) {
  const details = document.createElement("details");
  details.className = "row-menu";
  const summary = document.createElement("summary");
  summary.textContent = "⋯";
  summary.setAttribute("aria-label", "Thao tác");
  summary.title = "Thao tác";
  details.append(summary);
  const pop = document.createElement("div");
  pop.className = "row-menu-pop";
  for (const { action, label, danger, id } of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.dataset.action = action;
    button.dataset.id = id;
    if (danger) button.classList.add("is-danger");
    pop.append(button);
  }
  details.append(pop);
  return details;
}

function pickRow({ id, active, badge, menu }) {
  const li = document.createElement("li");
  li.className = `pick-row${active ? " is-active" : ""}`;
  li.dataset.id = id;
  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "pick-select";
  if (active) pick.setAttribute("aria-current", "true");
  const name = document.createElement("span");
  name.className = "pick-name";
  name.textContent = id;
  name.title = id;
  pick.append(name);
  if (badge) {
    const badgeNode = document.createElement("span");
    badgeNode.className = "pick-badge";
    badgeNode.textContent = badge;
    pick.append(badgeNode);
  }
  li.append(pick);
  li.append(rowMenu(menu));
  return li;
}

function emptyRow(text) {
  const li = document.createElement("li");
  li.className = "pick-empty";
  li.textContent = text;
  return li;
}

export function renderProviders() {
  const ids = providerIds();
  if (!ids.includes(state.activeProvider)) state.activeProvider = state.values?.defaultProvider || ids[0] || "";
  const adding = state.adding === "provider";
  el.providerList.replaceChildren(...ids.map((pid) => pickRow({
    id: pid,
    active: !adding && pid === state.activeProvider,
    badge: pid === state.values?.defaultProvider ? "Mặc định" : "",
    menu: [
      { action: "provider-default", label: "Đặt mặc định", id: pid },
      { action: "provider-rename", label: "Đổi tên", id: pid },
      { action: "provider-delete", label: "Xóa", danger: true, id: pid },
    ],
  })));
  if (!ids.length) el.providerList.append(emptyRow("Chưa có provider — bấm Thêm."));
  el.providerCount.textContent = ids.length ? `${ids.length}` : "";
  el.providerNameLabel.hidden = !adding;
  el.providerName.hidden = !adding;
  el.verify.hidden = adding;
  el.test.hidden = adding;
  el.providerCreate.hidden = !adding;
  el.providerCancel.hidden = !adding;
  if (adding) {
    // Empty add form. Renders never fire mid-typing: every render path exits
    // add mode first (list/menu/reset), so clearing here is safe.
    el.providerDetailName.textContent = "Provider mới";
    el.providerDetailBadge.hidden = true;
    el.providerName.value = "";
    el.provider.value = "custom";
    el.endpoint.value = "";
    syncProviderUi();
    el.apiKey.value = "";
    el.apiKey.placeholder = "Dán API key";
    fillProviderEffort(undefined);
    syncProviderApi({});
    return;
  }
  const entry = state.values?.providers?.[state.activeProvider] || {};
  el.providerDetailName.textContent = state.activeProvider || "Chưa có provider";
  el.providerDetailBadge.hidden = !state.activeProvider || state.activeProvider !== state.values?.defaultProvider;
  el.provider.value = presetFor(entry.baseUrl);
  el.endpoint.value = entry.baseUrl || "";
  syncProviderUi();
  el.apiKey.value = "";
  el.apiKey.placeholder = state.meta.providers?.[state.activeProvider]
    ? "•••••••• (đã lưu — để trống để giữ)"
    : "Dán API key";
  fillProviderEffort(entry.reasoningEffort);
  syncProviderApi(entry);
  el.modelProviderName.textContent = state.activeProvider ? `· ${state.activeProvider}` : "";
}

export function renderModels() {
  const names = modelsOf(state.activeProvider);
  const adding = state.adding === "model";
  if (!adding && !names.includes(state.activeModel)) {
    state.activeModel = names.includes(state.values?.defaultModel)
      ? state.values.defaultModel
      : (names[0] || "");
  }
  el.modelListbox.replaceChildren(...names.map((name) => pickRow({
    id: name,
    active: !adding && name === state.activeModel,
    badge: name === state.values?.defaultModel ? "Mặc định" : "",
    menu: [
      { action: "model-default", label: "Đặt mặc định", id: name },
      { action: "model-delete", label: "Xóa", danger: true, id: name },
    ],
  })));
  if (!names.length) el.modelListbox.append(emptyRow("Chưa có model — bấm Thêm."));
  el.modelCount.textContent = names.length ? `${names.length}` : "";
  el.modelAddHead.hidden = !adding;
  el.modelCreateRow.hidden = !adding;
  if (adding) {
    el.model.value = "";
    applyModel("");
    return;
  }
  el.model.value = state.activeModel;
  applyModel(state.activeModel);
}

export function applyModel(name) {
  const values = state.values;
  if (!values) return;
  for (const row of el.modelListbox.querySelectorAll(".pick-row")) {
    const on = row.dataset.id === name;
    row.classList.toggle("is-active", on);
    const pick = row.querySelector(".pick-select");
    if (pick) {
      if (on) pick.setAttribute("aria-current", "true");
      else pick.removeAttribute("aria-current");
    }
  }
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
  renderLimits();
}

export function renderLimits() {
  const limits = state.values?.limits || {};
  el.limitsLoopMax.value = limits.loopMax ?? 200;
  el.limitsHotTailKB.value = limits.hotTailKB ?? 4;
  el.limitsContextTokens.value = limits.contextTokens ?? 272000;
  el.limitsSoftTimeoutS.value = limits.softTimeoutS ?? 60;
}

export function renderAll() {
  renderProviders();
  renderModels();
  const entry = state.values?.providers?.[state.activeProvider];
  if (entry) syncProviderApi(entry);
  refreshModelSuggest();
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
