import { getJson, postJson } from "./backend/api.js";
import { completeDays, rollingRange } from "./backend/analytics-data.js";
import { drawBarChart } from "./backend/bar-chart.js";
import { formatDate, formatDateTime, formatInteger } from "./backend/format.js";
import { selectMemories } from "./backend/memory-data.js";
const el = {
  list: document.querySelector("#memory-list"),
  empty: document.querySelector("#memory-empty"),
  viewSections: [...document.querySelectorAll(".memory-view")],
  search: document.querySelector("#memory-search"),
  status: document.querySelector("#memory-status"),
  viewButtons: [...document.querySelectorAll(".session-item[data-view]")],
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
const compact = matchMedia("(max-width: 56rem)");

function placeSearch() {
  const cluster = el.search?.closest(".memory-search-cluster");
  const bar = document.querySelector(".memory-search-bar");
  if (!cluster) return;
  if (compact.matches) {
    if (bar && cluster.parentNode !== bar) bar.append(cluster);
  } else {
    const sessions = document.querySelector(".sessions");
    const list = sessions?.querySelector(".session-list");
    if (sessions && list && cluster.nextElementSibling !== list) sessions.insertBefore(cluster, list);
  }
}

const state = {
  stats: { leaves: [] },
  view: "overview",
  activeMemory: null,
  opener: null,
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
  for (const control of el.dialog.querySelectorAll("button, input, textarea")) control.disabled = busy;
  el.list.setAttribute("aria-busy", String(busy));
}

function memoryCard(memory) {
  const article = document.createElement("article");
  article.className = "memory-card";
  article.dataset.memoryId = memory.id;
  const time = document.createElement("time");
  time.textContent = memory.date === "Không rõ ngày" ? memory.date : formatDate(memory.date);
  const copy = document.createElement("div");
  copy.className = "memory-copy";
  const heading = document.createElement("h3");
  heading.textContent = memory.title;
  const description = document.createElement("p");
  description.textContent = memory.description;
  copy.append(heading, description);
  const meta = document.createElement("div");
  meta.className = "memory-meta";
  for (const label of [`${memory.uses} lần dùng`, `${memory.searches} lần tìm`]) {
    const tag = document.createElement("span");
    tag.textContent = label;
    meta.append(tag);
  }
  if (memory.expiresAt) {
    const tag = document.createElement("span");
    tag.textContent = `hết hạn ${formatDateTime(memory.expiresAt)}`;
    meta.append(tag);
  }
  const more = document.createElement("button");
  more.className = "memory-more row-action";
  more.type = "button";
  more.disabled = !memory.sessionId;
  more.setAttribute("aria-label", `Sửa trang ${memory.title}`);
  more.textContent = "⋮";
  more.addEventListener("click", () => openMemory(memory, more));
  meta.append(more);
  article.append(time, copy, meta);
  return article;
}

const SVG_NS = "http://www.w3.org/2000/svg";
let overviewSvg = null;
const chartObserver = new ResizeObserver(() => drawOverviewChart());

function drawOverviewChart() {
  if (!overviewSvg) return;
  const counts = new Map();
  for (const leaf of state.stats.leaves) {
    const day = String(leaf?.timeline_day || "");
    if (/^\d{4}-\d{2}-\d{2}$/.test(day)) counts.set(day, (counts.get(day) || 0) + 1);
  }
  // completeDays zero-fills the 30-day window; days without data come back
  // without `value`, so coerce to 0.
  const rows = completeDays([...counts].map(([day, value]) => ({ day, value })), rollingRange(30))
    .map(({ day, value }) => ({ day, value: value || 0 }));
  drawBarChart(overviewSvg, rows.map((row) => ({
    day: row.day,
    segments: [{ class: "memory-bar", value: row.value }],
  })), {
    valueLabels: true,
    ariaLabel: `Biểu đồ mẩu nhật ký theo ngày; mức cao nhất ${formatInteger(Math.max(...rows.map((row) => row.value), 0))}.`,
  });
}

function overviewChartCard() {
  const article = document.createElement("article");
  article.className = "screen-card memory-chart-card";
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("role", "img");
  article.append(svg);
  overviewSvg = svg;
  chartObserver.disconnect();
  chartObserver.observe(svg);
  drawOverviewChart();
  return article;
}

function overviewCards() {
  const leaves = state.stats.leaves;
  const pages = state.stats.total ?? leaves.length;
  const uses = leaves.reduce((sum, leaf) => sum + Math.max(0, Number(leaf.get_count) || 0), 0);
  const searches = leaves.reduce((sum, leaf) => sum + Math.max(0, Number(leaf.search_count) || 0), 0);
  return [
    ["Mẩu nhật ký", pages],
    ["Lần dùng", uses],
    ["Lần tìm", searches],
  ].map(([title, count]) => {
    const article = document.createElement("article");
    article.className = "screen-card";
    const heading = document.createElement("h3");
    heading.textContent = title;
    const value = document.createElement("strong");
    value.textContent = formatInteger(count);
    article.append(heading, value);
    return article;
  });
}

function overviewStats() {
  const wrap = document.createElement("div");
  wrap.className = "memory-overview";
  wrap.append(...overviewCards());
  return wrap;
}

function renderRows(view) {
  const rows = selectMemories(state.stats.leaves, { view, query: el.search.value });
  // Dates live in each entry's left gutter, so the day view needs no separate
  // day headings between groups.
  const nodes = rows.map((memory) => memoryCard(memory));
  return { rows, nodes };
}

function render() {
  if (compact.matches) {
    // Mobile: every view is its own collapsible section on one scrolling page.
    el.list.hidden = true;
    el.empty.hidden = true;
    for (const section of el.viewSections) {
      section.hidden = false;
      const body = section.querySelector(".memory-view-body");
      if (section.dataset.view === "overview") {
        body.replaceChildren(overviewChartCard(), overviewStats());
        continue;
      }
      const { nodes } = renderRows(section.dataset.view);
      body.replaceChildren(...nodes);
      if (!nodes.length) {
        const note = document.createElement("p");
        note.className = "screen-note";
        note.textContent = "Không tìm thấy mẩu nhật ký phù hợp.";
        body.append(note);
      }
    }
  } else {
    for (const section of el.viewSections) section.hidden = true;
    if (state.view === "overview") {
      el.list.replaceChildren(overviewChartCard(), overviewStats());
      el.list.hidden = false;
      el.empty.hidden = true;
    } else {
      const { rows, nodes } = renderRows(state.view);
      el.list.replaceChildren(...nodes);
      el.list.hidden = false;
      el.empty.hidden = rows.length > 0;
      el.empty.textContent = "Không tìm thấy mẩu nhật ký phù hợp.";
    }
  }
  el.viewButtons.forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}
compact.addEventListener("change", () => {
  placeSearch();
  render();
});

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
    setStatus();
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
  placeSearch();
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
