/* Unified Dashboard overview (single screen): entity sidebar (Chung /
   bots / groups) + Chung / bot / group views over one trace snapshot.
   Layout DNA follows the approved compact prototype; colours, fonts and
   radii resolve only through shared tokens — no literal palette here. */
import { getJson } from "../../shared/js/http.js";
import { botMark, botMarkGroup } from "../../shared/js/bot-avatar.js";
import { tintVarFor } from "../chat/team-data.js";
import {
  byName,
  finiteCost,
  rollingRange,
  splitPromptTokens,
  traceRangeUrl,
} from "../../shared/js/analytics-data.js";
import { fetchAllTraces } from "../../shared/js/dashboard-today.js";
import { modelTokens, priceRates, tokenCost } from "./cost-data.js";
import {
  cleanText,
  formatCompact,
  formatCost,
  formatDate,
  formatDuration,
  formatInteger,
  shareLabel,
} from "../../shared/js/format.js";

const el = {
  period: document.querySelector("#overview-period"),
  range: document.querySelector("#overview-range"),
  status: document.querySelector("#overview-status"),
  nav: document.querySelector("#overview-nav"),
  content: document.querySelector("#overview-content"),
  heading: document.querySelector("#overview-heading"),
};
let snapshot = null;
let selection = { type: "all" };
let agentMetric = "token";
let modelMetric = "token";
let generation = 0;

const setStatus = (text) => {
  if (el.status) el.status.textContent = text || "";
};

export function aggregateGroupBots(rows, sessionId) {
  const sid = cleanText(sessionId);
  if (!sid) return [];
  const turns = (Array.isArray(rows) ? rows : [])
    .filter((row) => cleanText(row?.session_id) === sid);
  const byBot = new Map();
  for (const turn of turns) {
    const breakdown = Array.isArray(turn?.bot_breakdown)
      ? turn.bot_breakdown.filter((e) => e && typeof e === "object")
      : [];
    const actor = normBotId(turn?.bot_id);
    const parts = breakdown.length ? breakdown : [turn];
    // Latency is a turn-level span, not a per-bot slice: attribute it only
    // when the bot is the turn's actor (or the only contributor), so the
    // panel never double-counts one turn across several bots.
    const sole = parts.length === 1 ? normBotId(parts[0]?.bot_id ?? actor) : "";
    for (const entry of parts) {
      const key = normBotId(entry?.bot_id ?? actor);
      if (!key) continue;
      const current = byBot.get(key) || {
        botId: key, turns: 0, latencyMs: 0, cost: null, pricedTurns: 0,
      };
      current.turns += 1;
      if (key === actor || key === sole) current.latencyMs += Number(turn?.latency_ms) || 0;
      const price = finiteCost(entry?.cost_usd);
      if (price != null) {
        current.cost = (current.cost ?? 0) + price;
        current.pricedTurns += 1;
      }
      byBot.set(key, current);
    }
  }
  return [...byBot.values()].sort((a, b) => (b.cost ?? -1) - (a.cost ?? -1)
    || a.botId.localeCompare(b.botId, "vi"));
}

/* ---------------- snapshot load ---------------- */

function turnTokens(row) {
  const prompt = Number(row?.prompt_tokens) || 0;
  const completion = Number(row?.completion_tokens) || 0;
  return { prompt, completion, total: prompt + completion };
}

function turnCost(row) {
  const cost = finiteCost(row?.cost_usd);
  return cost == null ? null : cost;
}

export function aggregateOverview(rows) {
  const list = Array.isArray(rows) ? rows : [];
  let requests = 0;
  let toolCalls = 0;
  let prompt = 0;
  let cached = 0;
  let completion = 0;
  let cost = null;
  let pricedTurns = 0;
  for (const row of list) {
    requests += Number(row?.requests) || 0;
    const tools = Number(row?.tool_calls);
    toolCalls += Number.isFinite(tools) && tools > 0 ? tools : 0;
    prompt += Number(row?.prompt_tokens) || 0;
    cached += Number(row?.cached_tokens) || 0;
    completion += Number(row?.completion_tokens) || 0;
    const price = finiteCost(row?.cost_usd);
    if (price != null) {
      cost = (cost ?? 0) + price;
      pricedTurns += 1;
    }
  }
  const split = splitPromptTokens(prompt, cached);
  return {
    requests, toolCalls, turns: list.length,
    prompt, cached, completion, total: prompt + completion,
    input: split.input, cache: split.cache,
    cost, pricedTurns, unpricedTurns: list.length - pricedTurns,
  };
}

function normBotId(value) {
  const id = typeof value === "string" ? value : "";
  return !id || id === "unknown" ? "" : id;
}

export function aggregateByBot(rows) {
  const byBot = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row !== "object") continue;
    const breakdown = Array.isArray(row.bot_breakdown)
      ? row.bot_breakdown.filter((e) => e && typeof e === "object")
      : [];
    const parts = breakdown.length
      ? breakdown.map((e) => ({
          botId: normBotId(e.bot_id),
          requests: e.requests,
          tool_calls: e.tool_calls,
          prompt_tokens: e.prompt_tokens,
          cached_tokens: e.cached_tokens,
          completion_tokens: e.completion_tokens,
          cost_usd: e.cost_usd,
        }))
      : [{
          botId: normBotId(row.bot_id),
          requests: row.requests,
          tool_calls: row.tool_calls,
          prompt_tokens: row.prompt_tokens,
          cached_tokens: row.cached_tokens,
          completion_tokens: row.completion_tokens,
          cost_usd: row.cost_usd,
        }];
    for (const part of parts) {
      const key = part.botId;
      const current = byBot.get(key) || {
        botId: key, turns: 0, requests: 0, toolCalls: 0,
        prompt_tokens: 0, cached_tokens: 0, completion_tokens: 0,
        cost: null, pricedTurns: 0,
      };
      current.turns += 1;
      current.requests += Number(part.requests) || 0;
      const tools = Number(part.tool_calls);
      current.toolCalls += Number.isFinite(tools) && tools > 0 ? tools : 0;
      current.prompt_tokens += Number(part.prompt_tokens) || 0;
      current.cached_tokens += Number(part.cached_tokens) || 0;
      current.completion_tokens += Number(part.completion_tokens) || 0;
      const price = finiteCost(part.cost_usd);
      if (price != null) {
        current.cost = (current.cost ?? 0) + price;
        current.pricedTurns += 1;
      }
      byBot.set(key, current);
    }
  }
  return [...byBot.values()].sort((a, b) => (b.cost ?? -1) - (a.cost ?? -1)
    || a.botId.localeCompare(b.botId, "vi"));
}

