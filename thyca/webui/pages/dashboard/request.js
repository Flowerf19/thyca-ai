import { getJson } from "../../shared/js/api.js";
import { completeDays, rollingRange, traceRangeUrl } from "../../shared/js/analytics-data.js";
import { svg } from "../../shared/js/bar-chart.js";
import { makeSetStatus, messageOf } from "../../shared/js/status.js";
import { cleanText, formatDate, formatInteger } from "../../shared/js/format.js";

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
let loadId = 0;

const setStatus = makeSetStatus(el.status);

function dateLabel(day) {
  const [year, month, date] = String(day).split("-");
  return year && month && date ? `${date}/${month}` : String(day);
}

function completeRequests(range, rows) {
  return completeDays(rows || [], range).map((row) => ({ day: row.day, value: Number(row.requests) || 0 }));
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

/* Request theo mô hình is ONE horizontal bar chart: every row is measured on
   the same scale (width = value / largest value across the full data, not
   the filtered subset), so bars stay comparable after search or sort. Names
   and counts are real text — long model names wrap instead of hiding behind
   a tooltip — and the bar is decorative. Share uses the full-data total. */
function modelChart(rows, total, max) {
  const list = document.createElement("ul");
  list.className = "request-model-chart";
  rows.forEach((row) => {
    const item = document.createElement("li");
    item.className = "request-model-row";
    const head = document.createElement("div");
    head.className = "request-model-head";
    const name = document.createElement("span");
    name.className = "request-model-name";
    name.textContent = cleanText(row?.model) || "unknown";
    const count = document.createElement("span");
    count.className = "request-model-count";
    count.textContent = formatInteger(row.requests);
    const share = document.createElement("span");
    share.className = "request-model-share";
    share.textContent = shareOf(row.requests, total);
    const bar = document.createElement("span");
    bar.className = "request-model-bar";
    bar.setAttribute("aria-hidden", "true");
    const fill = document.createElement("span");
    fill.className = "request-model-fill";
    fill.style.width = max > 0 ? `${(Number(row.requests) || 0) / max * 100}%` : "0%";
    bar.append(fill);
    head.append(name, count, share);
    item.append(head, bar);
    list.append(item);
  });
  return list;
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
  // Shared scale covers every model in the stats payload, so a search that
  // narrows the rows never changes what a bar of a given length means.
  const max = Math.max(...(stats.by_model || []).map((row) => Number(row.requests) || 0), 0);
  el.models.replaceChildren(modelChart(rows, total, max));
  el.sorts.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.reqSort === sort));
  });
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "screen-note";
    // Name the real reason: a search that matched nothing vs a period with
    // no request at all (zero-request models are never drawn as bars).
    empty.textContent = (el.search?.value || "").trim() && (stats.by_model || []).length
      ? "Không có mô hình nào khớp tên đang tìm."
      : "Chưa có request nào trong khoảng này.";
    el.models.append(empty);
  }
}

async function load() {
  // Generation guard: a slow response for an older period must never
  // overwrite the newer selection's stats (period change race).
  const generation = ++loadId;
  const days = Number(el.period.value) || 30;
  setStatus("Đang tổng hợp request từ trace…");
  try {
    const fresh = await getJson(traceRangeUrl("/api/traces/stats", days));
    if (generation !== loadId) return;
    stats = fresh;
    render();
    setStatus();
  } catch (error) {
    if (generation !== loadId) return;
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
