import { el } from "./dom.js";
import { modes } from "./data.js";
import { state } from "./state.js";
import { mountStaff } from "./staff.js";
import { traceScoreFromMessages } from "./trace-score.js";
import {
  cleanTitle,
  escapeHtml,
  fmtCost,
  fmtInt,
  fmtIso,
  fmtLatency,
  shortModel,
  statusLabel,
} from "./util.js";
import { byDayBlock } from "./trace-chart.js";
import { timelineSpans } from "./trace-timeline.js";
import {
  activeParams,
  bindFilterOutside,
  closeFilterPops,
  pillBlock,
  traceFilter,
} from "./trace-filter.js";


// ---- formatters (vi-VN, no UNKNOWN) ----

function fmtDayPart(value) {
  const stamp = new Date(String(value));
  if (!value || Number.isNaN(stamp.getTime())) return "—";
  return stamp.toLocaleDateString("vi-VN", { day: "numeric", month: "short" });
}

function fmtTimePart(value) {
  const stamp = new Date(String(value));
  if (!value || Number.isNaN(stamp.getTime())) return "—";
  return stamp.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function shortSession(sessionId) {
  const match = String(sessionId || "").match(/([0-9a-f]{4})$/i);
  return match ? `ses_${match[1].toLowerCase()}` : "ses_—";
}

// ---- API ----

function traceQuery(params) {
  const qs = new URLSearchParams();
  if (params.model) qs.set("model", params.model);
  if (params.status) qs.set("status", params.status);
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  if (params.limit) qs.set("limit", String(params.limit));
  return qs.toString();
}

async function fetchTraceStats(params = {}) {
  const qs = traceQuery(params);
  const response = await fetch("/api/traces/stats" + (qs ? "?" + qs : ""), { cache: "no-store" });
  if (!response.ok) return null;
  return response.json();
}

async function fetchTraces(params = {}) {
  const qs = traceQuery(params);
  const response = await fetch("/api/traces" + (qs ? "?" + qs : ""), { cache: "no-store" });
  if (!response.ok) return null;
  return response.json();
}

async function fetchTraceDetail(sessionId, turnIndex) {
  const response = await fetch(`/api/traces/${encodeURIComponent(sessionId)}/${turnIndex}`, { cache: "no-store" });
  if (!response.ok) return null;
  return response.json();
}

// ---- pages ----

export function pagesFromTraces(stats, list, pillStats = null) {
  const traces = Array.isArray(list && list.traces) ? list.traces : [];
  return [overviewPage(stats, traces, pillStats), ...sessionPages(traces)];
}

function sessionPages(traces) {
  const order = [];
  const groups = new Map();
  for (const item of traces) {
    const id = String(item.session_id || "");
    if (!id) continue;
    if (!groups.has(id)) {
      groups.set(id, []);
      order.push(id);
    }
    groups.get(id).push(item);
  }
  return order.map((id) => sessionPage(groups.get(id)));
}

function overviewPage(stats, traces, pillStats) {
  const requests = Number(stats && stats.totals && stats.totals.requests) || 0;
  return {
    title: "Tổng quan",
    hideTitle: true,
    date: `${fmtInt(requests)} lượt`,
    tag: "",
    tone: "trace",
    kicker: "Trace · AgentLoop",
    note: "",
    body: overviewBody(stats, traces, pillStats),
  };
}

function overviewBody(stats, traces, pillStats) {
  const totals = (stats && stats.totals) || {};
  const prompt = Number(totals.prompt_tokens) || 0;
  const cached = Number(totals.cached_tokens) || 0;
  const completion = Number(totals.completion_tokens) || 0;
  const byModel = Array.isArray(stats && stats.by_model) ? stats.by_model : [];
  const byDay = Array.isArray(stats && stats.by_day) ? stats.by_day : [];
  const byHour = Array.isArray(stats && stats.by_hour) ? stats.by_hour : [];
  const byStatus = Array.isArray(stats && stats.by_status) ? stats.by_status : [];
  // stats?model= collapses models to the selected one — pills need the unfiltered list.
  const models = Array.isArray(pillStats && pillStats.models)
    ? pillStats.models
    : Array.isArray(stats && stats.models)
      ? stats.models
      : [];
  return `<div class="book-reading">
      ${pillBlock(models, byModel, byStatus)}
      <div class="stat-row">
        <div><strong>${fmtInt(totals.requests)}</strong><span>request</span></div>
        <div><strong>${fmtInt(prompt)}</strong><span><small>cache ${fmtInt(cached)}</small></span></div>
        <div><strong>${fmtInt(completion)}</strong><span>output</span></div>
        <div><strong>${fmtCost(totals.cost_usd)}</strong><span>cost</span></div>
      </div>
      ${byDayBlock(byDay, byHour, traceFilter.range === "1d")}
      ${recentBlock(traces)}
    </div>`;
}

const RECENT_LIMIT = 12;

// Recent requests — one row per turn, newest first, model name doubles as the filter.
function recentBlock(traces) {
  const rows = (Array.isArray(traces) ? traces : []).slice(0, RECENT_LIMIT);
  if (!rows.length) {
    return `<section class="trace-section"><h3>Recent request</h3><p class="suggest-empty">Chưa có request nào.</p></section>`;
  }
  const ledger = rows
    .map((item) => {
      const status = String(item.status || "");
      return `<div class="trace-ledger-row">
          <span class="trace-ledger-day" title="ngày">${escapeHtml(fmtDayPart(item.started_at))}</span>
          <span class="trace-ledger-time" title="giờ">${escapeHtml(fmtTimePart(item.started_at))}</span>
          <button type="button" class="trace-ledger-name" data-trace-pill="model" data-value="${escapeHtml(String(item.model || "unknown"))}">${escapeHtml(shortModel(item.model) || "không rõ")}</button>
          <span class="trace-ledger-status is-${escapeHtml(status)}" title="trạng thái">${escapeHtml(statusLabel(status))}</span>
          <span class="trace-ledger-num" title="input">${fmtInt(item.prompt_tokens)}</span>
          <span class="trace-ledger-num" title="output">${fmtInt(item.completion_tokens)}</span>
          <span class="trace-ledger-num" title="cache">${fmtInt(item.cached_tokens)}</span>
          <span class="trace-ledger-num" title="cost">${fmtCost(item.cost_usd)}</span>
        </div>`;
    })
    .join("");
  return `<section class="trace-section">
      <h3>Recent request</h3>
      <p class="trace-section-note">${fmtInt(rows.length)} request gần nhất</p>
      <div class="trace-ledger-head" aria-hidden="true">
        <span>ngày</span><span>giờ</span><span>model</span><span>trạng thái</span><span>input</span><span>output</span><span>cache</span><span>cost</span>
      </div>
      ${ledger}
    </section>`;
}

function sessionPage(turnsNewestFirst) {
  const newest = turnsNewestFirst[0];
  const failed = turnsNewestFirst.some((item) => item.status === "failed");
  const turns = [...turnsNewestFirst].sort((a, b) => (a.turn_index || 0) - (b.turn_index || 0));
  return {
    title: newest.title && newest.title !== newest.session_id ? cleanTitle(newest.title) : "Phiên không tên",
    date: escapeHtml(`${fmtInt(turns.length)} lượt · ${fmtIso(newest.started_at)}`),
    tag: "",
    tone: "trace",
    status: failed ? "failed" : String(newest.status || ""),
    model: String(newest.model || ""),
    sessionId: String(newest.session_id || ""),
    turns,
    selectedTurnIndex: Number(newest.turn_index) || 0,
    score: null,
    body: "",
  };
}


// ---- hydrate (assign pages, no innerHTML dump) ----

async function fetchTraceBundle(params) {
  // pill dropdown needs the unfiltered model list — refetch stats without ?model=
  const [stats, list, pillStats] = await Promise.all([
    fetchTraceStats(params),
    fetchTraces({ ...params, limit: 50 }),
    params.model ? fetchTraceStats({ ...params, model: undefined }) : null,
  ]);
  return stats && list ? { stats, list, pillStats } : null;
}

export async function hydrateTrace() {
  const bundle = await fetchTraceBundle(activeParams());
  if (!bundle) return;
  applyTracePages(bundle.stats, bundle.list, bundle.pillStats);
}

function applyTracePages(stats, list, pillStats = null) {
  modes.trace = {
    label: "Trace",
    listLabel: "Phiên gần đây",
    kicker: "Trace · AgentLoop",
    note: "",
    chips: [],
    pages: pagesFromTraces(stats, list, pillStats),
  };
  const count = el.modeList.querySelector('[data-mode="trace"] .mode-count');
  if (count) count.textContent = String(Math.max((modes.trace.pages || []).length - 1, 0));
}

// Callback injected by render.js so trace.js never imports renderPage (circular import).
let onTraceRefilter = null;
let onTraceTurn = null;
async function refilterTrace() {
  const bundle = await fetchTraceBundle(activeParams());
  if (!bundle) return;
  applyTracePages(bundle.stats, bundle.list, bundle.pillStats);
  state.activePageIndex = 0;
  if (typeof onTraceRefilter === "function") onTraceRefilter();
}

export function bindTraceOverview(root, { onRefilter, onTurn } = {}) {
  if (!root) return;
  if (typeof onRefilter === "function") onTraceRefilter = onRefilter;
  if (typeof onTurn === "function") onTraceTurn = onTurn;
  bindFilterOutside();
  root.querySelectorAll("[data-filter-toggle]").forEach((toggle) => {
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const pop = toggle.parentElement.querySelector(".trace-filter-pop");
      const wasOpen = pop && !pop.hidden;
      closeFilterPops(root);
      if (!wasOpen && pop) {
        pop.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
      }
    });
  });
  root.querySelectorAll("[data-trace-pill]").forEach((pill) => {
    pill.addEventListener("click", () => {
      const group = pill.dataset.tracePill;
      const value = pill.dataset.value;
      if (!group || !value || traceFilter[group] === value) return;
      traceFilter[group] = value; // mutate in place — one shared filter state
      void refilterTrace();
    });
  });
  root.querySelectorAll("[data-trace-turn]").forEach((button) => {
    button.addEventListener("click", () => {
      const page = modes.trace.pages[state.activePageIndex];
      if (!page || !page.sessionId) return;
      page.selectedTurnIndex = Number(button.dataset.traceTurn);
      page.score = null;
      void fillTraceAt(state.activePageIndex).then(() => {
        if (typeof onTraceTurn === "function") onTraceTurn();
      });
    });
  });
}