export function aggregateGroups(rows, sessionMap) {
  const map = sessionMap instanceof Map
    ? sessionMap
    : new Map(Object.entries(sessionMap || {}));
  const bySession = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    const sid = cleanText(row?.session_id);
    if (!sid) continue;
    const info = map.get(sid) || {};
    const kind = typeof info.kind === "string" && info.kind
      ? info.kind
      : (typeof row.session_kind === "string" && row.session_kind ? row.session_kind : "group");
    if (kind !== "group") continue;
    let group = bySession.get(sid);
    if (!group) {
      const participants = Array.isArray(info.bots) && info.bots.length
        ? info.bots.map(String)
        : (Array.isArray(row.session_bots) ? row.session_bots.map(String) : []);
      group = {
        sessionId: sid,
        title: cleanText(info.title) || cleanText(row?.title, sid),
        participants,
        turns: 0, latencyMs: 0, cost: null, pricedTurns: 0, unpricedTurns: 0,
        startedAt: "", endedAt: "",
      };
      bySession.set(sid, group);
    }
    group.turns += 1;
    group.latencyMs += Number(row?.latency_ms) || 0;
    const price = finiteCost(row?.cost_usd);
    if (price != null) {
      group.cost = (group.cost ?? 0) + price;
      group.pricedTurns += 1;
    } else {
      group.unpricedTurns += 1;
    }
    if (String(row?.started_at || "") < group.startedAt || !group.startedAt) {
      group.startedAt = String(row?.started_at || "");
    }
    if (String(row?.ended_at || "") > group.endedAt) group.endedAt = String(row?.ended_at || "");
    if (!group.title) group.title = cleanText(row?.title, sid);
  }
  return [...bySession.values()].sort((a, b) =>
    String(b.startedAt).localeCompare(String(a.startedAt))
    || a.sessionId.localeCompare(b.sessionId));
}

export function aggregateModels(rows) {
  const byModel = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    const name = cleanText(row?.model) || "unknown";
    const current = byModel.get(name) || {
      model: name, turns: 0, requests: 0, toolCalls: 0,
      prompt_tokens: 0, cached_tokens: 0, completion_tokens: 0,
      cost: null, pricedTurns: 0,
    };
    current.turns += 1;
    current.requests += Number(row?.requests) || 0;
    const tools = Number(row?.tool_calls);
    current.toolCalls += Number.isFinite(tools) && tools > 0 ? tools : 0;
    current.prompt_tokens += Number(row?.prompt_tokens) || 0;
    current.cached_tokens += Number(row?.cached_tokens) || 0;
    current.completion_tokens += Number(row?.completion_tokens) || 0;
    const price = finiteCost(row?.cost_usd);
    if (price != null) {
      current.cost = (current.cost ?? 0) + price;
      current.pricedTurns += 1;
    }
    byModel.set(name, current);
  }
  return [...byModel.values()].sort(
    (a, b) => modelTokens(b) - modelTokens(a) || byName(a, b));
}

function turnBotIds(row) {
  const ids = new Set();
  const breakdown = Array.isArray(row?.bot_breakdown) ? row.bot_breakdown : [];
  for (const entry of breakdown) {
    if (entry && typeof entry === "object" && normBotId(entry.bot_id)) {
      ids.add(normBotId(entry.bot_id));
    }
  }
  if (normBotId(row?.bot_id)) ids.add(normBotId(row.bot_id));
  return [...ids];
}

function turnBotSlice(row, botId) {
  const breakdown = Array.isArray(row?.bot_breakdown) ? row.bot_breakdown : [];
  const entry = breakdown.find((e) => e && normBotId(e?.bot_id) === botId);
  if (entry && typeof entry === "object") {
    return {
      requests: Number(entry.requests) || 0,
      toolCalls: Number(entry.tool_calls) || 0,
      prompt: Number(entry.prompt_tokens) || 0,
      cached: Number(entry.cached_tokens) || 0,
      completion: Number(entry.completion_tokens) || 0,
      cost: finiteCost(entry.cost_usd),
      latency: Number(row?.latency_ms) || 0,
    };
  }
  return {
    requests: Number(row?.requests) || 0,
    toolCalls: Number(row?.tool_calls) || 0,
    prompt: Number(row?.prompt_tokens) || 0,
    cached: Number(row?.cached_tokens) || 0,
    completion: Number(row?.completion_tokens) || 0,
    cost: finiteCost(row?.cost_usd),
    latency: Number(row?.latency_ms) || 0,
  };
}

