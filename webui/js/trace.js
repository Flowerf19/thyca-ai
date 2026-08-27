import { el } from "./dom.js";
import { modes } from "./data.js";
import { escapeHtml } from "./memories.js";
import { state } from "./state.js";
import { mountStaff } from "./staff.js";
import { traceScoreFromMessages } from "./trace-score.js";

// Filter state for the overview pills. "all" = param omitted.
let traceFilter = { model: "all", status: "all", range: "30d" };

// ---- formatters (vi-VN, no UNKNOWN) ----

export function fmtInt(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("vi-VN");
}

export function fmtCost(value) {
  const n = Number(value);
  if (value == null || !Number.isFinite(n)) return "—";
  return "$" + n.toLocaleString("vi-VN", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
}

export function fmtLatency(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n < 1000) return `${fmtInt(Math.round(n))} ms`;
  const s = n / 1000;
  if (s < 10) return `${s.toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`;
  return `${Math.round(s).toLocaleString("vi-VN")} s`;
}

export function fmtIso(value) {
  if (!value) return "";
  const stamp = new Date(String(value));
  if (Number.isNaN(stamp.getTime())) return String(value);
  const time = stamp.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const day = stamp.toLocaleDateString("vi-VN", { day: "numeric", month: "short" });
  return `${time} ${day}`;
}

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

export function shortModel(model) {
  const raw = String(model || "").trim();
  if (!raw || raw.toLowerCase() === "unknown") return "";
  return raw;
}