// ---- turn paper (lazy, like fillChatAt) ----

export async function fillTraceAt(index) {
  const page = modes.trace.pages[index];
  if (!page || !page.sessionId) return false;
  state.activePageIndex = index;
  const turnIndex = Number.isInteger(page.selectedTurnIndex) ? page.selectedTurnIndex : 0;
  const detail = await fetchTraceDetail(page.sessionId, turnIndex);
  if (!detail) {
    if (!page.body) {
      page.body = `<div class="music-page"><p class="suggest-empty">Không tải được phiên này.</p></div>`;
    }
    return false;
  }
  const messages = Array.isArray(detail.messages) ? detail.messages : [];
  const n = Array.isArray(page.turns) ? page.turns.length : 1;
  page.title = cleanTitle(detail.title || page.sessionId);
  page.model = String(detail.model || page.model || "");
  page.kicker = `${escapeHtml(shortSession(page.sessionId))} · ${fmtInt(n)} lượt`;
  page.note = tokenLine(detail);
  page.score = traceScoreFromMessages(messages);
  page.body = sessionBody(page, detail, messages);
  return true;
}

function tokenLine(detail) {
  const cached = Number(detail.cached_tokens) || 0;
  const prompt = Number(detail.prompt_tokens) || 0;
  const cachePct = prompt > 0 ? Math.round((cached / prompt) * 100) : 0;
  const cacheBadge = cached > 0 ? ` <span class="trace-token-badge">cache ${fmtInt(cached)} · ${cachePct}%</span>` : "";
  return `input ${fmtInt(detail.prompt_tokens)}${cacheBadge} → output ${fmtInt(detail.completion_tokens)} · ${fmtCost(detail.cost_usd)} · ${fmtLatency(detail.latency_ms)}`;
}