export function timelineForBot(rows, botId, limit = 20) {
  const id = normBotId(botId);
  if (!id) return [];
  return (Array.isArray(rows) ? rows : [])
    .filter((row) => turnBotIds(row).includes(id))
    .slice()
    .sort((a, b) => String(b.started_at || "").localeCompare(String(a.started_at || "")))
    .slice(0, Math.max(0, limit))
    .map((row) => ({
      sessionId: cleanText(row?.session_id),
      turnIndex: row?.turn_index,
      startedAt: String(row?.started_at || ""),
      title: cleanText(row?.title),
      model: cleanText(row?.model),
      status: cleanText(row?.status),
      slice: turnBotSlice(row, id),
    }));
}

export function timelineForGroup(rows, sessionId) {
  const sid = cleanText(sessionId);
  if (!sid) return [];
  return (Array.isArray(rows) ? rows : [])
    .filter((row) => cleanText(row?.session_id) === sid)
    .slice()
    .sort((a, b) => String(b.started_at || "").localeCompare(String(a.started_at || "")));
}

/* ---------------- render ---------------- */

function esc(value) {
  return cleanText(value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function botTintStyle(botId, roster) {
  if (!botId) return "";
  const bot = roster instanceof Map ? roster.get(botId) : null;
  const tint = tintVarFor(botId);
  return `--team-tint:${tint};--avatar-tint:${tint}`;
}

function sealFor(botId, roster) {
  if (!botId) return `<span class="bot-avatar-slot is-empty" aria-hidden="true"></span>`;
  const bot = (roster && roster.get(botId)) || { id: botId };
  const style = botTintStyle(botId, roster);
  return `<span class="bot-avatar-slot"${style ? ` style="${style}"` : ""} aria-hidden="true">${botMark(bot, { state: "idle" })}</span>`;
}

function sealsFor(members, roster) {
  const list = (members || []).map((id) => (roster && roster.get(id)) || { id });
  const first = list.length ? String(list[0]?.id || "") : "";
  const style = botTintStyle(first, roster);
  return `<span class="bot-avatar-slot is-stack"${style ? ` style="${style}"` : ""} aria-hidden="true">${botMarkGroup(list, { state: "idle" })}</span>`;
}

export function badgeFor(group) {
  if (group.unpricedTurns > 0) {
    const note = group.unpricedTurns === 1 ? "1 lượt chưa định giá" : `${group.unpricedTurns} lượt chưa định giá`;
    return ` · <span class="badge part">một phần — ${note}</span>`;
  }
  return "";
}

function costText(cost) {
  return cost == null ? "—" : formatCost(cost);
}

function groupHeading(group, roster) {
  if (cleanText(group.title) && group.title !== group.sessionId) return group.title;
  const names = group.participants.map((id) => rosterName(id, roster));
  return names.length ? names.join(" + ") : group.sessionId;
}

/* Token split of one bot grouped by the model that produced it, so the
   cost formula can price each model with its own configured rates and
   the model chart stacks the same three sides per model. */
export function botModelTokens(rows, botId) {
  const byModel = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!turnBotIds(row).includes(botId)) continue;
    const slice = turnBotSlice(row, botId);
    const split = splitPromptTokens(slice.prompt, slice.cached);
    const model = cleanText(row?.model) || "unknown";
    const current = byModel.get(model) || {
      model, input: 0, cache: 0, output: 0, requests: 0, toolCalls: 0, cost: null,
    };
    current.input += split.input;
    current.cache += split.cache;
    current.output += slice.completion;
    current.requests += slice.requests;
    current.toolCalls += slice.toolCalls;
    if (slice.cost != null) current.cost = (current.cost ?? 0) + slice.cost;
    byModel.set(model, current);
  }
  return [...byModel.values()].sort((a, b) =>
    (b.input + b.cache + b.output) - (a.input + a.cache + a.output)
    || a.model.localeCompare(b.model));
}

/* Model groups for the formula and the model chart: the per-model split
   of the bot's turns, or — for snapshots without rows — one unnamed
   group from the bot aggregate. */
function botModelGroups(snap, botId, entry) {
  const groups = botModelTokens(snap?.rows, botId);
  if (!groups.length && entry) {
    const split = splitPromptTokens(entry.prompt_tokens, entry.cached_tokens);
    groups.push({
      model: "", input: split.input, cache: split.cache,
      output: Number(entry.completion_tokens) || 0,
      requests: Number(entry.requests) || 0,
      toolCalls: Number(entry.toolCalls) || 0,
      cost: entry.cost ?? null,
    });
  }
  return groups;
}

/* Stacked token bar per model (the diagram chosen over the old day
   columns: no empty days, no dwarfed scale, no UTC day shift). Each bar
   stacks Vào/Cache/Ra as shares of the bot's own token total, in the
   same reading as the Theo bot token-stack bars. */
function botModelChartHtml(snap, botId, entry, name) {
  const groups = botModelGroups(snap, botId, entry);
  if (groups.length < 2) return "";
  const total = groups.reduce((sum, g) => sum + g.input + g.cache + g.output, 0);
  const roster = snap?.roster;
  const tint = botTintStyle(botId, roster);
  let html = `<section class="panel bot-chart" aria-label="Token theo mô hình của ${esc(name)}"${tint ? ` style="${tint}"` : ""}>`
    + `<h3>Token theo mô hình</h3>`
    + `<div class="ph bot-chart-legend"><span class="lg-in">Vào</span><span class="lg-cache">Cache</span><span class="lg-out">Ra</span></div>`;
  for (const group of groups) {
    const tokens = group.input + group.cache + group.output;
    html += `<div class="bot-model-row">`
      + `<span class="bot-model-head"><span class="name mn">${esc(group.model || "unknown")}</span>`
      + `<span class="bot-model-value">${formatCompact(tokens)} token<small>${shareLabel(tokens, total)}</small></span></span>`
      + `<span class="token-stack" role="img" aria-label="${shareLabel(tokens, total)} token của bot">`
      + `<i class="seg-in" style="width:${(total > 0 ? group.input / total : 0) * 100}%"></i>`
      + `<i class="seg-cache" style="width:${(total > 0 ? group.cache / total : 0) * 100}%"></i>`
      + `<i class="seg-out" style="width:${(total > 0 ? group.output / total : 0) * 100}%"></i></span>`
      + `</div>`;
  }
  return `${html}</section>`;
}

