import { postJson } from "../util.js";
import { state } from "../state.js";
import {
  costInputs,
  defaultModel,
  esc,
  modelCache,
  modelFetching,
  modelOptions,
  modelOptionsEndpoint,
  persist,
  readCostInputs,
  refreshPages,
  schemaValues,
  setDefaultModel,
  setModelFetching,
  setModelOptions,
  setModelOptionsEndpoint,
  setStatus,
} from "./schema.js";

export function modelsPage(count) {
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

export function bindAddModel(root) {
  bindModelInput(root.querySelector("[data-add-model]"), root);
  // Picking a provider auto-loads its model list (no button).
  // Bind mọi select (box chính + box clone) thay vì chỉ box đầu.
  root.querySelectorAll("[data-add-provider]").forEach((providerSel) => {
    if (providerSel.dataset.bound) return;
    providerSel.dataset.bound = "1";
    providerSel.addEventListener("change", () => {
      const box = providerSel.closest("[data-add-model-box], [data-extra-box]");
      const input = box?.querySelector("[data-add-model]");
      setModelOptions([]);
      setModelOptionsEndpoint("");
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
          setModelOptions([]);
          setModelOptionsEndpoint("");
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
    if (makeDefault) setDefaultModel(firstName);
    root.querySelector("[data-add-model-extra]")?.replaceChildren();
    await refreshPages(root, state.activePageIndex);
    const note = added.length > 1 ? `Đã thêm ${added.length} model.` : makeDefault ? `Đã thêm ${firstName} (mặc định).` : `Đã thêm ${firstName}.`;
    setStatus(root, note, "ok");
  } catch (error) {
    setStatus(root, error instanceof Error ? error.message : "Không thêm được model.", "error");
  }
}

export function bindModelCards(root) {
  root.querySelectorAll(".pricing-card").forEach((card) => {
    delete card.dataset.bound;
    card.dataset.bound = "1";
    card.querySelector("[data-set-default]")?.addEventListener("click", async () => {
      const name = card.dataset.model;
      try {
        await persist(root, { providerModel: name });
        setDefaultModel(name);
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
        setDefaultModel(newName);
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

// Probe endpoint = endpoint sẽ lưu (TASK-032): đọc đúng dropdown của box
// chứa input đang focus, không đọc ô input đang gõ dở ở card provider.
// Scoped trong root (form settings) để không phụ thuộc DOM global.
function probeTargetFor(root, input) {
  const box = input?.closest("[data-add-model-box], [data-extra-box]");
  const form = root?.querySelector?.("#settings-form") || document.querySelector("#settings-form");
  const chosen = box?.querySelector("[data-add-provider]")?.value.trim()
    || form?.querySelector("[data-add-provider]")?.value.trim()
    || "";
  if (chosen) return { baseUrl: chosen, apiKey: "" };
  // Global provider: baseUrl đã lưu + key (ô nhập trước, key lưu sau).
  const card = form?.querySelector("[data-provider-card]");
  return {
    baseUrl: card?.querySelector("[data-provider-url]")?.value.trim()
      || schemaValues.provider?.baseUrl || "",
    apiKey: card?.querySelector("[data-provider-key]")?.value || "",
  };
}

async function fetchModels(root, input = null) {
  const { baseUrl, apiKey } = probeTargetFor(root, input);
  if (!baseUrl) {
    setStatus(root, "Cần Base URL trước khi tải model.", "error");
    return;
  }
  // Cache theo endpoint: đổi provider mới probe lại.
  if (modelCache.has(baseUrl)) {
    setModelOptions(modelCache.get(baseUrl));
    setModelOptionsEndpoint(baseUrl);
    return;
  }
  if (modelFetching) return;
  setModelFetching(true);
  setStatus(root, "Đang tải danh sách model…");
  try {
    const payload = await postJson("/api/onboarding/verify", {
      baseUrl,
      apiKey: typeof apiKey === "string" ? apiKey : (apiKey?.value ?? ""),
    });
    const next = payload.models || [];
    setModelOptions(next);
    setModelOptionsEndpoint(baseUrl);
    modelCache.set(baseUrl, next);
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
    setModelFetching(false);
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
