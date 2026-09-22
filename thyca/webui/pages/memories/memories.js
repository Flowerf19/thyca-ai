import { getJson, postJson } from "../../shared/js/api.js";
import { completeDays, rollingRange } from "../../shared/js/analytics-data.js";
import { drawBarChart } from "../../shared/js/bar-chart.js";
import { formatDate, formatDateTime, formatInteger } from "../../shared/js/format.js";
import { selectMemories } from "../../shared/js/memory-data.js";
const el = {
  list: document.querySelector("#memory-list"),
  empty: document.querySelector("#memory-empty"),
  viewSections: [...document.querySelectorAll(".memory-view")],
  search: document.querySelector("#memory-search"),
  status: document.querySelector("#memory-status"),
  viewButtons: [...document.querySelectorAll(".session-item[data-view]")],
};
const compact = matchMedia("(max-width: 56rem)");

// 1. Responsive search placement: the search cluster lives in the sidebar on
// desktop and moves into the bar above the view folds on mobile.
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
  // Inline edit state: the edited card re-renders from here, so a search
  // keystroke or a resize rebuild keeps the draft instead of losing it.
  editing: null, // { id, sessionId, topic, summary }
  // True while a save/forget/reinforce request is in flight: one mutation at
  // a time, editors stay disabled, start/cancel are refused.
  pending: false,
};

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-note memory-status screen-status${kind ? ` is-${kind}` : ""}`;
}

function setBusy(busy) {
  el.list.setAttribute("aria-busy", String(busy));
  // Every rendered editor responds, not just the last one created: mobile
  // renders one editor copy per matching view section.
  for (const form of document.querySelectorAll(".memory-edit")) {
    for (const control of form.querySelectorAll("button, input, textarea")) control.disabled = busy;
  }
}

// 2. Card render: one memory leaf becomes an article row; the row being
// edited renders the inline editor instead of its copy block.
function memoryCard(memory) {
  const article = document.createElement("article");
  article.className = "memory-card";
  article.dataset.memoryId = memory.id;
  const time = document.createElement("time");
  time.textContent = memory.date === "Không rõ ngày" ? memory.date : formatDate(memory.date);
  article.append(time);
  if (state.editing?.id === memory.id) {
    // Edit-in-place: the card turns into the editor on the page itself —
    // no dialog. The date gutter and grid stay untouched.
    article.classList.add("is-editing");
    article.append(editForm(memory));
    return article;
  }
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
  more.addEventListener("click", (event) => startEdit(memory, event.currentTarget));
  meta.append(more);
  article.append(copy, meta);
  return article;
}

// 3. Inline editor: the card turns into the edit form in place. The draft
// lives in state.editing so re-renders never lose it; focus follows the
// visible copy on mobile (one editor copy per view section).
// The editor lives on the card itself: same entry grid, boxless fields that
// only gain a dashed underline on hover/focus.
function editForm(memory) {
  const form = document.createElement("form");
  form.className = "memory-edit";
  const line = document.createElement("div");
  line.className = "memory-edit-line";
  const title = document.createElement("input");
  title.className = "memory-edit-title";
  title.required = true;
  title.maxLength = 120;
  title.autocomplete = "off";
  title.placeholder = "Tên trang nhật ký";
  title.setAttribute("aria-label", "Tiêu đề trang");
  title.value = state.editing.topic;
  title.addEventListener("input", () => { state.editing.topic = title.value; });
  line.append(title);
  const text = document.createElement("textarea");
  text.className = "memory-edit-text";
  text.required = true;
  text.maxLength = 400;
  text.placeholder = "Nội dung ghi lại từ phiên trò chuyện";
  text.setAttribute("aria-label", "Mô tả trang");
  text.value = state.editing.summary;
  text.addEventListener("input", () => { state.editing.summary = text.value; });
  const actions = document.createElement("div");
  actions.className = "memory-edit-actions";
  const save = document.createElement("button");
  save.className = "screen-button is-primary";
  save.type = "submit";
  save.textContent = "Lưu trang";
  const cancel = document.createElement("button");
  cancel.className = "screen-button";
  cancel.type = "button";
  cancel.textContent = "Hủy";
  cancel.addEventListener("click", () => cancelEdit(cancel));
  const reinforce = document.createElement("button");
  reinforce.className = "screen-button";
  reinforce.type = "button";
  reinforce.textContent = "Gia hạn";
  reinforce.addEventListener("click", () => {
    void mutateMemory("/api/memory/reinforce", { session_id: state.editing.sessionId }, "Đã gia hạn trang.");
  });
  const forget = document.createElement("button");
  forget.className = "screen-button is-danger";
  forget.type = "button";
  forget.textContent = "Quên";
  forget.addEventListener("click", () => {
    if (!confirm(`Quên “${state.editing.topic || "trang này"}” khỏi bộ nhớ?`)) return;
    void mutateMemory("/api/memory/forget", { session_id: state.editing.sessionId }, "Đã quên trang.");
  });
  actions.append(cancel, reinforce, save, forget);
  form.append(line, text, actions);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const topic = state.editing.topic.trim();
    const summary = state.editing.summary.trim();
    if (!topic || !summary) return;
    void mutateMemory("/api/memory/update", { session_id: state.editing.sessionId, topic, summary }, "Đã cập nhật trang.");
  });
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing) {
      event.stopPropagation();
      cancelEdit(form);
    }
  });
  return form;
}

// Mobile renders one editor copy per matching view section. Focus follows the
// copy the user actually opened, and cancel puts focus back on that row's
// action button. Both capture their section before render detaches the row.
function firstVisible(nodes) {
  return nodes.find((node) => node.offsetParent !== null) ?? null;
}

function startEdit(memory, origin) {
  if (state.pending) return; // a mutation is in flight; the draft must not move
  const section = origin?.closest(".memory-view") ?? null;
  state.editing = { id: memory.id, sessionId: memory.sessionId, topic: memory.title, summary: memory.description };
  render();
  const titles = [...(section || document).querySelectorAll(".memory-edit-title")];
  (firstVisible(titles) ?? titles[0])?.focus();
}

function cancelEdit(origin) {
  if (state.pending) return;
  const section = origin?.closest(".memory-view") ?? null;
  const id = state.editing?.id;
  state.editing = null;
  render();
  if (!id) return;
  const actions = [...(section || document).querySelectorAll(".row-action")]
    .filter((node) => node.closest(".memory-card")?.dataset.memoryId === String(id));
  (firstVisible(actions) ?? actions[0])?.focus();
}

// 4. Overview: 30-day bar chart plus the three stat cards.
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

// 5. Render dispatch + data: per-view rows, desktop/mobile render, stats
// load, single-flight mutations, and event binding.
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
  // A re-render while a mutation is in flight (search keystroke, resize,
  // view switch) must rebuild the editor copies in the busy state, not live.
  if (state.pending) setBusy(true);
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

async function mutateMemory(path, body, success) {
  if (state.pending) return; // one mutation at a time; no duplicate requests
  const draft = state.editing; // identity: only the submitted draft may be cleared
  state.pending = true;
  setBusy(true);
  setStatus("Đang lưu…");
  try {
    await postJson(path, body);
    // A draft started after this request began keeps its text.
    if (state.editing === draft) state.editing = null;
    await loadStats({ quiet: true });
    setStatus(success, "success");
  } catch (error) {
    // Failure keeps the draft on screen, re-enabled.
    setStatus(messageOf(error, "Không lưu được."), "error");
  } finally {
    state.pending = false;
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
}

bind();
void loadStats();