function rateText(rate) {
  return rate == null ? "—" : `$${Number(rate).toLocaleString("en-US", { maximumFractionDigits: 6 })}/1M`;
}

/* "Cách tính" frame for the bot header: one small boxed block and
   nothing floating above it. Each model opens with a header line
   (model · its tokens · its requests · TB ghi nhận = its recorded cost
   ÷ its requests), then a compact table of Vào/Cache/Ra estimate rows
   (tokens × current configured rate). The recorded bot total sits in a
   separate footer below the estimates, with its share of the period
   cost — estimates never pose as the recorded figure. Without
   configured rates a row shows just its token count and a dash. */
function costFormulaHtml(snap, botId, entry, cost, requests) {
  const groups = botModelGroups(snap, botId, entry);
  let html = `<div class="bot-cost" aria-label="Cách tính chi phí">`
    + `<p class="bot-cost-heading">Cách tính</p>`;
  for (const group of groups) {
    const rates = group.model ? priceRates(snap?.config, group.model) : null;
    const groupTokens = group.input + group.cache + group.output;
    const groupAverage = group.cost != null && group.requests > 0
      ? `${formatCost(group.cost / group.requests)}/request` : "—";
    html += `<p class="bot-cost-model">`
      + `<span class="bot-cost-model-name">${esc(group.model || "Chưa rõ mô hình")}${rates ? " · ước tính theo đơn giá hiện tại" : ""}</span>`
      + `<span class="bot-cost-model-meta">${formatCompact(groupTokens)} token · ${formatInteger(group.requests)} request`
      + `${group.toolCalls ? ` · ${formatInteger(group.toolCalls)} tool` : ""} · TB ghi nhận ${groupAverage}</span></p>`;
    html += `<table class="bot-cost-table"><thead><tr>`
      + `<th class="bot-cost-label">Loại</th>`
      + `<th class="bot-cost-expr">Token × đơn giá</th>`
      + `<th class="bot-cost-amount">Thành tiền</th></tr></thead><tbody>`;
    const parts = [
      ["Vào", group.input, rates?.input],
      ["Cache", group.cache, rates?.cache],
      ["Ra", group.output, rates?.output],
    ];
    for (const [label, tokens, rate] of parts) {
      html += rate == null
        ? `<tr><td class="bot-cost-label">${label}</td><td class="bot-cost-expr">${formatInteger(tokens)} token</td><td class="bot-cost-amount">—</td></tr>`
        : `<tr><td class="bot-cost-label">${label}</td><td class="bot-cost-expr">${formatInteger(tokens)} × ${rateText(rate)}</td><td class="bot-cost-amount">${costText(tokenCost(tokens, rate))}</td></tr>`;
    }
    html += `</tbody></table>`;
  }
  const costShare = shareLabel(cost, snap?.totals?.cost);
  html += `<div class="bot-cost-recorded">`
    + `<span class="bot-cost-recorded-caption">Đã ghi nhận</span>`
    + `<span class="bot-cost-recorded-value"><strong>${costText(cost)}</strong>`
    + (costShare === "—" ? "" : ` <small class="bot-cost-share">${costShare} tổng chi phí</small>`)
    + `</span></div>`
    + `</div>`;
  return html;
}

function rosterName(botId, roster) {
  if (!botId) return "Chưa gắn bot";
  const bot = roster && roster.get(botId);
  return cleanText(bot?.name) || botId;
}

function openTrace(sessionId, turnIndex) {
  const params = new URLSearchParams();
  params.set("session", sessionId);
  if (turnIndex != null && String(turnIndex) !== "") params.set("turn", String(turnIndex));
  location.assign(`${location.pathname}?${params}#trace`);
}

function summaryCell(label, valueHtml, hint) {
  return `<div class="cell"><div class="label"><span class="dot"></span>${esc(label)}</div>`
    + `<div class="value">${valueHtml}</div>${hint ? `<div class="hint">${esc(hint)}</div>` : ""}</div>`;
}

export function renderChung(snap) {
  const { totals } = snap;
  const avg = totals.cost != null && totals.requests > 0
    ? `TB ${formatCost(totals.cost / totals.requests)} / request` : "chưa định giá";
  let html = `<section class="summary" aria-label="Tổng quan">`
    + summaryCell("Token",
        `<span>${formatCompact(totals.total)}</span>`,
        `vào ${formatCompact(totals.input)} · cache ${formatCompact(totals.cache)} · ra ${formatCompact(totals.completion)}`)
    + summaryCell("Request", `<span>${formatInteger(totals.requests)}</span>`)
    + summaryCell("Gọi tool", `<span>${formatInteger(totals.toolCalls)}</span>`)
    + summaryCell("Chi phí", `<span class="hero">${totals.cost == null ? "—" : formatCost(totals.cost)}</span>`, avg)
    + `</section>`;
  html += renderAgentChart(snap);
  html += renderModelChart(snap);
  return html;
}

export const AGENT_METRICS = ["token", "cost", "requests", "tools"];

const METRIC_LABELS = { token: "Token", cost: "Chi phí", requests: "Request", tools: "Gọi tool" };

export function setAgentMetric(metric) {
  if (AGENT_METRICS.includes(metric)) agentMetric = metric;
  return agentMetric;
}

export function setModelMetric(metric) {
  if (AGENT_METRICS.includes(metric)) modelMetric = metric;
  return modelMetric;
}

