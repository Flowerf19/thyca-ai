import { getJson } from "./backend/api.js";
import { rollingRange, traceRangeUrl } from "./backend/analytics-data.js";
import { formatCompact, formatCost, formatDate, formatInteger } from "./backend/format.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const el = {
  period: document.querySelector("#cost-period"),
  total: document.querySelector("#cost-total"),
  range: document.querySelector("#cost-range"),
  chart: document.querySelector("#cost-chart"),
  models: document.querySelector("#cost-models"),
  status: document.querySelector("#cost-status"),
};
const compact = matchMedia("(max-width: 56rem)");
let stats = null;

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function svg(name, attributes = {}, text = "") {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text) node.textContent = text;
  return node;
}

function dateLabel(day) {
  const [year, month, date] = String(day).split("-");
  return year && month && date ? `${date}/${month}` : String(day);
}

function completeCosts(range, rows) {
  const indexed = new Map((rows || []).map((row) => [row.day, row.cost_usd]));
  const start = new Date(`${range.from}T00:00:00Z`);
  return Array.from({ length: range.days }, (_, index) => {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    const day = date.toISOString().slice(0, 10);
    const raw = indexed.get(day);
    return { day, value: raw == null ? null : Number(raw) || 0 };
  });
}

function drawChart(rows) {
  const width = 720;
  const height = 230;
  const left = 58;
  const right = 34;
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
      svg("text", { class: "cost-chart-label", x: 2, y: yy + 4 }, formatCost(value, 6)),
    );
  }

  const path = rows.map((row, index) => `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(row.value || 0).toFixed(1)}`).join(" ");
  const area = `M${x(0)} ${height - bottom} ${rows.map((row, index) => `L${x(index)} ${y(row.value || 0)}`).join(" ")} L${x(rows.length - 1)} ${height - bottom} Z`;
  el.chart.append(svg("path", { class: "cost-chart-area", d: area }), svg("path", { class: "cost-chart-line", d: path }));
  rows.forEach((row, index) => {
    if (row.value == null) return;
    el.chart.append(svg("circle", { class: "cost-chart-point", cx: x(index), cy: y(row.value), r: 3 }));
  });
  const labels = compact.matches
    ? [0, Math.floor((rows.length - 1) / 2), rows.length - 1]
    : rows.map((_, index) => index).filter((index) => index === 0 || index === rows.length - 1 || index % 5 === 0);
  [...new Set(labels)].forEach((index) => {
    el.chart.append(svg("text", { class: "cost-chart-label", "text-anchor": "middle", x: x(index), y: height - 8 }, dateLabel(rows[index].day)));
  });
  el.chart.setAttribute("aria-label", `Chi phí ${rows.length} ngày; cao nhất ${formatCost(maxValue)}.`);
}

function modelRow(model, total) {
  const details = document.createElement("details");
  details.className = "fold-row";
  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "fold-row-icon";
  icon.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7.2"/><path d="M9 9.5h6v5H9Z"/><path d="M12 8v8"/></svg>';
  const copy = document.createElement("div");
  const name = document.createElement("span");
  name.className = "fold-row-name";
  name.textContent = model.model || "unknown";
  const meta = document.createElement("span");
  meta.className = "fold-row-meta";
  meta.textContent = `${formatInteger(model.requests)} request · ${formatCompact(model.total_tokens)} token`;
  copy.append(name, meta);
  const cost = document.createElement("span");
  cost.className = "fold-row-stat";
  cost.textContent = formatCost(model.cost_usd);
  const percent = document.createElement("span");
  percent.className = "screen-badge fold-row-badge";
  percent.textContent = model.cost_usd == null || !total ? "—" : `${Math.round(Number(model.cost_usd) / total * 100)}%`;
  summary.append(icon, copy, cost, percent);
  const breakdown = document.createElement("div");
  breakdown.className = "fold-row-body";
  for (const [label, value] of [
    ["Input", model.prompt_tokens],
    ["Cache", model.cached_tokens],
    ["Output", model.completion_tokens],
  ]) {
    const row = document.createElement("div");
    const key = document.createElement("span");
    const amount = document.createElement("span");
    key.textContent = label;
    amount.textContent = `${formatInteger(value)} token`;
    row.append(key, amount);
    breakdown.append(row);
  }
  details.append(summary, breakdown);
  return details;
}

function render() {
  if (!stats) return;
  const days = Number(el.period.value) || 30;
  const range = rollingRange(days);
  const total = stats.totals?.cost_usd;
  const value = document.createTextNode(total == null ? "—" : formatCost(total, 2));
  const unit = document.createElement("small");
  unit.textContent = " USD";
  el.total.replaceChildren(value, unit);
  el.range.textContent = `Từ ${formatDate(range.from)} – ${formatDate(range.to)}`;
  drawChart(completeCosts(range, stats.by_day));
  const rows = Array.isArray(stats.by_model) ? stats.by_model : [];
  el.models.replaceChildren(...rows.map((model) => modelRow(model, Number(total) || 0)));
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "screen-note";
    empty.textContent = "Chưa có lượt nào trong khoảng này.";
    el.models.append(empty);
  }
}

async function load() {
  const days = Number(el.period.value) || 30;
  setStatus("Đang tổng hợp chi phí từ trace…");
  try {
    stats = await getJson(traceRangeUrl("/api/traces/stats", days));
    render();
    setStatus(`${formatInteger(stats.totals?.requests)} request trong ${days} ngày.`, "success");
  } catch (error) {
    stats = null;
    el.models.replaceChildren();
    el.chart.replaceChildren();
    setStatus(messageOf(error, "Không tải được chi phí."), "error");
  }
}

el.period?.addEventListener("change", () => void load());
compact.addEventListener("change", render);
void load();
