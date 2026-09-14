import { getJson } from "./backend/api.js";
import {
  rollingRange,
  selectModels,
  splitPromptTokens,
  traceRangeUrl,
} from "./backend/analytics-data.js";
import { formatCompact, formatCost, formatDate, formatInteger } from "./backend/format.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const el = {
  period: document.querySelector("#cost-period"),
  total: document.querySelector("#cost-total"),
  range: document.querySelector("#cost-range"),
  chart: document.querySelector("#cost-chart"),
  models: document.querySelector("#cost-models"),
  status: document.querySelector("#cost-status"),
  search: document.querySelector("#model-search"),
  sorts: [...document.querySelectorAll("[data-sort]")],
};
const compact = matchMedia("(max-width: 56rem)");
let stats = null;
let sort = "cost-desc";

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

// One model per card, shaped like a Nhật ký page: title, summary, token tags,
// and the cost in the corner. Open at rest — nothing to click.
function modelCard(model, total) {
  const card = document.createElement("article");
  card.className = "cost-model-card";
  const copy = document.createElement("div");
  copy.className = "cost-model-copy";
  const name = document.createElement("h3");
  name.textContent = model.model || "unknown";
  const meta = document.createElement("p");
  meta.textContent = `${formatInteger(model.requests)} request · ${formatCompact(model.total_tokens)} token`;
  const tokens = document.createElement("div");
  tokens.className = "cost-model-tokens";
  // Input here is the uncached part only; cache is reported inside prompt_tokens.
  const { input, cache } = splitPromptTokens(model.prompt_tokens, model.cached_tokens);
  for (const [label, value] of [
    ["Input", input],
    ["Cache", cache],
    ["Output", model.completion_tokens],
  ]) {
    const tag = document.createElement("span");
    tag.textContent = `${label} ${formatCompact(value)}`;
    tokens.append(tag);
  }
  copy.append(name, meta, tokens);
  const share = document.createElement("span");
  share.className = "screen-badge cost-model-share";
  share.textContent = shareOf(model.cost_usd, total);
  const cost = document.createElement("strong");
  cost.className = "cost-model-cost";
  cost.textContent = formatCost(model.cost_usd);
  card.append(copy, share, cost);
  return card;
}

function shareOf(value, total) {
  return value == null || !total ? "—" : `${Math.round(Number(value) / total * 100)}%`;
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
  const rows = selectModels(stats.by_model, { sort, query: el.search?.value || "" });
  el.models.replaceChildren(...rows.map((model) => modelCard(model, Number(total) || 0)));
  el.sorts.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.sort === sort));
  });
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "screen-note";
    empty.textContent = (stats.by_model || []).length
      ? "Không có mô hình nào khớp tên đang tìm."
      : "Chưa có lượt nào trong khoảng này.";
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
el.search?.addEventListener("input", render);
el.sorts.forEach((button) => {
  button.addEventListener("click", () => {
    sort = button.dataset.sort || "cost-desc";
    render();
  });
});
compact.addEventListener("change", render);
void load();