export function renderAgentChart(snap, metric = agentMetric) {
  const { totals, byBot, roster } = snap;
  const mode = AGENT_METRICS.includes(metric) ? metric : "token";
  const valueOf = (entry) => {
    if (mode === "cost") return Number(entry.cost) || 0;
    if (mode === "requests") return entry.requests;
    if (mode === "tools") return entry.toolCalls || 0;
    return entry.prompt_tokens + entry.completion_tokens;
  };
  const totalOf = mode === "cost" ? totals.cost || 0
    : mode === "requests" ? totals.requests
    : mode === "tools" ? totals.toolCalls
    : totals.total;
  const rows = [...byBot].sort((a, b) => valueOf(b) - valueOf(a)
    || rosterName(a.botId, roster).localeCompare(rosterName(b.botId, roster), "vi"));
  const leader = rows.length ? valueOf(rows[0]) : 0;
  let html = `<section class="panel" aria-label="Theo bot"><h3>Theo bot</h3>`
    + `<div class="ph">Phần riêng từng bot · Token: vào · cache · ra</div>`
    + `<div class="screen-filters agent-metrics" role="group" aria-label="Chỉ số xếp hạng">`;
  for (const key of AGENT_METRICS) {
    html += `<button class="screen-button" type="button" data-metric="${key}" aria-pressed="${key === mode ? "true" : "false"}">${METRIC_LABELS[key]}</button>`;
  }
  html += `</div><div class="agent-chart">`;
  rows.forEach((entry, index) => {
    const value = valueOf(entry);
    const share = totalOf > 0 ? value / totalOf : 0;
    const tokens = entry.prompt_tokens + entry.completion_tokens;
    const split = splitPromptTokens(entry.prompt_tokens, entry.cached_tokens);
    const isTop = leader > 0 && value === leader;
    const shown = mode === "cost" ? costText(entry.cost)
      : mode === "token" ? `${formatCompact(tokens)} token`
      : formatInteger(value);
    const meta = mode === "token"
      ? `In ${formatCompact(split.input)} · Cache ${formatCompact(split.cache)} · Out ${formatCompact(entry.completion_tokens)} · ${formatInteger(entry.requests)} request · ${formatInteger(entry.toolCalls || 0)} gọi tool`
      : mode === "cost"
        ? `${formatInteger(entry.requests)} request · ${formatInteger(entry.toolCalls || 0)} gọi tool · ${formatCompact(tokens)} token`
        : mode === "requests"
          ? `${formatCompact(tokens)} token · ${formatInteger(entry.toolCalls || 0)} gọi tool · ${costText(entry.cost)}`
          : `${formatInteger(entry.requests)} request · ${formatCompact(tokens)} token · ${costText(entry.cost)}`;
    // Token mode stacks the agent's own Input/Cache/Output shares of the
    // total; every other mode is one solid bar in the bot's own tint.
    const bar = mode === "token"
      ? `<span class="token-stack" role="img" aria-label="${shareLabel(tokens, totals.total)} tổng token">`
        + `<i class="seg-in" style="width:${(totals.total > 0 ? split.input / totals.total : 0) * 100}%"></i>`
        + `<i class="seg-cache" style="width:${(totals.total > 0 ? split.cache / totals.total : 0) * 100}%"></i>`
        + `<i class="seg-out" style="width:${(totals.total > 0 ? entry.completion_tokens / totals.total : 0) * 100}%"></i></span>`
      : `<span class="metric-bar" role="img" aria-label="${shareLabel(value, totalOf)} tổng ${METRIC_LABELS[mode].toLowerCase()}"><i style="width:${Math.max(0, Math.min(100, share * 100))}%"></i></span>`;
    const tint = botTintStyle(entry.botId, roster);
    html += `<div class="agent-row"${tint ? ` style="${tint}"` : ""}>`
      + `<span class="rank">${index + 1}</span>${sealFor(entry.botId, roster)}`
      + `<span class="agent-main"><span class="name">${esc(rosterName(entry.botId, roster))}</span>`
      + (isTop ? `<span class="badge top">cao nhất</span>` : "")
      + `<span class="meta">${meta}</span>${bar}</span>`
      + `<span class="agent-value">${shown}<small>${shareLabel(value, totalOf)}</small></span></div>`;
  });
  return `${html}</div></section>`;
}

/* Top mô hình: same ranked-row table as Theo bot — rank + name + stacked
   token bar + value + the 4-way metric switch — minus the avatar column
   (a model has no seal). The leader is badged; an unpriced model still
   ranks, only its cost share stays "—". */