// Strip markdown chrome from titles: **, __, wrapping backticks, collapsed whitespace.
export function cleanTitle(value) {
  const text = String(value || "")
    .replace(/\*\*/g, "")
    .replace(/__/g, "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return escapeHtml(text);
}

function statusLabel(status) {
  if (status === "completed") return "ok";
  if (status === "failed") return "lỗi";
  return String(status || "—");
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

function activeParams() {
  const params = {};
  if (traceFilter.model !== "all") params.model = traceFilter.model;
  if (traceFilter.status !== "all") params.status = traceFilter.status;
  const range = dateRange(traceFilter.range);
  if (range.from) params.from = range.from;
  if (range.to) params.to = range.to;
  return params;
}

// 1d = today only; 7d/30d = rolling from today, UTC YYYY-MM-DD
function dateRange(range) {
  if (range === "1d") {
    const today = new Date().toISOString().slice(0, 10);
    return { from: today, to: today };
  }
  if (range !== "7d" && range !== "30d") return { from: "", to: "" };
  const days = range === "7d" ? 7 : 30;
  const from = new Date(Date.now() - (days - 1) * 86400000);
  return { from: from.toISOString().slice(0, 10), to: "" };
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
      ${byDayBlock(byDay, byHour)}
      ${recentBlock(traces)}
    </div>`;
}

function optionButton(group, value, label, count = null) {
  const active = traceFilter[group] === value;
  const n = count == null ? "" : ` <span class="pill-count">${fmtInt(count)}</span>`;
  return `<button type="button" class="trace-filter-option" role="option" data-trace-pill="${escapeHtml(group)}" data-value="${escapeHtml(value)}" aria-pressed="${active}" aria-selected="${active}"><span class="trace-option-label">${escapeHtml(label)}</span>${n}</button>`;
}

function countFor(rows, key, value) {
  const row = rows.find((r) => r[key] === value);
  return row ? Number(row.requests) || 0 : null;
}

// One dropdown per group — inline pills don't scale once several models exist.
// highlight the toggle only when the filter differs from its default
const FILTER_DEFAULTS = { model: "all", status: "all", range: "30d" };

function filterMenu(group, label, options, activeLabel) {
  const engaged = traceFilter[group] !== FILTER_DEFAULTS[group];
  const items = options
    .map((opt) => optionButton(group, opt.value, opt.label, opt.count))
    .join("");
  return `<div class="trace-filter">
      <span class="pill-name" id="trace-filter-${escapeHtml(group)}">${escapeHtml(label)}</span>
      <div class="trace-filter-menu">
        <button type="button" class="suggestion-chip trace-filter-toggle${engaged ? " is-engaged" : ""}" data-filter-toggle="${escapeHtml(group)}" aria-haspopup="listbox" aria-expanded="false" aria-labelledby="trace-filter-${escapeHtml(group)}">
          <span class="trace-filter-value">${escapeHtml(activeLabel)}</span>
          <span class="trace-filter-caret" aria-hidden="true">▾</span>
        </button>
        <div class="trace-filter-pop" role="listbox" aria-labelledby="trace-filter-${escapeHtml(group)}" hidden>${items}</div>
      </div>
    </div>`;
}

function pillBlock(models, byModel, byStatus) {
  const known = models.filter((model) => shortModel(model));
  const unknownCount = countFor(byModel, "model", "unknown");
  const total = byModel.reduce((sum, entry) => sum + (Number(entry.requests) || 0), 0);
  const modelOptions = [{ value: "all", label: "Tất cả", count: total }].concat(
    known.map((model) => ({
      value: String(model),
      label: shortModel(model),
      count: countFor(byModel, "model", String(model)),
    })),
  );
  if (unknownCount != null || traceFilter.model === "unknown") {
    modelOptions.push({
      value: "unknown",
      label: "không rõ",
      count: unknownCount,
    });
  }
  const modelLabel =
    traceFilter.model === "all"
      ? "Tất cả"
      : shortModel(traceFilter.model) || (traceFilter.model === "unknown" ? "không rõ" : "Tất cả");
  const statusOptions = [
    { value: "all", label: "Tất cả", count: null },
    { value: "completed", label: "ok", count: countFor(byStatus, "status", "completed") },
    { value: "failed", label: "lỗi", count: countFor(byStatus, "status", "failed") },
    { value: "loop_limit", label: "loop_limit", count: countFor(byStatus, "status", "loop_limit") },
  ];
  const statusEntry = statusOptions.find((opt) => opt.value === traceFilter.status) || statusOptions[0];
  const rangeOptions = [
    { value: "1d", label: "1 ngày" },
    { value: "7d", label: "7 ngày" },
    { value: "30d", label: "30 ngày" },
  ];
  const rangeEntry = rangeOptions.find((opt) => opt.value === traceFilter.range) || rangeOptions[2];
  return `<div class="trace-pills" role="group" aria-label="Bộ lọc trace">
      ${filterMenu("model", "model", modelOptions, modelLabel)}
      ${filterMenu("status", "trạng thái", statusOptions, statusEntry.label)}
      ${filterMenu("range", "ngày", rangeOptions, rangeEntry.label)}
    </div>`;
}

// ---- daily chart (hand-built SVG from by_day, no library) ----

function fmtDayShort(day) {
  const d = new Date(`${String(day)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return String(day);
  return d.toLocaleDateString("vi-VN", { day: "numeric", month: "short", timeZone: "UTC" });
}

// by_day only has days with turns — fill calendar gaps so a missing day reads as 0, not as a skipped category.
function fillDays(byDay) {
  const days = byDay.map((d) => String(d.day)).filter(Boolean).sort();
  if (!days.length) return [];
  const first = new Date(`${days[0]}T00:00:00Z`);
  const last = new Date(`${days[days.length - 1]}T00:00:00Z`);
  const span = Math.round((last.getTime() - first.getTime()) / 86400000);
  if (Number.isNaN(span) || span > 92) return byDay;
  const map = new Map(byDay.map((d) => [String(d.day), d]));
  const out = [];
  for (let t = first.getTime(); t <= last.getTime(); t += 86400000) {
    const key = new Date(t).toISOString().slice(0, 10);
    out.push(map.get(key) || { day: key, requests: 0, cost_usd: null });
  }
  return out;
}

function hourEntries(byHour) {
  const map = new Map((byHour || []).map((h) => [String(h.hour).padStart(2, "0"), h]));
  const out = [];
  for (let i = 0; i < 24; i += 1) {
    const key = String(i).padStart(2, "0");
    const row = map.get(key);
    out.push({ label: `${key}h`, requests: Number(row && row.requests) || 0, cost: row ? row.cost_usd : null });
  }
  return out;
}

function byDayBlock(byDay, byHour) {
  const hourMode = traceFilter.range === "1d";
  const entries = hourMode
    ? hourEntries(byHour)
    : fillDays(byDay)
        .filter((d) => d && d.day)
        .map((d) => ({ label: fmtDayShort(d.day), requests: Number(d.requests) || 0, cost: d.cost_usd }));
  if (!entries.length) return "";
  const W = 720;
  const H = 150;
  const PAD_B = 24;
  const TOP_PAD = 18;
  const max = Math.max(...entries.map((d) => Number(d.requests) || 0), 1);
  const n = entries.length;
  const slot = W / n;
  const barW = Math.max(2, Math.min(28, slot * 0.62));
  const baseline = H - PAD_B;
  const parts = [`<line class="trace-chart-rule" x1="0" y1="${baseline}" x2="${W}" y2="${baseline}" />`];
  const ticks = [];
  const tickEvery = Math.max(1, Math.ceil(n / 8));
  entries.forEach((d, i) => {
    const req = Number(d.requests) || 0;
    const h = req > 0 ? Math.max(2, Math.round((req / max) * (baseline - TOP_PAD))) : 0;
    const x = i * slot + (slot - barW) / 2;
    const y = baseline - h;
    const cost = Number(d.cost) > 0 ? ` · ${fmtCost(d.cost)}` : "";
    if (h > 0) {
      parts.push(
        `<rect class="trace-chart-bar" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h}" rx="1.5"><title>${escapeHtml(d.label)} · ${fmtInt(req)} lượt${cost}</title></rect>`,
      );
    }
    if (i % tickEvery === 0 || i === n - 1) {
      // label sits on top of its bar — same viewBox space, so % maps 1:1
      const topPct = ((baseline - h - 6) / H) * 100;
      ticks.push(`<span style="--x:${(((i * slot + slot / 2) / W) * 100).toFixed(2)}%;--y:${topPct.toFixed(2)}%">${escapeHtml(d.label)}</span>`);
    }
  });
  const unit = hourMode ? "giờ" : "ngày";
  return `<section class="trace-section">
      <h3>Theo ${unit}</h3>
      <p class="trace-section-note">${fmtInt(n)} ${unit} · cao nhất ${fmtInt(max)} lượt/${unit}</p>
      <div class="trace-chart-wrap">
        <svg class="trace-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Lượt theo ${unit}" preserveAspectRatio="xMidYMid meet">${parts.join("")}</svg>
        <div class="trace-chart-ticks" aria-hidden="true">${ticks.join("")}</div>
      </div>
    </section>`;
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

export async function hydrateTrace() {
  const base = activeParams();
  const [stats, list, pillStats] = await Promise.all([
    fetchTraceStats(base),
    fetchTraces({ ...base, limit: 50 }),
    base.model ? fetchTraceStats({ ...base, model: undefined }) : null,
  ]);
  if (!stats || !list) return;
  applyTracePages(stats, list, pillStats);
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
let filterOutsideBound = false;

function closeFilterPops(scope) {
  scope.querySelectorAll(".trace-filter-pop").forEach((pop) => {
    pop.hidden = true;
  });
  scope.querySelectorAll("[data-filter-toggle]").forEach((btn) => {
    btn.setAttribute("aria-expanded", "false");
  });
}

function bindFilterOutside() {
  if (filterOutsideBound) return;
  filterOutsideBound = true;
  const open = () => document.querySelector(".trace-filter-pop:not([hidden])");
  document.addEventListener("click", (event) => {
    if (!open() || event.target.closest(".trace-filter-menu")) return;
    closeFilterPops(document);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open()) closeFilterPops(document);
  });
}

async function refilterTrace() {
  const base = activeParams();
  const [stats, list, pillStats] = await Promise.all([
    fetchTraceStats(base),
    fetchTraces({ ...base, limit: 50 }),
    base.model ? fetchTraceStats({ ...base, model: undefined }) : null,
  ]);
  if (!stats || !list) return;
  applyTracePages(stats, list, pillStats);
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
      traceFilter = { ...traceFilter, [group]: value };
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

// ---- span bodies (payload only from detail.messages, no new API) ----

const SPAN_TEXT_LIMIT = 4000;

function truncateBody(text) {
  const raw = String(text == null ? "" : text).trim();
  if (!raw) return "";
  return escapeHtml(raw.length <= SPAN_TEXT_LIMIT ? raw : `${raw.slice(0, SPAN_TEXT_LIMIT)} …`);
}

function spanPre(text) {
  const body = truncateBody(text);
  return body ? `<pre>${body}</pre>` : "";
}

function usageField(usage, keys) {
  for (const key of keys) {
    if (usage && usage[key] != null) return Number(usage[key]);
  }
  return null;
}

// English token labels: input/output/cache/cost.
function usageRows(usage) {
  if (!usage || typeof usage !== "object") return [];
  const rows = [];
  const input = usageField(usage, ["prompt_tokens", "input_tokens"]);
  const output = usageField(usage, ["completion_tokens", "output_tokens"]);
  const cached = usageField(usage, ["cached_tokens"]);
  const cost = usageField(usage, ["cost_usd"]);
  if (input != null && Number.isFinite(input)) rows.push(["input", fmtInt(input)]);
  if (output != null && Number.isFinite(output)) rows.push(["output", fmtInt(output)]);
  if (cached != null && Number.isFinite(cached)) rows.push(["cache", fmtInt(cached)]);
  if (cost != null && Number.isFinite(cost)) rows.push(["cost", fmtCost(cost)]);
  return rows;
}

function spanMeta(rows) {
  if (!rows.length) return "";
  const items = rows.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("");
  return `<dl class="trace-span-meta">${items}</dl>`;
}

function publicJson(value) {
  return spanPre(JSON.stringify(stripArgs(value), null, 2));
}

function stripArgs(value) {
  if (Array.isArray(value)) return value.map(stripArgs);
  if (!value || typeof value !== "object") return value;
  const out = { ...value };
  if (Array.isArray(out.tool_calls)) {
    out.tool_calls = out.tool_calls.map((call) => {
      if (!call || typeof call !== "object") return call;
      return { id: call.id, name: call.name };
    });
  }
  return out;
}

function thinkBody(msg) {
  return publicJson(msg);
}

function actBody(tools) {
  return publicJson(tools);
}

function observeBody(msg) {
  return publicJson(msg);
}

function namingBody(msg) {
  return publicJson(msg);
}

// Only spans present in messages: think#n, tools -> act, final text -> observe, meta.kind==naming -> naming.
// Each row is a native <details>; summary keeps label + latency bar, body holds the payload.
function timelineSpans(messages, turnLat) {
  const spans = [];
  let thinkCount = 0;
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") continue;
    if (msg.role === "assistant") {
      const meta = msg.meta || {};
      if (meta.kind === "naming") {
        spans.push({ label: "naming", latency: Number(meta.latency_ms) || 0, body: namingBody(msg) });
        continue;
      }
      thinkCount += 1;
      const hasTools = Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0;
      if (hasTools) {
        spans.push({ label: `think #${thinkCount}`, latency: Number(meta.latency_ms) || 0, body: thinkBody(msg) });
        spans.push({ label: "act", latency: 0, tools: [] });
      } else {
        // assistant text without tool_calls ends the loop -> observe (không đẩy thêm think trùng nội dung)
        spans.push({ label: "observe", latency: Number(meta.latency_ms) || 0, body: observeBody(msg) });
      }
    } else if (msg.role === "tool" && spans.length > 0 && spans[spans.length - 1].label === "act") {
      const act = spans[spans.length - 1];
      act.latency += Number((msg.meta || {}).latency_ms) || 0;
      act.tools.push(msg);
    }
  }
  let elapsed = 0;
  return spans
    .map((span) => {
      const body = span.label === "act" ? actBody(span.tools) : span.body || "";
      const startPct = turnLat > 0 ? Math.min(100, (elapsed / turnLat) * 100) : 0;
      const widthPct =
        turnLat > 0 && span.latency > 0 ? Math.min(100 - startPct, (span.latency / turnLat) * 100) : 0;
      if (span.latency > 0) elapsed += span.latency;
      const time =
        span.latency > 0 ? `<span class="trace-span-time">${escapeHtml(fmtLatency(span.latency))}</span>` : `<span class="trace-span-time"></span>`;
      return `<li class="is-done">
          <details class="trace-span">
            <summary><span class="phase-name">${escapeHtml(span.label)}</span>${time}<div class="track-rule"><span class="trace-span-spacer" style="flex-basis:${startPct}%"></span><span class="trace-span-fill" style="flex-basis:${widthPct}%"></span></div></summary>
            <div class="trace-span-body">${body}</div>
          </details>
        </li>`;
    })
    .join("");
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
