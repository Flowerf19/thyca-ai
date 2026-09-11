import { getJson, postJson } from "./backend/api.js";
import { formatDate, formatDateTime } from "./backend/format.js";
import { selectMemories } from "./backend/memory-data.js";

const el = {
  list: document.querySelector("#memory-list"),
  empty: document.querySelector("#memory-empty"),
  search: document.querySelector("#memory-search"),
  status: document.querySelector("#memory-status"),
  viewButtons: [...document.querySelectorAll("[data-view]")],
  dialog: document.querySelector("#memory-dialog"),
  form: document.querySelector("#memory-form"),
  id: document.querySelector("#memory-id"),
  title: document.querySelector("#memory-title"),
  description: document.querySelector("#memory-description"),
  dialogStatus: document.querySelector("#memory-dialog-status"),
  cancel: document.querySelector("#cancel-memory"),
  reinforce: document.querySelector("#reinforce-memory"),
  forget: document.querySelector("#forget-memory"),
};

const state = {
  stats: { leaves: [], files: [] },
  view: "day",
  activeMemory: null,
  opener: null,
  busy: false,
};

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-note memory-status screen-status${kind ? ` is-${kind}` : ""}`;
}

function setDialogStatus(target, message = "", kind = "") {
  target.textContent = message;
  target.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function setBusy(busy) {
  state.busy = busy;
  for (const control of el.dialog.querySelectorAll("button, input, textarea")) control.disabled = busy;
  el.list.setAttribute("aria-busy", String(busy));
}

function memoryCard(memory) {
  const article = document.createElement("article");
  article.className = "memory-card";
  article.dataset.memoryId = memory.id;
  const copy = document.createElement("div");
  copy.className = "memory-copy";
  const heading = document.createElement("h3");
  heading.textContent = memory.title;
  const description = document.createElement("p");
  description.textContent = memory.description;
  const tags = document.createElement("div");
  tags.className = "memory-tags";
  for (const label of [`${memory.uses} lần dùng`, `${memory.searches} lần tìm`]) {
    const tag = document.createElement("span");
    tag.textContent = label;
    tags.append(tag);
  }
  if (memory.expiresAt) {
    const tag = document.createElement("span");
    tag.textContent = `hết hạn ${formatDateTime(memory.expiresAt)}`;
    tags.append(tag);
  }
  copy.append(heading, description, tags);

  const meta = document.createElement("div");
  meta.className = "memory-meta";
  const time = document.createElement("time");
  time.textContent = memory.date === "Không rõ ngày" ? memory.date : formatDate(memory.date);
  const more = document.createElement("button");
  more.className = "memory-more row-action";
  more.type = "button";
  more.disabled = !memory.sessionId;
  more.setAttribute("aria-label", `Sửa trang ${memory.title}`);
  more.textContent = "⋮";
  more.addEventListener("click", () => openMemory(memory, more));
  meta.append(time, more);
  article.append(copy, meta);
  return article;
}

function render() {
  const rows = selectMemories(state.stats.leaves, { view: state.view, query: el.search.value });
  const nodes = [];
  let lastDay = "";
  for (const memory of rows) {
    if (state.view === "day" && memory.date !== lastDay) {
      lastDay = memory.date;
      const heading = document.createElement("h3");
      heading.className = "memory-day";
      heading.textContent = memory.date === "Không rõ ngày" ? memory.date : formatDate(memory.date);
      nodes.push(heading);
    }
    nodes.push(memoryCard(memory));
  }
  el.list.replaceChildren(...nodes);
  el.empty.hidden = rows.length > 0;
  el.empty.textContent = "Không tìm thấy trang nhật ký phù hợp.";
  el.viewButtons.forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

async function loadStats({ quiet = false } = {}) {
  if (!quiet) setStatus("Đang đọc bộ nhớ…");
  el.list.setAttribute("aria-busy", "true");
  try {
    const stats = await getJson("/api/memory/stats");
    state.stats = {
      ...stats,
      leaves: Array.isArray(stats.leaves) ? stats.leaves : [],
    };
    render();
    setStatus(`${state.stats.total ?? state.stats.leaves.length} leaf · dữ liệu backend`, "success");
  } catch (error) {
    el.list.replaceChildren();
    el.empty.hidden = false;
    el.empty.textContent = messageOf(error, "Không tải được bộ nhớ.");
    setStatus(messageOf(error, "Không tải được bộ nhớ."), "error");
  } finally {
    el.list.setAttribute("aria-busy", "false");
  }
}

function openMemory(memory, opener) {
  state.activeMemory = memory;
  state.opener = opener;
  el.id.value = memory.sessionId;
  el.title.value = memory.title;
  el.description.value = memory.description;
  setDialogStatus(el.dialogStatus);
  el.dialog.showModal();
  el.title.focus();
}

async function mutateMemory(path, body, success) {
  setBusy(true);
  setDialogStatus(el.dialogStatus, "Đang lưu…");
  try {
    await postJson(path, body);
    el.dialog.close();
    await loadStats({ quiet: true });
    setStatus(success, "success");
  } catch (error) {
    setDialogStatus(el.dialogStatus, messageOf(error, "Không lưu được."), "error");
  } finally {
    setBusy(false);
  }
}

function bind() {
  el.search.addEventListener("input", render);
  el.viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view || "day";
      render();
    });
  });
  el.cancel.addEventListener("click", () => el.dialog.close());
  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const topic = el.title.value.trim();
    const summary = el.description.value.trim();
    if (!topic || !summary || !el.id.value) return;
    void mutateMemory("/api/memory/update", { session_id: el.id.value, topic, summary }, "Đã cập nhật trang.");
  });
  el.reinforce.addEventListener("click", () => {
    if (!el.id.value) return;
    void mutateMemory("/api/memory/reinforce", { session_id: el.id.value }, "Đã gia hạn trang.");
  });
  el.forget.addEventListener("click", () => {
    if (!el.id.value || !confirm(`Quên “${state.activeMemory?.title || "trang này"}” khỏi bộ nhớ?`)) return;
    void mutateMemory("/api/memory/forget", { session_id: el.id.value }, "Đã quên trang.");
  });
  el.dialog.addEventListener("close", () => state.opener?.focus());
}

bind();
void loadStats();