export function renderModelChart(snap, metric = modelMetric) {
  const { totals, models } = snap;
  const mode = AGENT_METRICS.includes(metric) ? metric : "token";
  const valueOf = (entry) => {
    if (mode === "cost") return Number(entry.cost) || 0;
    if (mode === "requests") return entry.requests;
    if (mode === "tools") return entry.toolCalls || 0;
    return modelTokens(entry);
  };
  const totalOf = mode === "cost" ? totals.cost || 0
    : mode === "requests" ? totals.requests
    : mode === "tools" ? totals.toolCalls
    : totals.total;
  const rows = [...(Array.isArray(models) ? models : [])].sort(
    (a, b) => valueOf(b) - valueOf(a) || byName(a, b));
  const leader = rows.length ? valueOf(rows[0]) : 0;
  let html = `<section class="panel" aria-label="Top mô hình"><h3>Top mô hình</h3>`
    + `<div class="ph">Token: vào · cache · ra</div>`
    + `<div class="screen-filters agent-metrics" role="group" aria-label="Chỉ số xếp hạng mô hình">`;
  for (const key of AGENT_METRICS) {
    html += `<button class="screen-button" type="button" data-model-metric="${key}" aria-pressed="${key === mode ? "true" : "false"}">${METRIC_LABELS[key]}</button>`;
  }
  html += `</div><div class="agent-chart">`;
  if (!rows.length) {
    html += `<div class="footnote">Chưa có lượt nào trong kỳ.</div>`;
  }
  rows.forEach((model, index) => {
    const value = valueOf(model);
    const share = totalOf > 0 ? value / totalOf : 0;
    const tokens = modelTokens(model);
    const split = splitPromptTokens(model.prompt_tokens, model.cached_tokens);
    const completion = Number(model.completion_tokens) || 0;
    const requests = Number(model.requests) || 0;
    const tools = Number(model.toolCalls) || 0;
    const turns = Number(model.turns) || 0;
    const turnsLabel = turns === 1 ? "1 lượt" : `${formatInteger(turns)} lượt`;
    const isTop = leader > 0 && value === leader;
    const shown = mode === "cost" ? costText(model.cost)
      : mode === "token" ? `${formatCompact(tokens)} token`
      : formatInteger(value);
    const meta = mode === "token"
      ? `${turnsLabel} · In ${formatCompact(split.input)} · Cache ${formatCompact(split.cache)} · Out ${formatCompact(completion)} · ${formatInteger(requests)} request`
      : mode === "cost"
        ? `${turnsLabel} · ${formatInteger(requests)} request · ${formatInteger(tools)} gọi tool · ${formatCompact(tokens)} token`
        : mode === "requests"
          ? `${turnsLabel} · ${formatCompact(tokens)} token · ${formatInteger(tools)} gọi tool · ${costText(model.cost)}`
          : `${turnsLabel} · ${formatInteger(requests)} request · ${formatCompact(tokens)} token · ${costText(model.cost)}`;
    const bar = mode === "token"
      ? `<span class="token-stack" role="img" aria-label="${shareLabel(tokens, totals.total)} tổng token">`
        + `<i class="seg-in" style="width:${(totals.total > 0 ? split.input / totals.total : 0) * 100}%"></i>`
        + `<i class="seg-cache" style="width:${(totals.total > 0 ? split.cache / totals.total : 0) * 100}%"></i>`
        + `<i class="seg-out" style="width:${(totals.total > 0 ? completion / totals.total : 0) * 100}%"></i></span>`
      : `<span class="metric-bar" role="img" aria-label="${shareLabel(value, totalOf)} tổng ${METRIC_LABELS[mode].toLowerCase()}"><i style="width:${Math.max(0, Math.min(100, share * 100))}%"></i></span>`;
    html += `<div class="agent-row model">`
      + `<span class="rank">${index + 1}</span>`
      + `<span class="agent-main"><span class="name mn">${esc(model.model)}</span>`
      + (isTop ? `<span class="badge top">cao nhất</span>` : "")
      + `<span class="meta">${meta}</span>${bar}</span>`
      + `<span class="agent-value">${shown}<small>${shareLabel(value, totalOf)}</small></span></div>`;
  });
  return `${html}</div></section>`;
}

export function renderBot(snap, botId) {
  const { totals, byBot, rows, roster } = snap;
  const entry = byBot.find((e) => e.botId === botId);
  const name = rosterName(botId, roster);
  const requests = entry?.requests || 0;
  const toolCalls = entry?.toolCalls || 0;
  const prompt = entry?.prompt_tokens || 0;
  const completion = entry?.completion_tokens || 0;
  const cost = entry?.cost ?? null;
  const tokens = prompt + completion;
  // The bot name lives in the page header (#overview-heading, set by
  // render()); the view opens straight into the Cách tính frame, which
  // carries the token/request/average summary in its model header.
  let html = costFormulaHtml(snap, botId, entry, cost, requests)
    + `<details class="bot-overview-details">`
    + `<summary>Tỷ trọng</summary>`
    + `<p class="bot-overview-detail-line">${shareLabel(tokens, totals.total)} tổng token`
    + ` · ${shareLabel(requests, totals.requests)} tổng request`
    + ` · ${shareLabel(toolCalls, totals.toolCalls)} tổng gọi tool</p>`
    + `<p class="bot-overview-note">Chỉ tính phần riêng của bot.</p>`
    + `</details>`;
  html += botModelChartHtml(snap, botId, entry, name);
  html += `<section class="panel trace" aria-label="Trace của ${esc(name)}"><h3>Trace</h3>`
    + `<div class="ph">20 lượt gần nhất</div>`;
  const timeline = timelineForBot(rows, botId, 20);
  if (!timeline.length) {
    html += `<div class="footnote">Chưa có lượt trong kỳ.</div>`;
  }
  for (const turn of timeline) {
    const slice = turn.slice;
    const sliceTokens = slice.prompt + slice.completion;
    const title = `${cleanText(turn.title) ? `${turn.title} · ` : ""}${turn.model || "unknown"} · lượt ${turn.turnIndex ?? "?"}`;
    const ok = !turn.status || turn.status === "ok";
    html += `<button type="button" class="trow" data-session="${esc(turn.sessionId)}" data-turn="${turn.turnIndex ?? ""}">`
      + `<span class="ttime">${esc(formatDate(turn.startedAt))}</span>`
      + sealFor(botId, roster)
      + `<span><span class="tact">${esc(title)}</span>`
      + `<span class="tmeta"><span class="sdot${ok ? "" : " part"}"></span>${ok ? "Thành công" : esc(turn.status)}</span></span>`
      + `<span class="tval" title="Token phần riêng của bot">${formatDuration(slice.latency)} · ${formatCompact(sliceTokens)} token<small>${costText(slice.cost)}</small></span></button>`;
  }
  html += `</section>`;
  return html;
}

