import { getJson } from "./backend/api.js";
import { fetchAllTraces } from "./backend/dashboard-today.js";
import {
  NO_SESSION_KEY,
  aggregateSessions,
  averageDisplay,
  estimatedCostSplit,
  knownCostTotal,
  modelTurnCoverage,
  overviewMetrics,
  priceRates,
  shareLabel,
  shareRatio,
} from "./backend/cost-data.js";
import {
  rollingRange,
  selectModels,
  splitPromptTokens,
} from "./backend/analytics-data.js";
import { formatCost, formatInteger } from "./backend/format.js";

const el = {
  period: document.querySelector("#cost-period"),
  coverage: document.querySelector("#cost-coverage"),
  total: document.querySelector("#cost-total"),
  totalMeta: document.querySelector("#cost-total-meta"),
  average: document.querySelector("#cost-average"),
  averageMeta: document.querySelector("#cost-average-meta"),
  input: document.querySelector("#cost-input"),
  inputMeta: document.querySelector("#cost-input-meta"),
  output: document.querySelector("#cost-output"),
  outputMeta: document.querySelector("#cost-output-meta"),
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

// >>> journal-pager (pure math; extracted by unit tests, no DOM here)
const JOURNAL_PAGE_SIZE = 12;
function journalPageCount(totalItems, perPage = JOURNAL_PAGE_SIZE) {
  return Math.max(1, Math.ceil(totalItems / perPage));
}
function journalClampPage(page, pages) {
  return Math.min(Math.max(page, 1), pages);
}
// <<< journal-pager

let modelsPage = 1;
let modelsPages = 1;
let sessionsPage = 1;
let sessionsPages = 1;

// Open/closed "Đơn giá" disclosures, keyed by model name inside the current
// snapshot: repaging/searching/sorting re-renders entries and would else
// collapse every disclosure back to closed (TASK-018 follow-up).
const openPricing = new Set();

function buildPager(onStep) {
  const nav = document.createElement("nav");
  nav.className = "journal-pager";
  nav.hidden = true;
  const prev = document.createElement("button");
  prev.type = "button";
  prev.className = "screen-button journal-pager-step";
  prev.textContent = "‹ Trước";
  prev.setAttribute("aria-label", "Trang trước");
  const label = document.createElement("span");
  label.className = "journal-pager-label";
  label.setAttribute("aria-live", "polite");
  const next = document.createElement("button");
  next.type = "button";
  next.className = "screen-button journal-pager-step";
  next.textContent = "Sau ›";
  next.setAttribute("aria-label", "Trang sau");
  prev.addEventListener("click", () => onStep(-1));
  next.addEventListener("click", () => onStep(1));
  nav.append(prev, label, next);
  return { nav, prev, next, label };
}

function syncPager(pager, page, pages) {
  pager.nav.hidden = pages <= 1;
  pager.prev.disabled = page <= 1;
  pager.next.disabled = page >= pages;
  pager.label.textContent = `${page} / ${pages}`;
}

const modelsPager = buildPager((delta) => {
  modelsPage = journalClampPage(modelsPage + delta, modelsPages);
  renderModels();
});
const sessionsPager = buildPager((delta) => {
  sessionsPage = journalClampPage(sessionsPage + delta, sessionsPages);
  renderSessions();
});
el.models.after(modelsPager.nav); // pager below the list, no layout shift
el.sessions.after(sessionsPager.nav);

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

function metricValue(node, text) {
  node.textContent = text;
}

function metricMeta(node, parts) {
  node.replaceChildren(...parts.filter(Boolean).map((text) => {
    const span = document.createElement("span");
    span.textContent = text;
    return span;
  }));
}

function partialNote(pricedTurns, turns) {
  if (pricedTurns >= turns) return "";
  return `${turns - pricedTurns} lượt chưa định giá`;
}

function renderOverview() {
  const { stats, overview } = view;
  setCoverage(coverageNotes(overview, stats));

  // stats.totals is the API's authoritative stored-cost total for the range;
  // turn/request counts come from the same fully-paged row snapshot.
  const total = stats?.totals?.cost_usd;
  const priced = total == null ? null : Number(total);
  metricValue(el.total, priced == null ? "—" : formatCost(priced, 2));
  metricMeta(el.totalMeta, [
    overview.turns ? `${formatInteger(overview.turns)} lượt` : "",
    overview.turns ? `${formatInteger(overview.requests)} lần gọi model` : "",
    partialNote(overview.pricedTurns, overview.turns),
  ]);

  // Average is per TURN (deduped trace rows), never per model request —
  // formula and wording live in averageDisplay() (tested).
  const display = averageDisplay(overview);
  metricValue(el.average, display.value == null ? "—" : formatCost(display.value, 6));
  metricMeta(el.averageMeta, display.meta);

  // Full input side (cache is a subset of prompt_tokens, so input + cache is
  // the raw prompt total); the cache part is called out beside it.
  metricValue(el.input, formatInteger(overview.promptTokens));
  metricMeta(el.inputMeta, [
    `gồm ${formatInteger(overview.cacheTokens)} token cache`,
  ]);
  metricValue(el.output, formatInteger(overview.outputTokens));
  metricMeta(el.outputMeta, [`${formatInteger(overview.requests)} lần gọi model`]);
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
  for (const node of [el.total, el.average, el.input, el.output]) metricValue(node, "—");
  for (const node of [el.totalMeta, el.averageMeta, el.inputMeta, el.outputMeta]) node.replaceChildren();
  el.models.replaceChildren();
  el.sessions.replaceChildren();
  modelsPager.nav.hidden = true; // no stale pager while the new period loads
  sessionsPager.nav.hidden = true;
}

function pricingDetails(model, denominator) {
  const details = document.createElement("details");
  details.className = "cost-pricing";
  const summary = document.createElement("summary");
  summary.textContent = "Đơn giá";
  const body = document.createElement("p");
  const rates = priceRates(view.config, model.model);
  if (!rates) {
    body.textContent = "Chưa có đơn giá cho mô hình này trong cấu hình — các mức chi phí đầu vào/đầu ra không tách được.";
  } else {
    const rate = (value) => (value == null ? "—" : `$${value.toLocaleString("vi-VN")}/1M token`);
    const estimate = estimatedCostSplit(model, rates);
    const split = estimate
      ? ` — ước tính theo đơn giá hiện tại: đầu vào ${estimate.inputUsd == null ? "—" : formatCost(estimate.inputUsd)} · cache ${estimate.cacheUsd == null ? "—" : formatCost(estimate.cacheUsd)} · đầu ra ${estimate.outputUsd == null ? "—" : formatCost(estimate.outputUsd)}`
      : "";
    body.textContent = `Đầu vào ${rate(rates.input)} · cache ${rate(rates.cache)} · đầu ra ${rate(rates.output)}${split}. Chi phí ghi nhận ${formatCost(model.cost_usd)}${shareLabel(model.cost_usd, denominator) === "—" ? "" : ` (${shareLabel(model.cost_usd, denominator)} tổng)`} do backend tính lúc chạy; mức tách theo đơn giá chỉ là ước tính với giá hiện tại, không phải số đã ghi nhận.`;
  }
  details.append(summary, body);
  // Restore the open state this model had before the last re-render of the
  // same snapshot, and keep it in sync while the user opens/closes it.
  details.open = openPricing.has(model.model);
  details.addEventListener("toggle", () => {
    if (details.open) openPricing.add(model.model);
    else openPricing.delete(model.model);
  });
  return details;
}

function modelEntry(model, index, denominator, coverage) {
  const entry = document.createElement("li");
  entry.className = "journal-entry";
  const gutter = document.createElement("span");
  gutter.className = "journal-date";
  gutter.textContent = shareLabel(model.cost_usd, denominator);
  const body = document.createElement("div");
  body.className = "journal-body";
  const title = document.createElement("div");
  title.className = "journal-row-title";
  const name = document.createElement("h3");
  name.textContent = model.model || "unknown";
  const amount = document.createElement("strong");
  amount.className = "journal-amount";
  amount.textContent = formatCost(model.cost_usd);
  title.append(name, amount);
  const meter = document.createElement("div");
  meter.className = "journal-meter";
  meter.setAttribute("aria-hidden", "true");
  const fill = document.createElement("span");
  const ratio = shareRatio(model.cost_usd, denominator);
  if (ratio != null) fill.style.setProperty("--share", ratio);
  meter.append(fill);
  const meta = document.createElement("p");
  meta.className = "journal-meta";
  const calls = document.createElement("span");
  calls.textContent = `${formatInteger(model.requests)} lần gọi model`;
  meta.append(calls);
  // prompt_tokens already contains cached_tokens (backend semantics), so the
  // uncached input is split out and cache is reported beside it, never added
  // twice.
  const { input, cache } = splitPromptTokens(model.prompt_tokens, model.cached_tokens);
  for (const [label, value] of [["Đầu vào", input], ["Cache", cache], ["Đầu ra", model.completion_tokens]]) {
    const token = document.createElement("span");
    token.textContent = `${label} ${formatInteger(value)} token`;
    meta.append(token);
  }
  // by_model carries no turn count: missing-turn coverage for this model is
  // derived from the loaded rows, so a partial aggregate is labeled as such
  // instead of posing as complete.
  if (coverage && coverage.pricedTurns < coverage.turns) {
    const partial = document.createElement("span");
    partial.textContent = partialNote(coverage.pricedTurns, coverage.turns);
    meta.append(partial);
  }
  body.append(title, meter, meta, pricingDetails(model, denominator));
  entry.append(gutter, body);
  return entry;
}

function sessionEntry(session, denominator) {
  const entry = document.createElement("li");
  entry.className = "journal-entry";
  const gutter = document.createElement("span");
  gutter.className = "journal-date";
  gutter.textContent = `${formatInteger(session.turns)} lượt`;
  const body = document.createElement("div");
  body.className = "journal-body";
  const title = document.createElement("div");
  title.className = "journal-row-title";
  const name = document.createElement("h3");
  if (session.sessionId) {
    // Omitted turn = newest turn of the session in the trace viewer.
    const link = document.createElement("a");
    link.href = `./trace.html?session=${encodeURIComponent(session.sessionId)}`;
    link.textContent = session.title || session.sessionId;
    name.append(link);
  } else {
    name.textContent = NO_SESSION_KEY;
  }
  const amount = document.createElement("strong");
  amount.className = "journal-amount";
  amount.textContent = session.costUsd == null ? "—" : formatCost(session.costUsd);
  title.append(name, amount);
  const meter = document.createElement("div");
  meter.className = "journal-meter";
  meter.setAttribute("aria-hidden", "true");
  const fill = document.createElement("span");
  const ratio = shareRatio(session.costUsd, denominator);
  if (ratio != null) fill.style.setProperty("--share", ratio);
  meter.append(fill);
  const meta = document.createElement("p");
  meta.className = "journal-meta";
  for (const text of [
    `${formatInteger(session.requests)} lần gọi model`,
    `Đầu vào ${formatInteger(session.inputTokens)} token`,
    `Cache ${formatInteger(session.cacheTokens)} token`,
    `Đầu ra ${formatInteger(session.outputTokens)} token`,
    partialNote(session.pricedTurns, session.turns),
    session.sessionId ? "" : "dòng này gom các lượt thiếu session ID",
  ].filter(Boolean)) {
    const span = document.createElement("span");
    span.textContent = text;
    meta.append(span);
  }
  body.append(title, meter, meta);
  entry.append(gutter, body);
  return entry;
}

function renderModels() {
  if (!view) return; // no snapshot (mid-load or after failure): render nothing
  const models = view.stats?.by_model || [];
  // Denominator is the whole snapshot's cost, fixed before the search filter.
  const denominator = knownCostTotal(models);
  const coverage = modelTurnCoverage(view.rows);
  const rows = selectModels(models, { sort, query: el.search?.value || "" });
  modelsPages = journalPageCount(rows.length);
  modelsPage = journalClampPage(modelsPage, modelsPages);
  const start = (modelsPage - 1) * JOURNAL_PAGE_SIZE;
  el.models.replaceChildren(
    ...rows.slice(start, start + JOURNAL_PAGE_SIZE)
      .map((model, index) => modelEntry(model, index, denominator, coverage.get(model.model || "unknown"))),
  );
  syncPager(modelsPager, modelsPage, modelsPages);
  el.sorts.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.sort === sort));
  });
  if (!rows.length) el.models.append(emptyItem(models.length
    ? "Không có mô hình nào khớp tên đang tìm."
    : "Chưa có lượt nào trong khoảng này."));
}

