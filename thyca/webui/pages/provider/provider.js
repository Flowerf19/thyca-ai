import { getJson } from "../../shared/js/http.js";
import { messageOf } from "../../shared/js/status.js";
import {
  addModel,
  addProvider,
  cancelAdd,
  createModel,
  createProvider,
  deleteModel,
  deleteProvider,
  renameProvider,
  saveProvider,
  setDefaultModel,
  setDefaultProvider,
  testProvider,
  verifyProvider,
} from "./provider-actions.js";
import {
  applyModel,
  bindModelSuggest,
  el,
  fillEffortOptions,
  refreshModelSuggest,
  renderAll,
  renderModels,
  renderProviders,
  setBusy,
  setStatus,
  syncProviderUi,
} from "./provider-dom.js";
import { clone, presetFor, providerOf, state } from "./provider-state.js";

function handleMenuAction(action, id) {
  if (state.busy) return;
  // Any row action discards an unfinished add draft first.
  state.adding = null;
  switch (action) {
    case "provider-default": return setDefaultProvider(id);
    case "provider-rename": return renameProvider(id);
    case "provider-delete": return deleteProvider(id);
    case "model-default": return setDefaultModel(id);
    case "model-delete": return deleteModel(id);
    default: return undefined;
  }
}

function closeRowMenus(except = null) {
  for (const open of document.querySelectorAll("details.row-menu[open]")) {
    if (open !== except) open.open = false;
  }
}

function bind() {
  bindModelSuggest();
  // Field-level errors clear as soon as the user edits the flagged input.
  el.form.addEventListener("input", (event) => {
    event.target?.removeAttribute?.("aria-invalid");
  });
  // One ⋯ menu open at a time; Escape or an outside click closes it.
  document.addEventListener("toggle", (event) => {
    const node = event.target;
    if (!(node instanceof HTMLElement) || !node.matches("details.row-menu")) return;
    if (node.open) closeRowMenus(node);
  }, true);
  document.addEventListener("click", (event) => {
    const actionButton = event.target?.closest?.(".row-menu-pop button");
    if (actionButton) {
      actionButton.closest("details.row-menu")?.removeAttribute("open");
      handleMenuAction(actionButton.dataset.action, actionButton.dataset.id);
      return;
    }
    if (!event.target?.closest?.("details.row-menu")) closeRowMenus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const hadMenu = document.querySelector("details.row-menu[open]");
    closeRowMenus();
    if (!hadMenu && state.adding) cancelAdd();
  });
  el.providerList.addEventListener("click", (event) => {
    const pick = event.target?.closest?.(".pick-select");
    if (!pick) return;
    const pid = pick.closest(".pick-row")?.dataset.id;
    if (!pid || (pid === state.activeProvider && !state.adding)) return;
    state.adding = null;
    state.activeProvider = pid;
    renderProviders();
    renderModels();
    refreshModelSuggest();
  });
  el.modelListbox.addEventListener("click", (event) => {
    const pick = event.target?.closest?.(".pick-select");
    if (!pick) return;
    const name = pick.closest(".pick-row")?.dataset.id;
    if (!name) return;
    state.adding = null;
    state.activeModel = name;
    el.model.value = name;
    applyModel(name);
  });
  el.provider.addEventListener("change", () => syncProviderUi({ replaceEndpoint: true }));
  el.endpoint.addEventListener("input", () => {
    el.provider.value = presetFor(el.endpoint.value);
    syncProviderUi();
  });
  el.providerAdd.addEventListener("click", addProvider);
  el.model.addEventListener("change", () => {
    const name = el.model.value.trim();
    // In add mode a blur must never wipe the draft: only refresh the effort
    // choices for the typed name, keeping the current selection if valid.
    if (state.adding === "model") {
      state.activeModel = name;
      fillEffortOptions(el.reasoning.value);
      if (name && state.values?.models?.[name]) {
        setStatus(`Model "${name}" đã tồn tại (provider ${providerOf(name)}) — bấm Tạo để chọn nó.`, "");
      }
      return;
    }
    state.activeModel = name;
    applyModel(name);
  });
  el.modelAdd.addEventListener("click", addModel);
  el.providerCreate.addEventListener("click", createProvider);
  el.providerCancel.addEventListener("click", cancelAdd);
  el.modelCreate.addEventListener("click", createModel);
  el.modelCancel.addEventListener("click", cancelAdd);
  el.verify.addEventListener("click", () => void verifyProvider());
  el.test.addEventListener("click", () => void testProvider());
  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    // Enter inside the form confirms the current mode: create the draft, or
    // save when browsing.
    if (state.adding === "provider") {
      createProvider();
      return;
    }
    if (state.adding === "model") {
      createModel();
      return;
    }
    void saveProvider();
  });
  el.reset.addEventListener("click", () => {
    if (!state.saved) return;
    state.adding = null;
    state.verified = {};
    state.values = clone(state.saved);
    state.activeProvider = state.values.defaultProvider;
    state.activeModel = state.values.defaultModel;
    renderAll();
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
    state.schema = payload.schema || null;
    state.saved = clone(payload.values || {});
    state.values = clone(state.saved);
    state.activeProvider = state.values.defaultProvider || Object.keys(state.values.providers || {})[0] || "";
    state.activeModel = state.values.defaultModel || "";
    renderAll();
    const required = new URLSearchParams(location.search).has("required");
    // Boot note stays a quiet line (kind ""), not a success card: the
    // rendered page itself is the confirmation.
    setStatus(
      required && !state.meta.hasApiKey
        ? "Cần cấu hình provider trước khi bắt đầu Chat."
        : "Cấu hình đã được tải từ backend.",
      required && !state.meta.hasApiKey ? "error" : "",
    );
  } catch (error) {
    setStatus(messageOf(error, "Không tải được cấu hình."), "error");
  } finally {
    setBusy(false);
  }
}

void boot();