function renderGroup(snap, sessionId) {
  const { rows, roster, sessionMap } = snap;
  const group = snap.groups.find((g) => g.sessionId === sessionId);
  const turns = timelineForGroup(rows, sessionId);
  const participants = group?.participants || [];
  let html = "";
  if (group) {
    html += `<section class="summary" aria-label="Tổng nhóm">`
      + summaryCell("Lượt", `<span>${formatInteger(group.turns)}</span>`)
      + summaryCell("Tổng thời gian", `<span>${formatDuration(group.latencyMs)}</span>`)
      + summaryCell("Chi phí", `<span class="hero">${costText(group.cost)}</span>`, group.unpricedTurns > 0 ? "một phần — có lượt chưa định giá" : "đã ghi nhận")
      + `</section>`;
    const parts = aggregateGroupBots(rows, sessionId);
    html += `<section class="panel" aria-label="Phần từng bot trong nhóm"><h3>Phần từng bot</h3>`;
    for (const part of parts) {
      html += `<div class="brow">${sealFor(part.botId, roster)}<div>`
        + `<span class="name">${esc(rosterName(part.botId, roster))}</span>`
        + `<div class="meta">${formatInteger(part.turns)} lượt tham gia · ${formatDuration(part.latencyMs)}</div>`
        + `</div><div class="amt">${costText(part.cost)}</div></div>`;
    }
  } else {
    html += `<div class="panel"><div class="footnote">Không tìm thấy phiên trong kỳ này.</div></div>`;
  }
  html += `<section class="panel trace" aria-label="Trace của nhóm"><h3>Trace</h3>`;
  for (const turn of turns) {
    const tokens = turnTokens(turn);
    const actor = normBotId(turn?.bot_id) || turnBotIds(turn)[0] || "";
    const title = `${cleanText(turn.title) ? `${turn.title} · ` : ""}${cleanText(turn.model) || "unknown"} · lượt ${turn.turn_index ?? "?"}`;
    const ok = !turn.status || turn.status === "ok";
    html += `<button type="button" class="trow" data-session="${esc(turn.session_id)}" data-turn="${turn.turn_index ?? ""}">`
      + `<span class="ttime">${esc(formatDate(turn.started_at))}</span>`
      + sealFor(actor, roster)
      + `<span><span class="tact">${esc(title)}<span class="mini-seals">${sealsFor(participants, roster)}</span></span>`
      + `<span class="tmeta"><span class="sdot${ok ? "" : " part"}"></span>${ok ? "Thành công" : esc(turn.status)}</span></span>`
      + `<span class="tval">${formatDuration(Number(turn.latency_ms) || 0)} · ${formatCompact(tokens.total)} token<small>${costText(turnCost(turn))}</small></span></button>`;
  }
  html += `</section>`;
  return html;
}

export function entityBotList(rosterList, byBot) {
  const list = Array.isArray(rosterList) ? rosterList : [];
  const aggregates = Array.isArray(byBot) ? byBot : [];
  const rosterIds = new Set(list.map((bot) => bot?.id));
  return [
    ...list,
    ...aggregates
      .filter((entry) => entry?.botId && !rosterIds.has(entry.botId))
      .map((entry) => ({ id: entry.botId, name: "" })),
  ];
}

function renderNav(snap) {
  const { roster, rosterList, byBot, groups } = snap;
  const costOf = (id) => byBot.find((e) => e.botId === id);
  let html = `<button type="button" class="srow${selection.type === "all" ? " active" : ""}" data-entity="all">`
    + `<span class="bot-avatar-slot" aria-hidden="true">${botMarkGroup(rosterList.slice(0, 3), { state: "idle" })}</span>`
    + `<span><b>Chung</b></span></button>`
    + `<div class="side-label">Theo bot</div>`;
  // Entity bots = current roster order first, then history bots that only
  // appear in this snapshot's aggregates (cost-desc aggregate order), so a
  // bot missing from /api/bots still gets a row. The "" bucket is
  // overview-only (disabled row below), never an entity.
  const entityBots = entityBotList(rosterList, byBot);
  for (const bot of entityBots) {
    const entry = costOf(bot.id);
    const meta = entry
      ? `${costText(entry.cost)} · ${formatInteger(entry.requests)} lần gọi`
      : "Chưa có dữ liệu";
    const tint = botTintStyle(bot.id, roster);
    html += `<button type="button" class="srow${selection.type === "bot" && selection.id === bot.id ? " active" : ""}" data-entity="bot:${esc(bot.id)}"${tint ? ` style="${tint}"` : ""}>`
      + `<span class="bot-avatar-slot" aria-hidden="true">${botMark(bot, { state: "idle" })}</span>`
      + `<span><b>${esc(cleanText(bot.name) || bot.id)}</b><span class="meta">${meta}</span></span></button>`;
  }
  const unknownEntry = byBot.find((e) => !e.botId);
  if (unknownEntry) {
    html += `<button type="button" class="srow" data-entity="bot:" disabled>`
      + `<span class="bot-avatar-slot is-empty" aria-hidden="true"></span>`
      + `<span><b>Chưa gắn bot</b><span class="meta">${costText(unknownEntry.cost)} · ${formatInteger(unknownEntry.requests)} lần gọi · chỉ ở Tổng quan</span></span></button>`;
  }
  html += `<div class="side-label">Nhóm</div>`;
  for (const group of groups) {
    const turnsLabel = group.turns === 1 ? "1 lượt" : `${group.turns} lượt`;
    html += `<button type="button" class="srow${selection.type === "group" && selection.id === group.sessionId ? " active" : ""}" data-entity="group:${esc(group.sessionId)}">`
      + sealsFor(group.participants, roster)
      + `<span><b>${esc(groupHeading(group, roster))}</b><span class="meta">${turnsLabel} · ${costText(group.cost)}</span></span></button>`;
  }
  if (!groups.length) {
    html += `<p class="side-empty">Chưa có nhóm trong kỳ.</p>`;
  }
  return html;
}