function renderSessions() {
  if (!view) return; // no snapshot (mid-load or after failure): render nothing
  const denominator = view.overview.costUsd;
  const sessions = aggregateSessions(view.rows);
  sessionsPages = journalPageCount(sessions.length);
  sessionsPage = journalClampPage(sessionsPage, sessionsPages);
  const start = (sessionsPage - 1) * JOURNAL_PAGE_SIZE;
  el.sessions.replaceChildren(
    ...sessions.slice(start, start + JOURNAL_PAGE_SIZE).map((session) => sessionEntry(session, denominator)),
  );
  syncPager(sessionsPager, sessionsPage, sessionsPages);
  if (!sessions.length) el.sessions.append(emptyItem("Chưa có phiên nào trong khoảng này."));
}

/* Empty states stay valid list children instead of a bare <p> inside <ul>. */
function emptyItem(text) {
  const item = document.createElement("li");
  item.className = "screen-note";
  item.textContent = text;
  return item;
}

function render() {
  if (!view) return;
  renderOverview();
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
    modelsPage = 1; // fresh snapshot: restart both journals at page 1
    sessionsPage = 1;
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

el.period?.addEventListener("change", () => void load());
el.retry?.addEventListener("click", () => void load());
// Search and sort re-render from the fixed snapshot; the share denominator
// never moves and no refetch happens.
el.search?.addEventListener("input", () => {
  if (view) {
    modelsPage = 1; // the filtered set changed: back to its first page
    renderModels();
  }
});
el.sorts.forEach((button) => {
  button.addEventListener("click", () => {
    sort = button.dataset.sort || "cost-desc";
    if (view) {
      modelsPage = 1; // the ordered set changed: back to its first page
      renderModels();
    }
  });
});
void load();
