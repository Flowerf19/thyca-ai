// Settings surface: a notebook mode (like Chat/Memories) with two pages.
// Page "Thêm model": provider form (baseUrl + API key + thinking) + add-model
// form (ID + Tải model probe + optional per-model baseUrl + token prices,
// then asks "Đặt làm mặc định?") + limits.
// Page "Model hiện có": one card per registered model — default badge,
// Set-default, Edit (id/baseUrl/prices), Delete.
// Field lists come from GET /api/config schema, so new backend fields appear
// without JS changes.
import { postJson } from "./util.js";
import { modes } from "./data.js";
import { state } from "./state.js";
import { el } from "./dom.js";
import { resetToNewChatPage } from "./chat.js";
import { renderPage } from "./render.js";

let schema = null;
let schemaValues = {};
let hasStoredKey = false;
let modelOptions = [];
let modelOptionsEndpoint = "";
const modelCache = new Map();
let modelFetching = false;
let defaultModel = "";

export function initSettings() {
  return { open: () => renderModeSettings(), isOpen: () => state.activeMode === "settings" };
}

// Open the settings page (used by the boot gate when provider isn't ready).
export async function renderModeSettings() {
  const { renderMode } = await import("./render.js");
  await renderMode("settings");
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

// ---- shared HTML helpers ----

function esc(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function nested(obj, dotted) {
  return dotted.split(".").reduce((acc, part) => (acc == null ? acc : acc[part]), obj);
}

function fieldHtml(field) {
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

function costInputs(prefix, model) {
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

function readCostInputs(root, attr) {
  const costs = {};
  for (const input of root.querySelectorAll(`[data-${attr}-cost]`)) {
    costs[input.dataset[`${attr}Cost`]] = Number(input.value) || 0;
  }
  return costs;
}

function providerFieldsHtml() {
  // Provider cards: base URL + API key, each with its own Lưu button.
  // "+ Thêm nhà cung cấp" appends the same card shape, blank.
  const global = schemaValues.provider || {};
  const cards = [providerCard(global.baseUrl || "", true)];
  const extraBaseUrls = [...new Set(
    Object.values(schemaValues.models || {})
      .map((m) => m.baseUrl)
      .filter(Boolean),
  )];
  for (const url of extraBaseUrls) {
    if (url !== global.baseUrl) cards.push(providerCard(url, false));
  }
  return `<div class="provider-list" data-provider-list>${cards.join("")}</div>
      <button type="button" class="settings-button" data-provider-add>+ Thêm nhà cung cấp</button>`;
}

function providerCard(baseUrl, isPrimary) {
  const savedUrl = isPrimary ? schemaValues.provider?.baseUrl || "" : baseUrl;
  const keyField = isPrimary
    ? `<label class="settings-field"><span>API key</span>
        <input class="settings-input" type="password" data-provider-key value="" placeholder="${hasStoredKey ? "•••••••• (đã lưu — để trống để giữ)" : "dán API key"}" autocomplete="new-password" />
      </label>`
    : "";
  return `<div class="provider-card" data-provider-card data-saved-url="${esc(savedUrl)}">
      <div class="canon-head">
        <h4>${isPrimary ? "Mặc định" : "Nhà cung cấp"}</h4>
        ${isPrimary ? "" : '<button type="button" class="mem-reinforce" data-provider-remove>Xóa</button>'}
      </div>
      <label class="settings-field"><span>Base URL</span>
        <input class="settings-input" type="text" data-provider-url value="${esc(baseUrl)}" placeholder="https://…/v1" spellcheck="false" />
      </label>
      ${keyField}
      <div class="mem-entry-actions"><button type="button" class="settings-button is-primary" data-provider-save>Lưu</button></div>
    </div>`;
}

function limitsFieldsHtml() {
  const sections = [];
  for (const section of schema.sections) {
    if (section.key !== "limits") continue;
    const visible = section.fields.filter((field) => !field.hidden);
    if (!visible.length) continue;
    sections.push(visible.map((field) => fieldHtml(field)).join(""));
  }
  return sections.join("");
}

// Distinct provider endpoints: the global one plus every registered
// model's own baseUrl. The add-model flow picks one, then loads its models.
function knownProviders() {
  const list = [];
  const global = schemaValues.provider?.baseUrl || "";
  if (global) list.push({ url: "", label: `${global} (mặc định)` });
  for (const model of Object.values(schemaValues.models || {})) {
    if (model.baseUrl && !list.some((p) => p.url === model.baseUrl)) {
      list.push({ url: model.baseUrl, label: model.baseUrl });
    }
  }
  return list;
}

function providerSelectHtml() {
  const options = knownProviders()
    .map((p) => `<option value="${esc(p.url)}">${esc(p.label)}</option>`)
    .join("");
  return `<label class="settings-field"><span>Provider</span>
      <select data-add-provider>${options}</select>
    </label>`;
}

// ---- page 0: providers + add model + limits ----

function reasoningHtml() {
  const field = schema.sections
    .flatMap((s) => s.fields)
    .find((f) => f.key === "provider.reasoningEffort");
  if (!field) return "";
  return fieldHtml(field);
}

function addModelPage(meta) {
  return {
    title: "Thêm model",
    hideTitle: true,
    date: meta.hasApiKey ? "API key đã lưu" : "chưa có API key",
    tag: "",
    tone: "settings",
    kicker: "~/.thyca · config.json",
    body: `<form class="settings-form" id="settings-form" novalidate>
        <section class="settings-section">
          <h3 class="settings-legend">Nhà cung cấp</h3>
          ${providerFieldsHtml()}
        </section>
        <section class="settings-section">
          <h3 class="settings-legend">Thêm model</h3>
          <div class="settings-box" data-add-model-box>
            ${providerSelectHtml()}
            <label class="settings-field"><span>Model ID</span>
              <div class="settings-row" id="add-model-row">
                <input class="settings-input" type="text" data-add-model spellcheck="false" autocomplete="off" placeholder="provider/model-id" />
              </div>
            </label>
            ${reasoningHtml()}
            ${limitsFieldsHtml()}
            <div class="settings-field settings-pricing-field"><span>Giá token (USD / 1M)</span>${costInputs("add", null)}
            </div>
            <div class="mem-entry-actions"><button type="button" class="settings-button is-primary" data-add-save>Thêm</button></div>
          </div>
          <div class="add-model-extra" data-add-model-extra></div>
          <button type="button" class="settings-button settings-block-button" data-add-model-box-btn>+ Thêm model</button>
        </section>
        <div class="settings-actions-row">
          <p class="settings-status" aria-live="polite"></p>
        </div>
      </form>`,
  };
}

// ---- page 1: registered models ----

function modelsPage(count) {
  const registered = schemaValues.models || {};
  const cards = Object.entries(registered)
    .map(([name, model]) => modelCard(name, model))
    .join("");
  return {
    title: "Model hiện có",
    hideTitle: true,
    date: `${count} model`,
    tag: "",
    tone: "settings",
    kicker: "models · mặc định + giá token",
    body: `<div class="pricing-page">
        <div class="pricing-list">${cards || `<p class="settings-hint">Chưa có model nào — thêm ở trang "Thêm model".</p>`}</div>
        <p class="settings-status" aria-live="polite"></p>
      </div>`,
  };
}

function modelCard(name, model) {
  const isDefault = name === defaultModel;
  const baseNote = model.baseUrl ? esc(model.baseUrl) : "dùng Base URL chung";
  return `<article class="mem-entry pricing-card${isDefault ? " is-default" : ""}" data-model="${esc(name)}">
      <div class="canon-head">
        <h3 class="pricing-name-text">${esc(name)}${isDefault ? ' <span class="page-tag page-tag-chat">mặc định</span>' : ""}</h3>
        <div class="mem-entry-actions">
          ${isDefault ? "" : '<button type="button" class="mem-reinforce" data-set-default>Đặt mặc định</button>'}
          <button type="button" class="mem-reinforce" data-model-edit>Sửa</button>
          <button type="button" class="mem-reinforce" data-model-delete>Xóa</button>
        </div>
      </div>
      <p class="settings-hint">${baseNote}</p>
      ${costInputs("card", model)}
      <div class="pricing-edit-row" hidden>
        <label class="settings-field"><span>Model ID</span><input type="text" class="settings-input" data-edit-name value="${esc(name)}" spellcheck="false" /></label>
        <label class="settings-field"><span>Base URL riêng (trống = dùng chung)</span><input type="text" class="settings-input" data-edit-baseurl value="${esc(model.baseUrl || "")}" spellcheck="false" placeholder="https://…/v1" /></label>
        ${costInputs("edit", model)}
        <div class="mem-entry-actions">
          <button type="button" class="mem-reinforce" data-model-save>Lưu</button>
          <button type="button" class="mem-reinforce" data-model-cancel>Hủy</button>
        </div>
      </div>
    </article>`;
}

// ---- event binding (called from render.js after renderPage) ----

export function bindSettings(root) {
  bindProviderForm(root);
  bindAddModel(root);
  bindModelCards(root);
}

function settingsForm(root) {
  // Guard: after a render/reload `root` may no longer host the settings DOM.
  return root && typeof root.querySelector === "function" ? root.querySelector("#settings-form") : null;
}

function setStatus(root, message, kind = "") {
  const status =
    (settingsForm(root)?.querySelector(".settings-status") ||
      (root && typeof root.querySelector === "function" ? root.querySelector(".settings-status") : null));
  if (status) {
    status.textContent = message;
    status.className = `settings-status${kind ? ` is-${kind}` : ""}`;
  }
}

function bindProviderForm(root) {
  const form = root.querySelector("#settings-form");
  if (!form) return;
  // Fresh DOM after each render: drop the stale flag from a previous page.
  delete form.dataset.bound;
  form.dataset.bound = "1";
  form.addEventListener("submit", (event) => event.preventDefault());
  const addProvider = form.querySelector("[data-provider-add]");
  if (addProvider && !addProvider.dataset.bound) {
    addProvider.dataset.bound = "1";
    addProvider.addEventListener("click", () => {
      const list = form.querySelector("[data-provider-list]");
      if (!list) return;
      list.insertAdjacentHTML("beforeend", providerCard("", false));
      bindProviderCards(list.lastElementChild, root);
      list.lastElementChild.querySelector("[data-provider-url]")?.focus();
    });
  }
  bindProviderCards(form, root);
}

// Per-provider Lưu/Xóa. Cards carry data-bound so rebinding never double-fires.
function bindProviderCards(scope, root) {
  const cards = scope.classList?.contains("provider-card")
    ? [scope]
    : [...scope.querySelectorAll("[data-provider-card]")];
  cards.forEach((card) => {
    if (card.dataset.bound) return;
    card.dataset.bound = "1";
    card.querySelector("[data-provider-save]")?.addEventListener("click", () => void saveProviderCard(root, card));
    card.querySelector("[data-provider-remove]")?.addEventListener("click", async () => {
      const url = card.dataset.savedUrl || card.querySelector("[data-provider-url]")?.value.trim();
      if (!url) {
        card.remove(); // blank card — nothing stored
        return;
      }
      if (!window.confirm(`Xóa nhà cung cấp ${url}?`)) return;
      // Drop every model registered against this endpoint.
      const models = { ...(schemaValues.models || {}) };
      for (const [name, m] of Object.entries(models)) {
        if (m.baseUrl === url) delete models[name];
      }
      try {
        await persist(root, { models });
        await refreshPages(root, state.activePageIndex);
        setStatus(root, `Đã xóa ${url}.`, "ok");
      } catch (error) {
        setStatus(root, error instanceof Error ? error.message : "Không xóa được.", "error");
      }
    });
  });
}

async function saveProviderCard(root, card) {
  const url = card.querySelector("[data-provider-url]")?.value.trim();
  const key = card.querySelector("[data-provider-key]")?.value.trim() || "";
  if (!url) {
    setStatus(root, "Cần Base URL.", "error");
    return;
  }
  const isPrimary = card === card.parentElement?.querySelector("[data-provider-card]");
  try {
    const patch = {};
    if (isPrimary) {
      // Global provider: baseUrl + optional key (empty = keep stored).
      patch.providerBaseUrl = url;
      if (key) patch.providerApiKey = key;
    } else {
      // Secondary provider: remap every model registered on the old endpoint.
      const oldUrl = card.dataset.savedUrl;
      const models = { ...(schemaValues.models || {}) };
      if (oldUrl && oldUrl !== url) {
        for (const m of Object.values(models)) {
          if (m.baseUrl === oldUrl) m.baseUrl = url;
        }
        patch.models = models;
      }
      card.dataset.savedUrl = url;
      // Secondary cards have no key field (config stores one key); a model
      // bound to this URL still needs a key — require the primary's key.
      // NOTE: server luôn mask apiKey="" nên phải check hasStoredKey
      // (meta.hasApiKey), không check schemaValues.provider.apiKey.
      if (!hasStoredKey && !key) {
        setStatus(root, "Lưu key ở nhà cung cấp Mặc định trước (model dùng chung key).", "error");
        return;
      }
    }
    await persist(root, patch);
    // Refresh để dropdown Provider / placeholder / hasStoredKey đồng bộ,
    // không cần F5 (TASK-021). renderPage rebuild DOM nên không cần xóa
    // keyInput tay — providerCard luôn render value="".
    // refreshPages bọc riêng: hydrate fail không được nuốt "Đã lưu"
    // khi persist đã thành công (user bấm Lưu nữa sẽ lưu trùng).
    try {
      await refreshPages(root, state.activePageIndex);
    } catch {
      /* giữ DOM cũ, vẫn báo đã lưu bên dưới */
    }
    setStatus(root, `Đã lưu ${url}.`, "ok");
  } catch (error) {
    setStatus(root, error instanceof Error ? error.message : "Không lưu được.", "error");
  }
}

function bindModelInput(input, root) {
  if (!input || input.dataset.bound) return;
  input.dataset.bound = "1";
  input.autocomplete = "off";
  input.addEventListener("focus", () => {
    // Focus tự fetch khi chưa có list (TASK-030): lần đầu mở trang
    // modelOptions rỗng, không bắt user gạt Provider qua lại.
    if (!modelOptions.length && !modelFetching) {
      void fetchModels(root, input).then(() => {
        openModelDropdown(input, input.value);
      });
      return;
    }
    openModelDropdown(input, input.value);
  });
  input.addEventListener("input", () => openModelDropdown(input, input.value));
}

function bindAddModel(root) {
  bindModelInput(root.querySelector("[data-add-model]"), root);
  // Picking a provider auto-loads its model list (no button).
  // Bind mọi select (box chính + box clone) thay vì chỉ box đầu.
  root.querySelectorAll("[data-add-provider]").forEach((providerSel) => {
    if (providerSel.dataset.bound) return;
    providerSel.dataset.bound = "1";
    providerSel.addEventListener("change", () => {
      const box = providerSel.closest("[data-add-model-box], [data-extra-box]");
      const input = box?.querySelector("[data-add-model]");
      modelOptions = [];
      modelOptionsEndpoint = "";
      void fetchModels(root, input).then(() => {
        if (input && modelOptions.length) openModelDropdown(input, input.value);
      });
    });
  });
  const addSave = root.querySelector("[data-add-save]");
  if (addSave) {
    delete addSave.dataset.bound;
    addSave.dataset.bound = "1";
    addSave.addEventListener("click", () => void addModel(root));
  }
  // "+ Thêm model": clone the add-model box as a blank extra block.
  const addBoxBtn = root.querySelector("[data-add-model-box-btn]");
  if (addBoxBtn) {
    delete addBoxBtn.dataset.bound;
    addBoxBtn.dataset.bound = "1";
    addBoxBtn.addEventListener("click", () => {
      const source = root.querySelector("[data-add-model-box]");
      const extra = root.querySelector("[data-add-model-extra]");
      if (!source || !extra) return;
      const clone = source.cloneNode(true);
      clone.removeAttribute("data-add-model-box");
      clone.dataset.extraBox = "1";
      // cloneNode không copy listener: xóa flag bound rồi bind lại
      // input + select trong box clone (TASK-031).
      clone.querySelectorAll("[data-bound]").forEach((n) => delete n.dataset.bound);
      clone.querySelectorAll("[data-add-model], [data-add-provider]").forEach((n) => delete n.dataset.bound);
      // Extra boxes skip limits (form-level, saved once) but keep their own
      // Thêm button, which binds to the same addModel flow.
      clone.querySelectorAll("[data-key^=\"limits.\"]").forEach((n) => n.closest(".settings-field")?.remove());
      extra.appendChild(clone);
      bindModelInput(clone.querySelector("[data-add-model]"), root);
      clone.querySelectorAll("[data-add-provider]").forEach((sel) => {
        if (sel.dataset.bound) return;
        sel.dataset.bound = "1";
        sel.addEventListener("change", () => {
          const input = clone.querySelector("[data-add-model]");
          modelOptions = [];
          modelOptionsEndpoint = "";
          void fetchModels(root, input).then(() => {
            if (input && modelOptions.length) openModelDropdown(input, input.value);
          });
        });
      });
      clone.querySelector("[data-add-model]")?.focus();
    });
  }
}

async function addModel(root) {
  // Main box + cloned extra boxes each add one model.
  const boxes = [
    root.querySelector("[data-add-model-box]"),
    ...root.querySelectorAll("[data-extra-box]"),
  ].filter(Boolean);
  const models = { ...(schemaValues.models || {}) };
  const added = [];
  for (const box of boxes) {
    const name = box.querySelector("[data-add-model]")?.value.trim();
    if (!name) continue; // blank extra box — skip
    const baseUrl = box.querySelector("[data-add-provider]")?.value.trim() || "";
    const costs = readCostInputs(box, "add");
    models[name] = { baseUrl, input: costs.input || 0, cache: costs.cache || 0, output: costs.output || 0 };
    added.push(name);
  }
  if (!added.length) {
    setStatus(root, "Cần Model ID.", "error");
    return;
  }
  const firstName = added[0];
  // Ask before writing: a single persist carries the chosen default.
  const makeDefault = added.length === 1 ? window.confirm(`Thêm ${firstName}? Đặt làm model mặc định luôn?`) : false;
  try {
    await persist(root, { models, providerModel: makeDefault ? firstName : undefined });
    if (makeDefault) defaultModel = firstName;
    root.querySelector("[data-add-model-extra]")?.replaceChildren();
    await refreshPages(root, state.activePageIndex);
    const note = added.length > 1 ? `Đã thêm ${added.length} model.` : makeDefault ? `Đã thêm ${firstName} (mặc định).` : `Đã thêm ${firstName}.`;
    setStatus(root, note, "ok");
  } catch (error) {
    setStatus(root, error instanceof Error ? error.message : "Không thêm được model.", "error");
  }
}

function bindModelCards(root) {
  root.querySelectorAll(".pricing-card").forEach((card) => {
    delete card.dataset.bound;
    card.dataset.bound = "1";
    card.querySelector("[data-set-default]")?.addEventListener("click", async () => {
      const name = card.dataset.model;
      try {
        await persist(root, { providerModel: name });
        defaultModel = name;
        await refreshPages(root, state.activePageIndex);
        setStatus(root, `Đã đặt ${name} làm mặc định.`, "ok");
      } catch (error) {
        setStatus(root, error instanceof Error ? error.message : "Không lưu được.", "error");
      }
    });
    card.querySelector("[data-model-edit]")?.addEventListener("click", () => {
      const row = card.querySelector(".pricing-edit-row");
      row.hidden = !row.hidden;
      if (!row.hidden) card.querySelector("[data-edit-name]")?.focus();
    });
    card.querySelector("[data-model-cancel]")?.addEventListener("click", () => {
      card.querySelector(".pricing-edit-row").hidden = true;
    });
    card.querySelector("[data-model-delete]")?.addEventListener("click", async () => {
      const name = card.dataset.model;
      if (!window.confirm(`Xóa model ${name}?`)) return;
      const models = { ...(schemaValues.models || {}) };
      delete models[name];
      try {
        const patch = { models };
        if (defaultModel === name) {
          // Never send an empty provider.model — keep the deleted one as a
          // fallback so the config stays valid (user can pick again).
          patch.providerModel = defaultModel;
        }
        await persist(root, patch);
        await refreshPages(root, state.activePageIndex);
        setStatus(root, `Đã xóa ${name}.`, "ok");
      } catch (error) {
        setStatus(root, error instanceof Error ? error.message : "Không xóa được.", "error");
      }
    });
    card.querySelector("[data-model-save]")?.addEventListener("click", async () => {
      const newName = card.querySelector("[data-edit-name]").value.trim();
      const oldName = card.dataset.model;
      if (!newName) {
        setStatus(root, "Model ID không được trống.", "error");
        return;
      }
      const models = { ...(schemaValues.models || {}) };
      const costs = readCostInputs(card, "edit");
      const entry = {
        baseUrl: card.querySelector("[data-edit-baseurl]").value.trim(),
        input: costs.input || 0,
        cache: costs.cache || 0,
        output: costs.output || 0,
      };
      if (newName !== oldName) delete models[oldName];
      models[newName] = entry;
      const patch = { models };
      if (defaultModel === oldName && newName !== oldName) {
        patch.providerModel = newName;
        defaultModel = newName;
      }
      try {
        await persist(root, patch);
        await refreshPages(root, state.activePageIndex);
        setStatus(root, `Đã lưu ${newName}.`, "ok");
      } catch (error) {
        setStatus(root, error instanceof Error ? error.message : "Không lưu được.", "error");
      }
    });
  });
}

// ---- persistence ----

function collectValues(root) {
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

async function persist(root, { models, providerModel, providerBaseUrl, providerApiKey }) {
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
  notifyProviderChanged(root, values.provider);
  return payload;
}

// Provider đổi (baseUrl/model/key mới) → chat phải reset theo (TASK-040/041):
// backend hot-reload config mỗi turn rồi, frontend chỉ cần mở khóa composer,
// refresh kicker model + về phiên mới khi model/baseUrl đổi.
function notifyProviderChanged(root, provider) {
  if (state.activeMode === "chat" || !provider) return;
  void import("./chat.js").then(async ({ resetToNewChatPage, refreshChatKicker }) => {
    try {
      if (typeof refreshChatKicker === "function") await refreshChatKicker();
    } catch { /* giữ kicker cũ, không chặn settings */ }
    const modelChanged = provider.model && provider.model !== state.lastChatModel;
    const urlChanged = provider.baseUrl && provider.baseUrl !== state.lastChatBaseUrl;
    if (modelChanged || urlChanged) resetToNewChatPage();
  });
}

async function refreshPages(root, page = 0) {
  await hydrateSettings();
  renderPage(page);
}

// Probe endpoint = endpoint sẽ lưu (TASK-032): đọc đúng dropdown của box
// chứa input đang focus, không đọc ô input đang gõ dở ở card provider.
function probeTargetFor(input) {
  const box = input?.closest("[data-add-model-box], [data-extra-box]");
  const chosen = box?.querySelector("[data-add-provider]")?.value.trim()
    || document.querySelector("#settings-form [data-add-provider]")?.value.trim()
    || "";
  if (chosen) return { baseUrl: chosen, apiKey: "" };
  // Global provider: baseUrl đã lưu + key (ô nhập trước, key lưu sau).
  const card = document.querySelector("#settings-form [data-provider-card]");
  return {
    baseUrl: card?.querySelector("[data-provider-url]")?.value.trim()
      || schemaValues.provider?.baseUrl || "",
    apiKey: card?.querySelector("[data-provider-key]")?.value || "",
  };
}

async function fetchModels(root, input = null) {
  const { baseUrl, apiKey } = probeTargetFor(input);
  if (!baseUrl) {
    setStatus(root, "Cần Base URL trước khi tải model.", "error");
    return;
  }
  // Cache theo endpoint: đổi provider mới probe lại.
  if (modelCache.has(baseUrl)) {
    modelOptions = modelCache.get(baseUrl);
    modelOptionsEndpoint = baseUrl;
    return;
  }
  if (modelFetching) return;
  modelFetching = true;
  setStatus(root, "Đang tải danh sách model…");
  try {
    const payload = await postJson("/api/onboarding/verify", {
      baseUrl,
      apiKey: typeof apiKey === "string" ? apiKey : (apiKey?.value ?? ""),
    });
    modelOptions = payload.models || [];
    modelOptionsEndpoint = baseUrl;
    modelCache.set(baseUrl, modelOptions);
    const usedSavedKey = !(typeof apiKey === "string" ? apiKey.trim() : apiKey?.value?.trim());
    const keyNote = payload.apiKeyOk
      ? usedSavedKey
        ? "API key đã lưu hợp lệ."
        : "API key vừa nhập hợp lệ."
      : "";
    setStatus(
      root,
      [
        keyNote,
        modelOptions.length
          ? `Đã tải ${modelOptions.length} model — chọn ở ô Model ID.`
          : "Provider không trả model nào — gõ tay tên model.",
      ]
        .filter(Boolean)
        .join(" "),
      modelOptions.length ? "ok" : "",
    );
  } catch (error) {
    setStatus(root, error instanceof Error ? error.message : "Không tải được model.", "error");
  } finally {
    modelFetching = false;
  }
}

// ---- model dropdown (styled list, click to pick, type to filter) ----

let modelDropdown = null;

function closeModelDropdown() {
  if (modelDropdown) {
    modelDropdown.remove();
    modelDropdown = null;
  }
}

document.addEventListener("click", (event) => {
  if (modelDropdown && !modelDropdown.contains(event.target) && !event.target.closest("[data-add-model]")) closeModelDropdown();
});

function openModelDropdown(input, filter = "") {
  closeModelDropdown();
  // Dropdown rỗng phải báo lý do, không return câm (TASK-032).
  if (modelFetching) {
    dropdownNote(input, "Đang tải danh sách model…");
    return;
  }
  if (!modelOptions.length) {
    dropdownNote(input, modelOptionsEndpoint
      ? "Không có model nào ở provider này — gõ tay tên model."
      : "Chưa tải danh sách — bấm vào lại sau giây lát, hoặc gõ tay tên model.");
    return;
  }
  const needle = filter.trim().toLowerCase();
  const matches = needle
    ? modelOptions.filter((id) => id.toLowerCase().includes(needle))
    : modelOptions;
  if (!matches.length) {
    dropdownNote(input, `Không khớp "${filter.trim()}" trong ${modelOptions.length} model — gõ tay tên model.`);
    return;
  }
  const shown = matches.slice(0, 40);
  const box = document.createElement("div");
  box.className = "settings-model-dropdown";
  if (matches.length > shown.length) {
    const more = document.createElement("p");
    more.className = "settings-hint";
    more.textContent = `Hiện 40/${matches.length} — gõ thêm để lọc.`;
    box.appendChild(more);
  }
  for (const id of shown) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "settings-model-item";
    item.textContent = id;
    item.addEventListener("pointerdown", (e) => e.stopPropagation());
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      input.value = id;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      closeModelDropdown();
    });
    box.appendChild(item);
  }
  input.parentElement.appendChild(box);
  modelDropdown = box;
}

function dropdownNote(input, text) {
  const box = document.createElement("div");
  box.className = "settings-model-dropdown";
  const note = document.createElement("p");
  note.className = "settings-hint";
  note.textContent = text;
  box.appendChild(note);
  input.parentElement.appendChild(box);
  modelDropdown = box;
}