function render() {
  if (!snapshot || !el.content || !el.nav) return;
  el.nav.innerHTML = renderNav(snapshot);
  if (el.heading) {
    // The page header names the current entity, like the other screens:
    // the bot for a bot view, the group for a group view, else Tổng quan.
    el.heading.textContent = selection.type === "bot"
      ? rosterName(selection.id, snapshot.roster)
      : selection.type === "group"
        ? (() => {
            const group = snapshot.groups.find((g) => g.sessionId === selection.id);
            return group ? groupHeading(group, snapshot.roster) : selection.id;
          })()
        : "Tổng quan";
  }
  if (selection.type === "bot") el.content.innerHTML = renderBot(snapshot, selection.id);
  else if (selection.type === "group") el.content.innerHTML = renderGroup(snapshot, selection.id);
  else el.content.innerHTML = renderChung(snapshot);
}

function bindClicks() {
  if (el.nav && !el.nav.dataset.bound) {
    el.nav.dataset.bound = "1";
    el.nav.addEventListener("click", (event) => {
      const button = event.target.closest("[data-entity]");
      if (!button || button.disabled) return;
      const [type, ...rest] = button.dataset.entity.split(":");
      const id = rest.join(":");
      selection = type === "all" ? { type: "all" } : { type, id };
      render();
      document.querySelector(".dashboard-shell")?.classList.remove("is-picking");
    });
  }
  if (el.content && !el.content.dataset.bound) {
    el.content.dataset.bound = "1";
    el.content.addEventListener("click", (event) => {
      const metricButton = event.target.closest("[data-metric]");
      if (metricButton) {
        setAgentMetric(metricButton.dataset.metric);
        render();
        return;
      }
      const modelMetricButton = event.target.closest("[data-model-metric]");
      if (modelMetricButton) {
        setModelMetric(modelMetricButton.dataset.modelMetric);
        render();
        return;
      }
      const row = event.target.closest("[data-session]");
      if (!row) return;
      const sessionId = row.dataset.session;
      if (!sessionId) return;
      openTrace(sessionId, row.dataset.turn || undefined);
    });
  }
}

/* ---------------- snapshot load ---------------- */

function rosterFrom(payload) {
  const list = Array.isArray(payload) ? payload : payload?.bots;
  const entries = (Array.isArray(list) ? list : [])
    .filter((bot) => bot && typeof bot === "object")
    .map((bot) => ({
      id: String(bot.id || ""),
      name: cleanText(bot.name) || String(bot.id || ""),
      system: typeof bot.system === "string" ? bot.system : "",
    }))
    .filter((bot) => bot.id);
  const seen = new Set();
  const rosterList = entries.filter((bot) => (seen.has(bot.id) ? false : (seen.add(bot.id), true)));
  return { rosterList, roster: new Map(rosterList.map((bot) => [bot.id, bot])) };
}

function sessionMapFrom(payload) {
  const list = Array.isArray(payload) ? payload : payload?.sessions;
  const map = new Map();
  for (const session of Array.isArray(list) ? list : []) {
    if (!session || typeof session !== "object") continue;
    const id = cleanText(session.id);
    if (!id) continue;
    map.set(id, {
      kind: typeof session.kind === "string" ? session.kind : "",
      dm: cleanText(session.dm),
      bots: Array.isArray(session.bots) ? session.bots.map(String) : [],
      title: cleanText(session.title),
    });
  }
  return map;
}

async function loadSnapshot(days, myGeneration) {
  const range = rollingRange(days);
  const tracesUrl = traceRangeUrl("/api/traces", days, 200);
  const statsUrl = `/api/traces/stats?from=${range.from}&to=${range.to}`;
  const [traces, stats, botsPayload, sessionsPayload, configPayload] = await Promise.all([
    fetchAllTraces((offset) => getJson(`${tracesUrl}&offset=${offset}`)),
    getJson(statsUrl).catch(() => null),
    getJson("/api/bots").catch(() => null),
    getJson("/api/sessions").catch(() => null),
    getJson("/api/config").catch(() => null),
  ]);
  if (myGeneration !== generation) return;
  const rows = Array.isArray(traces) ? traces : [];
  const { rosterList, roster } = rosterFrom(botsPayload);
  const sessionMap = sessionMapFrom(sessionsPayload);
  const totals = aggregateOverview(rows);
  if (stats && typeof stats === "object" && totals.cost == null) {
    const hero = finiteCost(stats?.totals?.cost_usd ?? stats?.cost_usd);
    if (hero != null) totals.cost = hero;
  }
  snapshot = {
    rows, rosterList, roster, sessionMap, totals,
    config: configPayload?.values || null,
    byBot: aggregateByBot(rows),
    groups: aggregateGroups(rows, sessionMap),
    models: aggregateModels(rows),
    days,
  };
  if (el.range) {
    el.range.innerHTML = `<b>${esc(formatDate(range.from))} – ${esc(formatDate(range.to))}</b>`;
  }
  setStatus("");
  render();
}

async function boot() {
  if (!el.content || !el.nav) return;
  bindClicks();
  const days = Number(el.period?.value) || 30;
  const myGeneration = ++generation;
  setStatus("Đang tải…");
  try {
    await loadSnapshot(days, myGeneration);
  } catch (error) {
    if (myGeneration !== generation) return;
    setStatus(error?.message || "Không tải được dữ liệu.");
  }
}

if (el.period) {
  el.period.addEventListener("change", () => void boot());
}
void boot();
