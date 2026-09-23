import {
  NO_SESSION_KEY,
  aggregateSessions,
  knownCostTotal,
  modelTurnCoverage,
  priceRates,
  estimatedCostSplit,
  shareLabel,
  shareRatio,
} from "./cost-data.js";
import {
  selectModels,
} from "../../shared/js/analytics-data.js";
import { formatCost, formatInteger } from "../../shared/js/format.js";
import {
  JOURNAL_PAGE_SIZE,
  journalPageCount,
  journalClampPage,
  buildPager,
  syncPager,
} from "../../shared/js/pager.js";
import { view, sort, el } from "./cost-view.js";

// Row renderers for the Cost journal: model/session entries plus the two
// paged lists. Journal paging state lives here with the lists it drives;
// the view (cost-view.js) restarts it on a fresh snapshot, search or sort.

// Open/closed "Đơn giá" disclosures, keyed by model name inside the current
// snapshot: repaging/searching/sorting re-renders entries and would else
// collapse every disclosure back to closed (TASK-018 follow-up).
const openPricing = new Set();

let modelsPage = 1;
let modelsPages = 1;
let sessionsPage = 1;
let sessionsPages = 1;

const modelsPager = buildPager((delta) => {
  modelsPage = journalClampPage(modelsPage + delta, modelsPages);
  renderModels();
});
const sessionsPager = buildPager((delta) => {
  sessionsPage = journalClampPage(sessionsPage + delta, sessionsPages);
  renderSessions();
});

// Paging restarts shared with the view: a fresh snapshot restarts both
// journals, a new sort restarts the models journal.
function resetModelsPage() {
  modelsPage = 1;
}

function resetPagingForSnapshot() {
  modelsPage = 1;
  sessionsPage = 1;
}

// Search re-renders the models list from the fixed snapshot; the share
// denominator never moves and no refetch happens.
function onCostSearch() {
  if (view) {
    modelsPage = 1; // the filtered set changed: back to its first page
    renderModels();
  }
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
  // One coverage style for model/session rows ("đã định giá N"), never a
  // second denominator.
  return `đã định giá ${formatInteger(pricedTurns)}`;
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

/* Cost-only chart row: same row markup as Request theo mô hình (shared
   .request-model-* rules in dashboard.css). The bar is the cost share of
   the snapshot total; the Đơn giá disclosure stays — it is cost detail. */
function modelEntry(model, denominator, coverage) {
  const item = document.createElement("li");
  item.className = "request-model-row";
  const head = document.createElement("div");
  head.className = "request-model-head";
  const name = document.createElement("span");
  name.className = "request-model-name";
  name.textContent = model.model || "unknown";
  const amount = document.createElement("span");
  amount.className = "request-model-count";
  amount.textContent = formatCost(model.cost_usd);
  const share = document.createElement("span");
  share.className = "request-model-share";
  share.textContent = shareLabel(model.cost_usd, denominator);
  const bar = document.createElement("span");
  bar.className = "request-model-bar";
  bar.setAttribute("aria-hidden", "true");
  const fill = document.createElement("span");
  fill.className = "request-model-fill";
  fill.style.width = shareRatio(model.cost_usd, denominator) ?? "0%";
  bar.append(fill);
  head.append(name, amount, share);
  item.append(head, bar);
  // by_model carries no turn count: missing-turn coverage for this model is
  // derived from the loaded rows, so a partial aggregate is labeled as such
  // instead of posing as complete.
  if (coverage && coverage.pricedTurns < coverage.turns) {
    const note = document.createElement("span");
    note.className = "request-model-share";
    note.textContent = partialNote(coverage.pricedTurns, coverage.turns);
    item.append(note);
  }
  item.append(pricingDetails(model, denominator));
  return item;
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
  const page = rows.slice(start, start + JOURNAL_PAGE_SIZE);
  const list = document.createElement("ul");
  list.className = "request-model-chart";
  list.replaceChildren(
    ...page.map((model) => modelEntry(model, denominator, coverage.get(model.model || "unknown"))),
  );
  el.models.replaceChildren(list);
  syncPager(modelsPager, modelsPage, modelsPages);
  el.sorts.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.sort === sort));
  });
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "screen-note";
    empty.textContent = models.length
      ? "Không có mô hình nào khớp tên đang tìm."
      : "Chưa có lượt nào trong khoảng này.";
    el.models.append(empty);
  }
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

export {
  modelsPager,
  sessionsPager,
  openPricing,
  metricValue,
  metricMeta,
  partialNote,
  renderModels,
  renderSessions,
  resetModelsPage,
  resetPagingForSnapshot,
  onCostSearch,
};
