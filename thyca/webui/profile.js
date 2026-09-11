import { getJson, postJson } from "./backend/api.js";
import { decodeHash } from "./backend/format.js";
import { formatMarkdown } from "./backend/markdown.js";
import { selectCanonical } from "./backend/memory-data.js";

const el = {
  nav: document.querySelector("#profile-nav"),
  title: document.querySelector("#profile-title"),
  note: document.querySelector("#profile-note"),
  content: document.querySelector("#profile-content"),
  status: document.querySelector("#profile-status"),
  edit: document.querySelector("#profile-edit"),
  dialog: document.querySelector("#canonical-dialog"),
  form: document.querySelector("#canonical-form"),
  name: document.querySelector("#canonical-name"),
  field: document.querySelector("#canonical-content"),
  dialogStatus: document.querySelector("#canonical-dialog-status"),
  cancel: document.querySelector("#cancel-canonical"),
};

const state = {
  files: [],
  active: "",
  opener: null,
  busy: false,
};

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-status profile-status${kind ? ` is-${kind}` : ""}`;
}

function setDialogStatus(message = "", kind = "") {
  el.dialogStatus.textContent = message;
  el.dialogStatus.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function setBusy(busy) {
  state.busy = busy;
  for (const control of el.dialog.querySelectorAll("button, input, textarea")) control.disabled = busy;
  el.nav.setAttribute("aria-busy", String(busy));
  el.edit.disabled = busy || !activeFile();
}

function activeFile() {
  return state.files.find((file) => file.name === state.active) || null;
}

function navButton(file) {
  // Same shape as the static rows in memories.html / dashboard.html: icon and
  // name directly inside .session-item. The chat variant wraps them in
  // .session-body, which lays the row out differently (taller, name shifted).
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-item";
  button.dataset.file = file.name;
  button.setAttribute("aria-pressed", String(file.name === state.active));

  const icon = document.createElement("span");
  icon.className = "session-icon";
  icon.setAttribute("aria-hidden", "true");
  const name = document.createElement("span");
  name.className = "session-name";
  name.textContent = file.title;
  button.append(icon, name);
  button.addEventListener("click", () => show(file.name, { focus: true }));
  return button;
}

// Rebuilding the list would drop keyboard focus with the removed button, so
// the buttons are built once and only their state changes afterwards.
function renderNav() {
  el.nav.replaceChildren(...state.files.map(navButton));
  markNav();
}

function markNav() {
  for (const button of el.nav.querySelectorAll("[data-file]")) {
    const active = button.dataset.file === state.active;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}

// One file on screen at a time, rendered as markdown with the chat renderer:
// escaping and link/image rules stay identical to a reply body.
function show(name, { focus = false } = {}) {
  const file = state.files.find((item) => item.name === name);
  if (!file) return;
  state.active = file.name;
  el.title.textContent = file.title;
  el.note.textContent = file.description;
  if (file.content.trim()) {
    el.content.innerHTML = formatMarkdown(file.content);
  } else {
    el.content.textContent = "";
  }
  el.edit.disabled = state.busy;
  markNav();
  // Clear whatever the previous switch left behind ("Đang đọc hồ sơ…").
  setStatus();
  const surface = document.querySelector(".profile-surface");
  if (surface) {
    surface.scrollTop = 0;
    requestAnimationFrame(() => { surface.scrollTop = 0; });
  }
  const hash = `#${file.name}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  if (focus) activeNavButton()?.focus();
}

function activeNavButton() {
  return el.nav.querySelector(`[data-file="${state.active}"]`);
}

async function load() {
  setStatus("Đang đọc hồ sơ…");
  el.nav.setAttribute("aria-busy", "true");
  try {
    const stats = await getJson("/api/memory/stats");
    state.files = selectCanonical(stats.files);
    renderNav();
    const wanted = decodeHash(location.hash);
    const first = state.files.find((file) => file.name === wanted) || state.files[0];
    if (!first) {
      el.content.textContent = "";
      setStatus("Chưa có file hồ sơ.");
      return;
    }
    show(first.name);
  } catch (error) {
    el.nav.replaceChildren();
    el.content.textContent = "";
    setStatus(messageOf(error, "Không tải được hồ sơ."), "error");
  } finally {
    el.nav.setAttribute("aria-busy", "false");
  }
}

function openDialog(opener) {
  const file = activeFile();
  if (!file) return;
  state.opener = opener;
  el.name.value = file.name;
  el.field.value = file.content;
  setDialogStatus();
  el.dialog.showModal();
  el.field.focus();
}

function bind() {
  el.edit.addEventListener("click", () => openDialog(el.edit));
  el.cancel.addEventListener("click", () => el.dialog.close());
  el.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = el.name.value;
    const content = el.field.value;
    // The textarea is not `required`: blanking a profile is a real action, so
    // it asks first instead of being blocked by a native message.
    if (!content.trim() && !confirm(`Xoá nội dung “${name}”?`)) return;
    setBusy(true);
    setDialogStatus("Đang lưu…");
    try {
      await postJson("/api/memory/canonical", { name, content });
      el.dialog.close();
      await load();
      setStatus(`Đã lưu ${name}.`, "success");
    } catch (error) {
      setDialogStatus(messageOf(error, "Không lưu được file."), "error");
    } finally {
      setBusy(false);
    }
  });
  el.dialog.addEventListener("close", () => state.opener?.focus());
  window.addEventListener("hashchange", () => {
    const wanted = decodeHash(location.hash);
    if (state.files.some((file) => file.name === wanted)) show(wanted);
  });
}

bind();
void load();
