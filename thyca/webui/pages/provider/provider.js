import { getJson, postJson } from "../../shared/js/api.js";
import { effortChoicesFor, fillEffortSelect } from "../../shared/js/reasoning-effort.js";

const PRESETS = {
  openai: "https://api.openai.com/v1",
  openrouter: "https://openrouter.ai/api/v1",
  local: "http://127.0.0.1:11434/v1",
  custom: "",
};

const PROVIDER_ID_RE = /^[A-Za-z0-9_-]+$/;

const el = {
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

// Mức suy luận chuẩn backend nhận trực tiếp khi model không khai thinking map.
const STANDARD_EFFORTS = ["low", "high", "max"];

const state = {
  values: null,
  schema: null,
  saved: null,
  meta: {},
  verified: {},
  activeProvider: "",
  activeModel: "",
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

function setBusy(busy) {
  state.busy = busy;
  for (const control of allControls()) control.disabled = busy;
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

function providerIds() {
  return Object.keys(state.values?.providers || {});
}

function providerOf(modelName) {
  return state.values?.models?.[modelName]?.provider || state.values?.defaultProvider || "";
}

function modelsOf(providerId) {
  return Object.keys(state.values?.models || {})
    .filter((name) => providerOf(name) === providerId)
    .sort((a, b) => a.localeCompare(b));
}

function modelSpec(name) {
  return state.values?.models?.[name] || state.values?.pricing?.[name] || {};
}

function fillEffortOptions(selected) {
  fillEffortSelect(
    el.reasoning,
    effortChoicesFor(state.schema, state.values, el.model.value.trim()),
    selected,
    state.schema,
  );
}

function fillProviderEffort(selected) {
  fillEffortSelect(el.providerEffort, STANDARD_EFFORTS, selected, state.schema);
}

function syncProviderApi(entry) {
  el.providerApi.value = entry.api === "openai_responses" ? "openai_responses" : "openai_chat";
}

function fillDatalist() {
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

function fillSelect(select, options, selected) {
  select.replaceChildren(...options.map((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    return option;
  }));
  if (options.includes(selected)) select.value = selected;
}

function renderProviders() {
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

function renderModels() {
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

function applyModel(name) {
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

function renderLimits() {
  const limits = state.values?.limits || {};
  el.limitsLoopMax.value = limits.loopMax ?? 200;
  el.limitsHotTailKB.value = limits.hotTailKB ?? 4;
  el.limitsContextTokens.value = limits.contextTokens ?? 272000;
}

function renderAll() {
  renderProviders();
  renderModels();
  const entry = state.values?.providers?.[state.activeProvider];
  if (entry) syncProviderApi(entry);
  fillDatalist();
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

function askProviderId(title, initial = "") {
  const raw = window.prompt(title, initial);
  if (raw === null) return null;
  const id = raw.trim();
  if (!id) throw new Error("Tên provider không được để trống.");
  if (!PROVIDER_ID_RE.test(id)) throw new Error("Tên provider chỉ gồm chữ, số, _ và -.");
  return id;
}

// Ghi các field đang hiện vào state.values (chưa POST). Ném Error khi invalid.
function applyFormToState(values) {
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
  }
  if (efforts.length) entry2.reasoningEfforts = efforts;
  else delete entry2.reasoningEfforts;
  values.models[model] = entry2;
  return values;
}

// Popup thông báo sau khi lưu thành công và provider sẵn sàng.
let readyDialog = null;

function showReadyPopup() {
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

function addProvider() {
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

function renameProvider() {
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

function deleteProvider() {
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

function addModel() {
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

function deleteModel() {
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

function setDefaultModel() {
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

async function verifyProvider() {
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

async function testProvider() {
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

async function saveProvider() {
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
