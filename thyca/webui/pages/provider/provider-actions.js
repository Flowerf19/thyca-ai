import { getJson, postJson } from "../../shared/js/api.js";
import {
  applyModel,
  el,
  fillDatalist,
  renderAll,
  renderModels,
  setBusy,
  setStatus,
  showReadyPopup,
} from "./provider-dom.js";
import {
  clone,
  messageOf,
  modelsOf,
  providerIds,
  providerOf,
  state,
} from "./provider-state.js";
import { applyFormToState, askProviderId, endpointValue } from "./provider-validate.js";

export function addProvider() {
  try {
    const id = askProviderId("Tên provider mới (chữ, số, _ -):");
    if (id === null) return;
    const values = clone(state.values);
    values.providers = { ...(values.providers || {}) };
    if (values.providers[id]) throw new Error(`Provider "${id}" đã tồn tại.`);
    values.providers[id] = { baseUrl: "https://api.openai.com/v1", apiKeyEnv: "THYCA_TOKEN", apiKey: "", reasoningEffort: "high", api: "openai_chat" };
    state.values = values;
    state.activeProvider = id;
    state.activeModel = "";
    renderAll();
    setStatus(`Đã thêm provider "${id}". Nhập endpoint + key, Kiểm tra, rồi Lưu.`, "success");
  } catch (error) {
    setStatus(messageOf(error, "Không thêm được provider."), "error");
  }
}

export function renameProvider() {
  try {
    const pid = state.activeProvider;
    const id = askProviderId(`Đổi tên provider "${pid}" thành:`, pid);
    if (id === null || id === pid) return;
    const values = clone(state.values);
    if (values.providers[id]) throw new Error(`Provider "${id}" đã tồn tại.`);
    values.providers[id] = values.providers[pid];
    delete values.providers[pid];
    if (values.defaultProvider === pid) values.defaultProvider = id;
    for (const spec of Object.values(values.models || {})) {
      if (spec.provider === pid) spec.provider = id;
    }
    if (state.verified[pid]) {
      state.verified[id] = state.verified[pid];
      delete state.verified[pid];
    }
    state.values = values;
    state.activeProvider = id;
    renderAll();
    setStatus(`Đã đổi "${pid}" thành "${id}". Nhớ Lưu cấu hình.`, "success");
  } catch (error) {
    setStatus(messageOf(error, "Không đổi tên được."), "error");
  }
}

export function deleteProvider() {
  try {
    const pid = state.activeProvider;
    const ids = providerIds();
    if (ids.length <= 1) throw new Error("Không xóa provider cuối cùng.");
    const owned = modelsOf(pid);
    const values = clone(state.values);
    if (owned.length) {
      const target = window.prompt(
        `Provider "${pid}" còn ${owned.length} model (${owned.join(", ")}). Nhập id provider để chuyển sang (để trống để hủy):`,
        values.defaultProvider === pid ? "" : values.defaultProvider,
      );
      if (!target) return;
      const dest = target.trim();
      if (!values.providers[dest]) throw new Error(`Provider "${dest}" không tồn tại.`);
      if (dest === pid) throw new Error("Phải chuyển sang provider khác.");
      for (const name of owned) values.models[name].provider = dest;
    }
    delete values.providers[pid];
    delete state.verified[pid];
    if (values.defaultProvider === pid) values.defaultProvider = Object.keys(values.providers)[0];
    state.values = values;
    state.activeProvider = values.defaultProvider;
    renderAll();
    setStatus(`Đã xóa provider "${pid}"${owned.length ? `, chuyển ${owned.length} model sang "${state.values.models[owned[0]].provider}".` : "."} Nhớ Lưu cấu hình.`, "success");
  } catch (error) {
    setStatus(messageOf(error, "Không xóa được provider."), "error");
  }
}

export function addModel() {
  try {
    const name = el.model.value.trim();
    if (!name) throw new Error("Nhập Model ID trước khi Thêm.");
    const values = clone(state.values);
    values.models = { ...(values.models || {}) };
    if (!values.models[name]) {
      values.models[name] = { provider: state.activeProvider };
      state.values = values;
      state.activeModel = name;
      renderAll();
      setStatus(`Đã thêm model "${name}" vào provider "${state.activeProvider}". Điền thinking map/giá rồi Lưu.`, "success");
    } else {
      state.activeModel = name;
      renderModels();
      setStatus(`Model "${name}" đã tồn tại (provider ${providerOf(name)}).`, "");
    }
  } catch (error) {
    setStatus(messageOf(error, "Không thêm được model."), "error");
  }
}

