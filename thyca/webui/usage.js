import { getJson } from "./backend/api.js";
import { aggregateUsage, completeDays, rollingRange, traceRangeUrl } from "./backend/analytics-data.js";
import { formatCompact, formatDate, formatInteger } from "./backend/format.js";

const SVG_NS = "http://www.w3.org/2000/svg";
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

function svg(name, attributes = {}, text = "") {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text) node.textContent = text;
  return node;
}

function niceCeiling(max) {
  if (!Number.isFinite(max) || max <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(max));
  const scaled = max / power;
  const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return nice * power;
}

function dayLabel(day) {
  return String(day || "").slice(8, 10).replace(/^0/, "") || "—";
}

function draw() {
  if (!series.length) {
    el.chart.replaceChildren();
    return;
  }
  const width = 900;
  const height = 300;
  const left = 58;
  const right = 10;
  const top = 12;
  const bottom = 34;
  const values = series.map((day) => unit === "token" ? day.input + day.cache + day.output : day.turns);
  const ceiling = niceCeiling(Math.max(...values, 0));
  const x = (index) => left + index * (width - left - right) / series.length;
  const y = (value) => height - bottom - value / ceiling * (height - bottom - top);
  const barWidth = Math.max(4, (width - left - right) / series.length * 0.58);
  el.chart.replaceChildren();

  for (let index = 0; index <= 5; index += 1) {
    const value = ceiling * index / 5;
    const yy = y(value);
    el.chart.append(
      svg("line", { class: "usage-grid", x1: left, x2: width - right, y1: yy, y2: yy }),
      svg("text", { class: "usage-axis", x: 3, y: yy + 4 }, formatCompact(value)),
    );
  }

  series.forEach((day, index) => {
    const base = height - bottom;
    const input = unit === "token" ? day.input : day.turns;
    const cache = unit === "token" ? day.cache : 0;
    const output = unit === "token" ? day.output : 0;
    const inputH = input / ceiling * (height - bottom - top);
    const cacheH = cache / ceiling * (height - bottom - top);
    const outputH = output / ceiling * (height - bottom - top);
    el.chart.append(
      svg("rect", { class: "usage-bar-input", x: x(index), y: base - inputH, width: barWidth, height: inputH, rx: 3 }),
      svg("rect", { class: "usage-bar-cache", x: x(index), y: base - inputH - cacheH, width: barWidth, height: cacheH }),
      svg("rect", { class: "usage-bar-output", x: x(index), y: base - inputH - cacheH - outputH, width: barWidth, height: outputH, rx: 3 }),
    );
    const show = !compact.matches || index === 0 || index === series.length - 1 || (index + 1) % 7 === 0;
    if (show) {
      el.chart.append(svg("text", {
        class: "usage-axis",
        "text-anchor": "middle",
        x: x(index) + barWidth / 2,
        y: height - 10,
      }, dayLabel(day.day)));
    }
  });
  el.chart.setAttribute("aria-label", `Biểu đồ ${unit === "token" ? "token" : "lượt"} theo ngày; mức cao nhất ${formatCompact(Math.max(...values, 0))}.`);
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

async function load() {
  const days = Number(el.period.value) || 30;
  setStatus("Đang tổng hợp token từ trace…");
  try {
    payload = await getJson(traceRangeUrl("/api/traces", days, 200));
    usage = aggregateUsage(payload.traces);
    series = completeDays(usage.days, rollingRange(days));
    render();
    const clipped = Number(payload.total) > (payload.traces?.length || 0);
    setStatus(
      clipped
        ? `Đang hiển thị ${payload.traces.length}/${payload.total} lượt mới nhất.`
        : `${formatInteger(usage.totals.turns)} lượt trong ${days} ngày.`,
      clipped ? "" : "success",
    );
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
void load();
