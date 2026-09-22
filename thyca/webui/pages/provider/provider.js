import { getJson } from "../../shared/js/api.js";
import {
  addModel,
  addProvider,
  deleteModel,
  deleteProvider,
  renameProvider,
  saveProvider,
  setDefaultModel,
  testProvider,
  verifyProvider,
} from "./provider-actions.js";
import {
  applyModel,
  el,
  fillDatalist,
  renderAll,
  renderModels,
  renderProviders,
  setBusy,
  setStatus,
  syncProviderUi,
} from "./provider-dom.js";
import { clone, messageOf, presetFor, state } from "./provider-state.js";

function bind() {
  el.providerId.addEventListener("change", () => {
    state.activeProvider = el.providerId.value;
    renderProviders();
    renderModels();
    fillDatalist();
  });
  el.defaultProvider.addEventListener("change", () => {
    state.values.defaultProvider = el.defaultProvider.value;
    renderProviders();
    renderModels();
  });
  el.provider.addEventListener("change", () => syncProviderUi({ replaceEndpoint: true }));
  el.endpoint.addEventListener("input", () => {
    el.provider.value = presetFor(el.endpoint.value);
    syncProviderUi();
  });
  el.providerAdd.addEventListener("click", addProvider);
  el.providerRename.addEventListener("click", renameProvider);
  el.providerDelete.addEventListener("click", deleteProvider);
  el.modelList.addEventListener("change", () => {
    state.activeModel = el.modelList.value;
    el.model.value = state.activeModel;
    applyModel(state.activeModel);
  });
  el.model.addEventListener("change", () => applyModel(el.model.value.trim()));
  el.modelAdd.addEventListener("click", addModel);
  el.modelDelete.addEventListener("click", deleteModel);
  el.modelDefault.addEventListener("click", setDefaultModel);
  el.verify.addEventListener("click", () => void verifyProvider());
  el.test.addEventListener("click", () => void testProvider());
  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    void saveProvider();
  });
  el.reset.addEventListener("click", () => {
    if (!state.saved) return;
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
