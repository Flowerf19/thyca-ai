import { cleanText } from "./format.js";

function isoDay(date) {
  return date.toISOString().slice(0, 10);
}

export function rollingRange(days, now = new Date()) {
  const count = Math.max(1, Math.floor(Number(days) || 1));
  const end = new Date(now);
  const start = new Date(now);
  start.setUTCDate(start.getUTCDate() - count + 1);
  return { from: isoDay(start), to: isoDay(end), days: count };
}

export function traceRangeUrl(path, days, limit = null, now = new Date()) {
  const range = rollingRange(days, now);
  const params = new URLSearchParams({ from: range.from, to: range.to });
  if (limit != null) params.set("limit", String(limit));
  return `${path}?${params}`;
}

export function aggregateUsage(rows) {
  const byDay = new Map();
  const totals = { input: 0, cache: 0, output: 0, total: 0, turns: 0, requests: 0 };
  for (const row of Array.isArray(rows) ? rows : []) {
    const day = String(row?.started_at || "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) continue;
    const current = byDay.get(day) || { day, input: 0, cache: 0, output: 0, turns: 0, requests: 0 };
    const { input, cache } = splitPromptTokens(row.prompt_tokens, row.cached_tokens);
    const output = Number(row.completion_tokens) || 0;
    const requests = Number(row.requests) || 0;
    current.input += input;
    current.cache += cache;
    current.output += output;
    current.turns += 1;
    current.requests += requests;
    byDay.set(day, current);
    totals.input += input;
    totals.cache += cache;
    totals.output += output;
    totals.total += Number(row.total_tokens) || input + cache + output;
    totals.turns += 1;
    totals.requests += requests;
  }
  return {
    totals,
    days: [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day)),
  };
}

/* Providers report prompt_tokens as the whole input side, cache included
   (llm_base.normalize_usage: "cached_tokens is always a subset of
   prompt_tokens"), so a raw prompt count added to a cache count double-counts.
   Split once here — pricing.py does the same subtraction server-side. */
/* Known cost: number (0 is real), or null when never priced. null and ""
   both mean "no price recorded" — never fake zero. Shared by the Cost
   journal (cost-data.js) and the Trace journal (trace-data.js), which
   previously carried identical copies. */
export function finiteCost(value) {
  if (value == null || value === "") return null;
  const cost = Number(value);
  return Number.isFinite(cost) ? cost : null;
}

export function splitPromptTokens(promptTokens, cachedTokens) {
  const prompt = Number(promptTokens) || 0;
  const cache = Math.min(Math.max(Number(cachedTokens) || 0, 0), Math.max(prompt, 0));
  return { input: Math.max(prompt - cache, 0), cache };
}

/* Model-name sort key shared by the Cost and Request journals: both rank
   by-model rows with the same Vietnamese name order and the same
   last-started-at recency (name tiebreak). */
export function modelName(row) {
  return cleanText(row?.model);
}

export function byName(a, b) {
  return modelName(a).localeCompare(modelName(b), "vi");
}

export function byRecent(a, b) {
  return cleanText(b?.last_started_at).localeCompare(cleanText(a?.last_started_at))
    || byName(a, b);
}

/* Model rows for "Chi phí theo mô hình": filter by name, then order. A model
   with no configured price has no cost to rank, so it sorts last either way
   instead of posing as the cheapest. Buckets with no requests and no tokens
   (e.g. an "unknown" placeholder) have nothing to show, so they never become
   a row. */
export function selectModels(models, { sort = "cost-desc", query = "" } = {}) {
  const needle = cleanText(query).toLocaleLowerCase("vi");
  const rows = (Array.isArray(models) ? models : [])
    .filter((row) => (Number(row?.requests) || 0) > 0 || (Number(row?.total_tokens) || 0) > 0)
    .filter((row) => !needle || modelName(row).toLocaleLowerCase("vi").includes(needle));
  if (sort === "cost-asc") {
    return rows.sort((a, b) => byCost(a, b, 1) || byName(a, b));
  }
  if (sort === "recent") {
    return rows.sort(byRecent);
  }
  return rows.sort((a, b) => byCost(a, b, -1) || byName(a, b));
}

function byCost(a, b, direction) {
  return rank(a) - rank(b) || (value(a) - value(b)) * direction;
}

// Number(null) is 0, so an unpriced model would compare as free.
function value(row) {
  const cost = row?.cost_usd;
  return cost == null || cost === "" || !Number.isFinite(Number(cost)) ? 0 : Number(cost);
}

function rank(row) {
  const cost = row?.cost_usd;
  return cost == null || cost === "" || !Number.isFinite(Number(cost)) ? 1 : 0;
}

export function completeDays(rows, range) {
  const indexed = new Map((rows || []).map((row) => [row.day, row]));
  const start = new Date(`${range.from}T00:00:00Z`);
  const out = [];
  for (let index = 0; index < range.days; index += 1) {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    const day = isoDay(date);
    out.push(indexed.get(day) || { day, input: 0, cache: 0, output: 0, turns: 0, requests: 0 });
  }
  return out;
}
