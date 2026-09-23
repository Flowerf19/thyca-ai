import { getJson } from "../../shared/js/http.js";
import { fetchAllTraces } from "../../shared/js/dashboard-today.js";
import {
  averageDisplay,
  dailyCosts,
  overviewMetrics,
} from "./cost-data.js";
import {
  rollingRange,
} from "../../shared/js/analytics-data.js";
import { formatCost, formatInteger } from "../../shared/js/format.js";
import { drawBarChart } from "../../shared/js/bar-chart.js";
import {
  modelsPager,
  sessionsPager,
  openPricing,
  metricValue,
  metricMeta,
  renderModels,
  renderSessions,
  resetModelsPage,
  resetPagingForSnapshot,
  onCostSearch,
} from "./cost-rows.js";

// Cost journal view: snapshot state, loading and overview rendering. Row
// entries and the two paged lists live in cost-rows.js; the shared journal
// pager comes from shared/js/pager.js.

const el = {
  period: document.querySelector("#cost-period"),
  coverage: document.querySelector("#cost-coverage"),
  total: document.querySelector("#cost-total"),
  totalMeta: document.querySelector("#cost-total-meta"),
  chart: document.querySelector("#cost-chart"),
  models: document.querySelector("#cost-models"),
  sessions: document.querySelector("#cost-sessions"),
  status: document.querySelector("#cost-status"),
  retry: document.querySelector("#cost-retry"),
  search: document.querySelector("#model-search"),
  sorts: [...document.querySelectorAll("[data-sort]")],
};

let view = null; // one immutable snapshot: fixed range, stats, rows, config
let sort = "cost-desc";
let generation = 0; // rapid period changes: only the newest load may render

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
}

function setRetry(visible) {
  if (el.retry) el.retry.hidden = !visible;
}

function renderOverview() {
  const { stats, overview } = view;
  setCoverage(coverageNotes(overview, stats));

  // stats.totals is the API's authoritative stored-cost total for the range;
  // turn/request counts come from the same fully-paged row snapshot.
  const total = stats?.totals?.cost_usd;
  const priced = total == null ? null : Number(total);
  metricValue(el.total, priced == null ? "—" : formatCost(priced, 2));
  // Single KPI: the per-turn average folds into the total's meta line in
  // the chart header row.
  const display = averageDisplay(overview);
  metricMeta(el.totalMeta, [
    overview.turns ? `${formatInteger(overview.turns)} lượt` : "",
    display.value == null ? "" : `trung bình ${formatCost(display.value, 4)}/lượt`,
  ]);
}

/* Cost-by-day bar chart above the model table: same shared bar helper as the
   token screen, one accent bar per day across the snapshot range. */
function drawCostChart() {
  if (!view || !el.chart) return;
  const days = dailyCosts(view.stats?.by_day, view.range);
  const max = Math.max(...days.map((row) => row.value), 0);
  drawBarChart(el.chart, days.map((row) => ({
    day: row.day,
    segments: [{ class: "cost-bar", value: row.value }],
  })), {
    ariaLabel: `Biểu đồ chi phí theo ngày; cao nhất ${formatCost(max, 2)}.`,
    // Dollar grid steps land on fractions ($0.4 …) that formatCompact would
    // round into duplicate integer labels.
    formatAxis: (value) => `$${Number.isInteger(value) ? value : value.toFixed(1)}`,
  });
}

/* Coverage note: the stats and list endpoints are two separate reads — if
   their totals disagree, the window changed in between, so say so instead of
   picking one. */
function coverageNotes(overview, stats) {
  const notes = [];
  const statsCost = stats?.totals?.cost_usd;
  const rowsCost = overview.costUsd;
  const statsNumber = statsCost == null ? null : Number(statsCost);
  const diverged = (statsNumber == null) !== (rowsCost == null)
    || (statsNumber != null && rowsCost != null && Math.abs(statsNumber - rowsCost) > 1e-6);
  if (diverged) {
    notes.push(`Tổng theo stats (${formatCost(statsNumber)}) và theo danh sách trace (${formatCost(rowsCost)}) lệch nhau — dữ liệu có thể vừa thay đổi giữa hai lần đọc, hãy tải lại.`);
  }
  return notes;
}