export function deleteModel() {
  try {
    const name = state.activeModel || el.model.value.trim();
    if (!name || !state.values?.models?.[name]) throw new Error("Chưa chọn model đã đăng ký.");
    if (name === state.values.defaultModel) throw new Error(`"${name}" đang là model mặc định — đặt model khác làm mặc định trước.`);
    const values = clone(state.values);
    delete values.models[name];
    if (values.pricing) delete values.pricing[name];
    state.values = values;
    state.activeModel = "";
    renderAll();
    setStatus(`Đã xóa model "${name}". Nhớ Lưu cấu hình.`, "success");
  } catch (error) {
    setStatus(messageOf(error, "Không xóa được model."), "error");
  }
}

export function setDefaultModel() {
  try {
    const name = el.model.value.trim();
    if (!name) throw new Error("Nhập Model ID trước.");
    const values = clone(state.values);
    values.defaultModel = name;
    state.values = values;
    state.activeModel = name;
    renderAll();
    setStatus(`Model mặc định: "${name}". Nhớ Lưu cấu hình.`, "success");
  } catch (error) {
    setStatus(messageOf(error, "Không đặt mặc định được."), "error");
  }
}

export async function verifyProvider() {
  setBusy(true);
  setStatus("Đang kiểm tra provider…");
  try {
    const payload = await postJson("/api/onboarding/verify", {
      baseUrl: endpointValue(),
      apiKey: el.apiKey.value.trim(),
      providerId: state.activeProvider,
    });
    const found = Array.isArray(payload.models) ? payload.models : [];
    state.verified[state.activeProvider] = found;
    fillDatalist();
    if (!el.model.value && found.length) {
      el.model.value = found[0];
      applyModel(el.model.value);
    }
    setStatus(
      found.length
        ? `Kết nối thành công · tìm thấy ${found.length} model.`
        : "Kết nối thành công · provider không trả danh sách model.",
      "success",
    );
  } catch (error) {
    setStatus(messageOf(error, "Không kiểm tra được provider."), "error");
  } finally {
    setBusy(false);
  }
}

export async function testProvider() {
  setBusy(true);
  setStatus("Đang test API…");
  try {
    const model = el.model.value.trim() || state.values?.defaultModel || "";
    const result = await postJson("/api/providers/test", {
      providerId: state.activeProvider,
      model,
    });
    const latency = result.latencyMs != null ? ` · ${result.latencyMs}ms` : "";
    setStatus(`Test API thành công: ${result.model}${latency}.`, "success");
  } catch (error) {
    setStatus(messageOf(error, "Test API thất bại."), "error");
  } finally {
    setBusy(false);
  }
}

export async function saveProvider() {
  setBusy(true);
  setStatus("Đang lưu cấu hình…");
  try {
    const values = applyFormToState(clone(state.values));
    await postJson("/api/config", values);
    const fresh = await getJson("/api/config");
    state.values = clone(fresh.values || values);
    for (const entry of Object.values(state.values.providers || {})) entry.apiKey = "";
    state.saved = clone(state.values);
    state.meta = fresh.meta || {};
    if (!providerIds().includes(state.activeProvider)) state.activeProvider = state.values.defaultProvider;
    renderAll();
    const providerCount = Object.keys(values.providers || {}).length;
    const modelCount = Object.keys(values.models || {}).length;
    // Lưu xong test luôn API của model mặc định để đảm bảo dùng được.
    // Trạng thái theo kết quả test, không theo default key: multi-provider
    // hợp lệ vẫn có thể để default provider trống key.
    let testNote = "";
    let testOk = false;
    try {
      const pid = providerOf(values.defaultModel) || values.defaultProvider;
      const tested = await postJson("/api/providers/test", {
        providerId: pid,
        model: values.defaultModel,
      });
      testNote = ` Test API: OK (${tested.model} · ${tested.latencyMs}ms).`;
      testOk = true;
    } catch (error) {
      testNote = ` Test API thất bại: ${messageOf(error, "không rõ lỗi")}.`;
    }
    setStatus(
      `Đã lưu · ${providerCount} provider · ${modelCount} model · mặc định: ${values.defaultModel}.${testNote}`,
      testOk ? "success" : "error",
    );
    if (testOk) showReadyPopup();
  } catch (error) {
    setStatus(messageOf(error, "Không lưu được cấu hình."), "error");
  } finally {
    setBusy(false);
  }
}
