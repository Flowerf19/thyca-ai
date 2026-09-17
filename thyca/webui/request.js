import { getJson } from "./backend/api.js";
import { rollingRange, traceRangeUrl } from "./backend/analytics-data.js";
import { cleanText, formatDate, formatInteger } from "./backend/format.js";

const el = {
  period: document.querySelector("#request-period"),
  total: document.querySelector("#request-total"),
  range: document.querySelector("#request-range"),
  chart: document.querySelector("#request-chart"),
  models: document.querySelector("#request-models"),
  status: document.querySelector("#request-status"),
  search: document.querySelector("#request-model-search"),
  sorts: [...document.querySelectorAll("[data-req-sort]")],
};
const compact = matchMedia("(max-width: 56rem)");
let stats = null;
let sort = "req-desc";

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  if (!el.status) return;
  el.status.textContent = message;
  el.status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function svg(name, attributes = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text) node.textContent = text;
  return node;
}

function dateLabel(day) {
  const [year, month, date] = String(day).split("-");
  return year && month && date ? `${date}/${month}` : String(day);
}

function completeRequests(range, rows) {
  const indexed = new Map((rows || []).map((row) => [row.day, row.requests]));
  const start = new Date(`${range.from}T00:00:00Z`);
  return Array.from({ length: range.days }, (_, index) => {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    const day = date.toISOString().slice(0, 10);
    const raw = indexed.get(day);
    return { day, value: raw == null ? 0 : Number(raw) || 0 };
  });
}

function selectRequestModels(models, { sort: order, query = "" } = {}) {
  const needle = cleanText(query).toLocaleLowerCase("vi");
  const name = (row) => cleanText(row?.model);
  const rows = (Array.isArray(models) ? models : [])
    .filter((row) => (Number(row?.requests) || 0) > 0)
    .filter((row) => !needle || name(row).toLocaleLowerCase("vi").includes(needle));
  if (order === "req-asc") {
    return rows.sort((a, b) => (Number(a.requests) || 0) - (Number(b.requests) || 0) || name(a).localeCompare(name(b), "vi"));
  }
  if (order === "recent") {
    return rows.sort((a, b) => cleanText(b?.last_started_at).localeCompare(cleanText(a?.last_started_at))
      || name(a).localeCompare(name(b), "vi"));
  }
  return rows.sort((a, b) => (Number(b.requests) || 0) - (Number(a.requests) || 0) || name(a).localeCompare(name(b), "vi"));
}

function drawChart(rows) {
  if (!el.chart) return;
  const box = el.chart.getBoundingClientRect();
  const width = Math.max(Math.round(box.width) || 720, 320);
  const height = Math.max(Math.round(box.height) || 168, 160);
  el.chart.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const left = 58;
  const right = 10;
  const top = 14;
  const bottom = 32;
  const values = rows.map((row) => row.value || 0);
  const maxValue = Math.max(...values, 0);
  const ceiling = maxValue > 0 ? maxValue * 1.12 : 1;
  const span = Math.max(rows.length - 1, 1);
  const x = (index) => left + index * (width - left - right) / span;
  const y = (value) => height - bottom - value / ceiling * (height - top - bottom);
  el.chart.replaceChildren();

  for (let index = 0; index <= 4; index += 1) {
    const value = ceiling * index / 4;
    const yy = y(value);
    el.chart.append(
      svg("line", { class: "cost-chart-grid", x1: left, x2: width - right, y1: yy, y2: yy }),
      svg("text", { class: "cost-chart-label", x: 2, y: yy + 4 }, formatInteger(value)),
    );
  }

  const path = rows.map((row, index) => `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(row.value || 0).toFixed(1)}`).join(" ");
  const area = `M${x(0)} ${height - bottom} ${rows.map((row, index) => `L${x(index)} ${y(row.value || 0)}`).join(" ")} L${x(rows.length - 1)} ${height - bottom} Z`;
  el.chart.append(svg("path", { class: "cost-chart-area", d: area }), svg("path", { class: "cost-chart-line", d: path }));
  rows.forEach((row, index) => {
    el.chart.append(svg("circle", { class: "cost-chart-point", cx: x(index), cy: y(row.value), r: 3 }));
  });
  const labels = compact.matches
    ? [0, Math.floor((rows.length - 1) / 2), rows.length - 1]
    : rows.map((_, index) => index).filter((index) => index === 0 || index === rows.length - 1 || index % 5 === 0);
  [...new Set(labels)].forEach((index) => {
    el.chart.append(svg("text", { class: "cost-chart-label", "text-anchor": "middle", x: x(index), y: height - 8 }, dateLabel(rows[index].day)));
  });
  el.chart.setAttribute("aria-label", `Request ${rows.length} ngày; cao nhất ${formatInteger(maxValue)}.`);
}

function shareOf(value, total) {
  return !total ? "—" : `${Math.round(Number(value) / total * 100)}%`;
}

function modelRow(model, total, index) {
  const row = document.createElement("article");
  row.className = "cost-model-row";
  const head = document.createElement("div");
  head.className = "cost-model-row-head";
  const name = document.createElement("h3");
  name.textContent = model.model || "unknown";
  const count = document.createElement("strong");
  count.className = "cost-model-cost";
  count.textContent = formatInteger(model.requests);
  head.append(name, count);
  const bar = document.createElement("div");
  bar.className = "cost-model-bar";
  const track = document.createElement("span");
  track.className = "cost-model-track";
  const fill = document.createElement("span");
  fill.className = `cost-model-fill is-${index % 3}`;
  const shareText = shareOf(model.requests, total);
  fill.style.width = shareText === "—" ? "0%" : shareText;
  const share = document.createElement("span");
  share.className = "cost-model-share";
  share.textContent = shareText;
  track.append(fill);
  bar.append(track, share);
  row.append(head, bar);
  return row;
}

function render() {
  if (!stats || !el.total) return;
  const days = Number(el.period.value) || 30;
  const range = rollingRange(days);
  const total = Number(stats.totals?.requests) || 0;
  el.total.textContent = formatInteger(total);
  el.range.textContent = `Từ ${formatDate(range.from)} – ${formatDate(range.to)}`;
  drawChart(completeRequests(range, stats.by_day));
  const rows = selectRequestModels(stats.by_model, { sort, query: el.search?.value || "" });
  el.models.replaceChildren(...rows.map((model, index) => modelRow(model, total, index)));
  el.sorts.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.reqSort === sort));
  });
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "screen-note";
    empty.textContent = (stats.by_model || []).length
      ? "Không có mô hình nào khớp tên đang tìm."
      : "Chưa có request nào trong khoảng này.";
    el.models.append(empty);
  }
}

async function load() {
  const days = Number(el.period.value) || 30;
  setStatus("Đang tổng hợp request từ trace…");
  try {
    stats = await getJson(traceRangeUrl("/api/traces/stats", days));
    render();
    setStatus();
  } catch (error) {
    stats = null;
    el.models?.replaceChildren();
    el.chart?.replaceChildren();
    setStatus(messageOf(error, "Không tải được request."), "error");
  }
}

el.period?.addEventListener("change", () => void load());
el.search?.addEventListener("input", render);
el.sorts.forEach((button) => {
  button.addEventListener("click", () => {
    sort = button.dataset.reqSort || "req-desc";
    render();
  });
});
compact.addEventListener("change", render);
if (el.chart) {
  new ResizeObserver(() => { if (stats) render(); }).observe(el.chart);
}
if (el.total) void load();