function setCoverage(notes) {
  if (!el.coverage) return;
  el.coverage.replaceChildren(...notes.map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
}

function resetView() {
  // A failed/starting load must never leave numbers of a previous period on
  // screen.
  setCoverage([]);
  for (const node of [el.total]) metricValue(node, "—");
  for (const node of [el.totalMeta]) node.replaceChildren();
  el.chart?.replaceChildren();
  el.models.replaceChildren();
  el.sessions.replaceChildren();
  modelsPager.nav.hidden = true; // no stale pager while the new period loads
  sessionsPager.nav.hidden = true;
}

function render() {
  if (!view) return;
  renderOverview();
  drawCostChart();
  renderModels();
  renderSessions();
}

async function load() {
  const current = ++generation;
  const days = Number(el.period.value) || 30;
  const range = rollingRange(days); // fixed for this load; URLs derive from it
  const rangeQuery = new URLSearchParams({ from: range.from, to: range.to });
  setStatus("Đang tổng hợp chi phí từ trace…");
  setRetry(false);
  // Drop the old snapshot NOW: search, sort and the pagers read `view`, so a
  // live snapshot here would resurrect the previous period mid-load.
  view = null;
  resetView(); // no numbers of a previous period may survive a reload
  try {
    const [statsResult, rowsResult, configResult] = await Promise.allSettled([
      getJson(`/api/traces/stats?${rangeQuery}`),
      fetchAllTraces((offset) => {
        const pageQuery = new URLSearchParams({ from: range.from, to: range.to, limit: "200", offset: String(offset) });
        return getJson(`/api/traces?${pageQuery}`);
      }),
      getJson("/api/config"),
    ]);
    if (current !== generation) return; // a newer period load superseded this one
    if (statsResult.status === "rejected") throw statsResult.reason;
    if (rowsResult.status === "rejected") throw rowsResult.reason;
    const config = configResult.status === "fulfilled" ? configResult.value?.values || null : null;
    view = {
      range,
      stats: statsResult.value,
      rows: rowsResult.value,
      config,
      overview: overviewMetrics(rowsResult.value),
    };
    resetPagingForSnapshot(); // fresh snapshot: restart both journals at page 1
    openPricing.clear(); // disclosure state belongs to the old snapshot
    render();
    setStatus(config ? "" : "Không đọc được cấu hình — phần đơn giá hiển thị không đầy đủ.");
  } catch (error) {
    if (current !== generation) return;
    // Accepted failure: nothing of the old period may survive, so reset the
    // DOM again before showing the error (the snapshot was already dropped
    // at load start).
    view = null;
    resetView();
    setStatus(`Không tải đủ chi phí (${messageOf(error, "lỗi không rõ")}) — số liệu chưa đầy đủ.`, "error");
    setRetry(true);
  }
}

// Module bodies stay side-effect free (the view/rows import cycle is only
// ever exercised after evaluation); the entry calls this once the DOM and
// both modules are ready.
function bootCostJournal() {
  el.period?.addEventListener("change", () => void load());
  el.retry?.addEventListener("click", () => void load());
  // Search and sort re-render from the fixed snapshot; the share denominator
  // never moves and no refetch happens.
  el.search?.addEventListener("input", onCostSearch);
  el.sorts.forEach((button) => {
    button.addEventListener("click", () => {
      sort = button.dataset.sort || "cost-desc";
      if (view) {
        resetModelsPage(); // the ordered set changed: back to its first page
        renderModels();
      }
    });
  });
  el.models.after(modelsPager.nav); // pager below the list, no layout shift
  el.sessions.after(sessionsPager.nav);
  if (el.chart) new ResizeObserver(() => drawCostChart()).observe(el.chart);
  void load();
}

export { el, view, sort, bootCostJournal };
