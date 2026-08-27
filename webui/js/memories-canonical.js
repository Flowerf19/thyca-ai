// Canonical files (SOUL/USER/IDENTITY.md): page builder + inline editor
// (split from memories.js). Event hooks come in via bindCanonical(root, hooks).

import { formatMarkdown } from "./markdown.js";
import { escapeHtml } from "./util.js";

export function canonicalPages(files) {
  const order = { "USER.md": 0, "SOUL.md": 1, "IDENTITY.md": 2 };
  const descs = {
    "USER.md": "thông tin về bạn — tên, sở thích, bối cảnh",
    "SOUL.md": "cách agent nói chuyện và trả lời",
    "IDENTITY.md": "danh tính và giới hạn của agent",
  };
  const list = files
    .filter((file) => file && file.name)
    .sort((a, b) => (order[a.name] ?? 9) - (order[b.name] ?? 9));
  const sections = [];
  let prevLayer = null;
  for (const file of list) {
    const name = String(file.name);
    const layer = name === "USER.md" ? "user" : "self";
    // ngăn giữa 2 lớp: ghi chú của user — ghi chú của bản thân
    if (prevLayer === "user" && layer === "self") {
      sections.push(`<div class="canon-divider" role="separator"><span>chỉ dẫn cho agent</span></div>`);
    }
    prevLayer = layer;
    const content = String(file.content || "");
    sections.push(`<article class="mem-entry">
        <div class="canon-head">
          <h3>${escapeHtml(name)}</h3>
          <p class="canon-desc">${escapeHtml(descs[name] || "")}</p>
        </div>
        <div class="mem-md" data-canonical="${escapeHtml(name)}" data-raw="${escapeHtml(content)}">${formatMarkdown(content) || "(trống)"}</div>
        <div class="mem-entry-actions mem-canonical-actions">
          <button type="button" class="mem-reinforce" data-canonical-edit="${escapeHtml(name)}">Sửa</button>
        </div>
      </article>`);
  }
  return [
    {
      title: "Hồ sơ",
      date: `${escapeHtml(String(list.length))} file · inject mỗi lượt`,
      tag: "",
      tone: "memories",
      kicker: "canonical · prompt",
      body: `<div class="canon-list">${sections.join("")}</div>`,
    },
  ];
}

// ---- sửa file canonical (SOUL/USER/IDENTITY.md) ----

function bindOnce(button, handler) {
  if (button.dataset.bound) return;
  button.dataset.bound = "1";
  button.addEventListener("click", handler);
}

export function bindCanonical(root, hooks) {
  root.querySelectorAll("[data-canonical-edit]").forEach((button) => {
    bindOnce(button, () => openCanonicalEditor(root, button.dataset.canonicalEdit));
  });
  // Lưu/Hủy là nút động (tạo khi mở editor) → delegation trên .mem-md
  root.querySelectorAll("[data-canonical]").forEach((wrap) => {
    if (wrap.dataset.canonicalBound) return;
    wrap.dataset.canonicalBound = "1";
    wrap.addEventListener("click", (event) => {
      const save = event.target.closest("[data-canonical-save]");
      if (save) {
        const text = wrap.querySelector("textarea")?.value;
        if (typeof text !== "string") return;
        save.disabled = true;
        void saveCanonical(save.dataset.canonicalSave, text, wrap, save, hooks);
        return;
      }
      const cancel = event.target.closest("[data-canonical-cancel]");
      if (cancel) {
        delete wrap.dataset.editing;
        wrap.innerHTML = formatMarkdown(wrap.dataset.raw || "") || "(trống)";
      }
    });
  });
}

function cssEscapeAttr(value) {
  return window.CSS?.escape ? CSS.escape(value) : String(value).replace(/"/g, '\\"');
}

function openCanonicalEditor(root, name) {
  const wrap = root.querySelector(`[data-canonical="${cssEscapeAttr(name)}"]`);
  if (!wrap || wrap.dataset.editing) return;
  wrap.dataset.editing = "1";
  const raw = wrap.dataset.raw || "";
  wrap.innerHTML = `<textarea class="mem-edit-body" spellcheck="false"></textarea>
      <div class="mem-entry-actions mem-canonical-actions">
        <button type="button" class="mem-reinforce" data-canonical-save="${escapeHtml(name)}">Lưu</button>
        <button type="button" class="mem-forget" data-canonical-cancel="${escapeHtml(name)}">Hủy</button>
      </div>`;
  const area = wrap.querySelector("textarea");
  if (area) {
    area.value = raw;
    area.focus();
  }
}

async function saveCanonical(name, content, wrap, button, hooks) {
  button.disabled = true;
  try {
    /* static mock */
    const res = await fetch("/api/memory/canonical", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, content }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    wrap.dataset.raw = content;
    delete wrap.dataset.editing;
    wrap.innerHTML = formatMarkdown(content) || "(trống)";
    // refresh stats để số liệu khớp
    hooks.onForget?.();
  } catch {
    button.disabled = false;
    window.alert("Không lưu được file. Thử lại nhé.");
  }
}

// Bấm card gợi ý: mở đúng ngày trong "Theo ngày" rồi nhảy tới leaf