function sessionBody(page, detail, messages) {
  const selected = Number(page.selectedTurnIndex) || 0;
  const failed = String(detail.status) === "failed";
  const turnLat = Number(detail.latency_ms) || 0;
  const spans = timelineSpans(messages, turnLat);
  const timeline = spans.length
    ? `<ol class="phase-list trace-timeline">${spans}</ol>`
    : `<p class="suggest-empty">Không có pha nào trong lượt này.</p>`;
  const picker = turnPicker(page.turns || [], selected);
  return `<div class="music-page${failed ? " is-failed" : ""}">
      ${picker}
      <article class="entry entry-thyca" aria-label="Nhạc cốt lượt"></article>
      ${timeline}
    </div>`;
}

function turnPicker(turns, selected) {
  if (!turns.length) return "";
  const chips = turns
    .map((item) => {
      const idx = Number(item.turn_index) || 0;
      const pressed = idx === selected;
      const label = `lượt ${idx + 1} · ${fmtIso(item.started_at) || statusLabel(item.status)}`;
      return `<button type="button" class="suggestion-chip" data-trace-turn="${idx}" aria-pressed="${pressed}">${escapeHtml(label)}</button>`;
    })
    .join("");
  return `<div class="trace-turn-list" role="list">${chips}</div>`;
}

// ---- chrome helpers called from render.js ----

export function mountTraceStaff(root, page) {
  if (!root || !page || !page.score) return;
  const article = root.querySelector(".entry-thyca");
  if (article) mountStaff(article, page.score);
}

// Mini-player is a plaque: title + model · status. No replay button.
export function updateMiniPlayer(page) {
  const isTurn = Boolean(page && page.sessionId);
  el.miniPlayer.hidden = !isTurn;
  if (!isTurn) return;
  const title = document.getElementById("mini-phase");
  const sub = document.getElementById("mini-round");
  if (title) title.innerHTML = page.title;
  if (sub) sub.textContent = `${shortModel(page.model) || "—"} · ${statusLabel(page.status)}`;
}
