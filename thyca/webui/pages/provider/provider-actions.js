import { getJson, postJson } from "../../shared/js/http.js";
import { messageOf } from "../../shared/js/status.js";
import {
  applyModel,
  clearFieldErrors,
  el,
  flagField,
  refreshModelSuggest,
  renderAll,
  setBusy,
  setStatus,
  showReadyPopup,
} from "./provider-dom.js";
import {
  PROVIDER_ID_RE,
  clone,
  modelsOf,
  providerIds,
  providerOf,
  state,
} from "./provider-state.js";
import { FieldError, applyFormToState, applyModelFormToState, askProviderId, endpointValue } from "./provider-validate.js";

function fail(error, fallback) {
  if (error?.fieldId) flagField(error.fieldId);
  setStatus(messageOf(error, fallback), "error");
}

export function addProvider() {
  if (state.adding === "provider") {
    el.providerName.focus();
    return;
  }
  clearFieldErrors();
  state.adding = "provider";
  renderAll();
  el.providerName.focus();
  setStatus("Điền tên, endpoint và key rồi bấm Tạo provider.", "");
}

export function createProvider() {
  try {
    clearFieldErrors();
    const id = el.providerName.value.trim();
    if (!id) throw new FieldError("Tên provider không được để trống.", "provider-name");
    if (!PROVIDER_ID_RE.test(id)) throw new FieldError("Tên provider chỉ gồm chữ, số, _ và -.", "provider-name");
    const values = clone(state.values);
    values.providers = { ...(values.providers || {}) };
    if (values.providers[id]) throw new FieldError(`Provider "${id}" đã tồn tại.`, "provider-name");
    values.providers[id] = {
      baseUrl: endpointValue(),
      apiKeyEnv: "THYCA_TOKEN",
      apiKey: el.apiKey.value.trim(),
      reasoningEffort: el.providerEffort.value,
      api: el.providerApi.value === "openai_responses" ? "openai_responses" : "openai_chat",
    };
    state.values = values;
    state.activeProvider = id;
    state.activeModel = "";
    state.adding = null;
    renderAll();
    setStatus(`Đã tạo provider "${id}". Kiểm tra & tải model, rồi Lưu.`, "success");
    el.verify.focus();
  } catch (error) {
    fail(error, "Không tạo được provider.");
  }
}

export function cancelAdd() {
  if (!state.adding) return;
  state.adding = null;
  clearFieldErrors();
  renderAll();
  setStatus("Đã hủy thêm mới.", "");
}

export function renameProvider(pid = state.activeProvider) {
  try {
    if (!pid || !state.values?.providers?.[pid]) throw new Error("Chưa chọn provider.");
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
    fail(error, "Không đổi tên được.");
  }
}

export function deleteProvider(pid = state.activeProvider) {
  try {
    if (!pid || !state.values?.providers?.[pid]) throw new Error("Chưa chọn provider.");
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
    fail(error, "Không xóa được provider.");
  }
}

export function setDefaultProvider(pid = state.activeProvider) {
  try {
    if (!pid || !state.values?.providers?.[pid]) throw new Error("Chưa chọn provider.");
    const values = clone(state.values);
    values.defaultProvider = pid;
    state.values = values;
    renderAll();
    setStatus(`Provider mặc định: "${pid}". Nhớ Lưu cấu hình.`, "success");
  } catch (error) {
    fail(error, "Không đặt mặc định được.");
  }
}

export function addModel() {
  if (state.adding === "model") {
    el.model.focus();
    return;
  }
  if (!state.activeProvider || !state.values?.providers?.[state.activeProvider]) {
    setStatus("Thêm provider trước khi thêm model.", "error");
    return;
  }
  clearFieldErrors();
  state.adding = "model";
  renderAll();
  el.model.focus();
  setStatus(`Điền Model ID rồi bấm Tạo model (thuộc provider "${state.activeProvider}").`, "");
}

export function createModel() {
  try {
    clearFieldErrors();
    const name = el.model.value.trim();
    if (!name) throw new FieldError("Nhập Model ID trước khi Tạo.", "model");
    const values = clone(state.values);
    const exists = Boolean(values.models?.[name]);
    if (!exists) applyModelFormToState(values, name);
    state.values = values;
    state.activeModel = name;
    state.adding = null;
    renderAll();
    if (exists) setStatus(`Model "${name}" đã tồn tại (provider ${providerOf(name)}).`, "");
    else setStatus(`Đã tạo model "${name}". Nhớ Lưu cấu hình.`, "success");
  } catch (error) {
    fail(error, "Không tạo được model.");
  }
}

export function deleteModel(name = state.activeModel || el.model.value.trim()) {
  try {
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
    fail(error, "Không xóa được model.");
  }
}

export function setDefaultModel(name = el.model.value.trim()) {
  try {
    if (!name) throw new FieldError("Nhập Model ID trước.", "model");
    const values = clone(state.values);
    values.defaultModel = name;
    state.values = values;
    state.activeModel = name;
    renderAll();
    setStatus(`Model mặc định: "${name}". Nhớ Lưu cấu hình.`, "success");
  } catch (error) {
    fail(error, "Không đặt mặc định được.");
  }
}

export async function verifyProvider() {
  clearFieldErrors();
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
    refreshModelSuggest();
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
    fail(error, "Không kiểm tra được provider.");
  } finally {
    setBusy(false);
  }
}

export async function testProvider() {
  clearFieldErrors();
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
    fail(error, "Test API thất bại.");
  } finally {
    setBusy(false);
  }
}

export async function saveProvider() {
  if (state.adding === "provider") {
    setStatus("Đang thêm provider mới — bấm Tạo provider (hoặc Hủy) trước khi Lưu.", "error");
    el.providerName.focus();
    return;
  }
  if (state.adding === "model") {
    setStatus("Đang thêm model mới — bấm Tạo model (hoặc Hủy) trước khi Lưu.", "error");
    el.model.focus();
    return;
  }
  clearFieldErrors();
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
    fail(error, "Không lưu được cấu hình.");
  } finally {
    setBusy(false);
  }
}
