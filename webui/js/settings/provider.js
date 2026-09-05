import { state } from "../shared/state.js";
import {
  costInputs,
  esc,
  fieldHtml,
  hasStoredKey,
  persist,
  refreshPages,
  schema,
  schemaValues,
  setStatus,
} from "./schema.js";

export function providerFieldsHtml() {
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

export function providerCard(baseUrl, isPrimary) {
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

export function providerSelectHtml() {
  const options = knownProviders()
    .map((p) => `<option value="${esc(p.url)}">${esc(p.label)}</option>`)
    .join("");
  return `<label class="settings-field"><span>Provider</span>
      <select data-add-provider>${options}</select>
    </label>`;
}

function reasoningHtml() {
  const field = schema.sections
    .flatMap((s) => s.fields)
    .find((f) => f.key === "provider.reasoningEffort");
  if (!field) return "";
  return fieldHtml(field);
}

export function addModelPage(meta) {
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

export function bindProviderForm(root) {
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
