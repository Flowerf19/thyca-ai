import { getJson } from "../../shared/js/api.js";
import { aggregateUsage, completeDays, rollingRange, traceRangeUrl } from "../../shared/js/analytics-data.js";
import { drawBarChart } from "../../shared/js/bar-chart.js";
import { formatCompact, formatDate, formatInteger } from "../../shared/js/format.js";

const el = {
  period: document.querySelector("#usage-month"),
  chart: document.querySelector("#usage-chart"),
  summaries: document.querySelector("#usage-summaries"),
  total: document.querySelector("#usage-total"),
  turns: document.querySelector("#usage-turns"),
  range: document.querySelector("#usage-period"),
  status: document.querySelector("#usage-status"),
  legend: [...document.querySelectorAll(".usage-legend span")],
  units: [...document.querySelectorAll(".usage-toggle button")],
};
const compact = matchMedia("(max-width: 56rem)");
const leafIcon = '<svg class="usage-leaf" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 18c4.2-7.5 7.5-10.8 12-12-1.2 4.8-4.7 8.1-12 12Z"/><path d="m7 17 6-6"/></svg>';
let unit = "token";
let payload = null;
let usage = null;
let series = [];

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function draw() {
  if (!series.length) {
    el.chart.replaceChildren();
    return;
  }
  const values = series.map((day) => unit === "token" ? day.input + day.cache + day.output : day.turns);
  const rows = series.map((day) => ({
    day: day.day,
    segments: unit === "token"
      ? [
          { class: "usage-bar-input", value: day.input },
          { class: "usage-bar-cache", value: day.cache },
          { class: "usage-bar-output", value: day.output },
        ]
      : [{ class: "usage-bar-input", value: day.turns }],
  }));
  drawBarChart(el.chart, rows, {
    ariaLabel: `Biểu đồ ${unit === "token" ? "token" : "lượt"} theo ngày; mức cao nhất ${formatCompact(Math.max(...values, 0))}.`,
  });
}

function summaryCard(title, amount, total, turns) {
  const article = document.createElement("article");
  article.className = "screen-card usage-summary";
  const header = document.createElement("header");
  const heading = document.createElement("h3");
  heading.textContent = title;
  const icon = document.createElement("span");
  icon.innerHTML = leafIcon;
  header.append(heading, icon.firstElementChild);
  const value = document.createElement("strong");
  value.append(document.createTextNode(`${formatCompact(amount)} `));
  const unitText = document.createElement("small");
  unitText.textContent = "token";
  value.append(unitText);
  const footer = document.createElement("footer");
  const share = document.createElement("span");
  share.textContent = total > 0 ? `${(amount / total * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}% tổng sử dụng` : "0% tổng sử dụng";
  const turnCount = document.createElement("span");
  turnCount.textContent = `${formatInteger(turns)} lượt`;
  footer.append(share, turnCount);
  article.append(header, value, footer);
  return article;
}

function setStrong(root, value, unitText) {
  const small = document.createElement("small");
  small.textContent = unitText;
  root.replaceChildren(document.createTextNode(`${value} `), small);
}

function render() {
  if (!usage) return;
  const totals = usage.totals;
  setStrong(el.total, formatCompact(totals.total), "token");
  setStrong(el.turns, formatInteger(totals.turns), "lượt");
  const range = rollingRange(Number(el.period.value) || 30);
  el.range.textContent = `${formatDate(range.from)} – ${formatDate(range.to)}`;
  el.summaries.replaceChildren(
    summaryCard("Tổng Input", totals.input, totals.total, totals.turns),
    summaryCard("Tổng Cache read", totals.cache, totals.total, totals.turns),
    summaryCard("Tổng Output", totals.output, totals.total, totals.turns),
  );
  el.legend.forEach((item, index) => {
    item.hidden = unit === "turn" && index > 0;
    if (index === 0) item.textContent = unit === "turn" ? "Lượt" : "Input";
  });
  draw();
}

async function loadAllTraces(days) {
  const traces = [];
  let offset = 0;
  let total = Infinity;
  while (offset < total) {
    const page = await getJson(`${traceRangeUrl("/api/traces", days, 200)}&offset=${offset}`);
    const rows = Array.isArray(page.traces) ? page.traces : [];
    total = Number(page.total) || 0;
    traces.push(...rows);
    if (!rows.length) break;
    offset += rows.length;
  }
  return { traces, total };
}

async function load() {
  const days = Number(el.period.value) || 30;
  setStatus("Đang tổng hợp token từ trace…");
  try {
    payload = await loadAllTraces(days);
    usage = aggregateUsage(payload.traces);
    series = completeDays(usage.days, rollingRange(days));
    render();
    setStatus();
  } catch (error) {
    payload = null;
    usage = null;
    series = [];
    el.chart.replaceChildren();
    el.summaries.replaceChildren();
    setStatus(messageOf(error, "Không tải được số liệu token."), "error");
  }
}

el.units.forEach((button) => {
  button.addEventListener("click", () => {
    unit = button.dataset.unit || "token";
    el.units.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    render();
  });
});
el.period?.addEventListener("change", () => void load());
compact.addEventListener("change", draw);
new ResizeObserver(() => {
  if (series.length) draw();
}).observe(el.chart);
void load();
