/* Pure helpers for the Chi phí journal — no DOM, no fetch. The fetch and
   rendering side lives in webui/cost.js; paging reuses fetchAllTraces from
   dashboard-today.js. All aggregates run over one fixed snapshot of /api/traces
   rows (one range, fully paged) so shares never change denominator mid-view.

   Semantics kept from the backend (trace.py): a "turn" is one trace row keyed
   by (session_id, turn_index) — there is no separate trace id — while
   `requests` counts model calls inside a turn, so requests != turns. Stored
   cost_usd is authoritative; null means "no priced turn yet", not $0. */

import { cleanText } from "./format.js";
import { splitPromptTokens } from "./analytics-data.js";
import { selectedModelConfig, tokenCost } from "./trace-data.js";

/* Group key for rows whose session_id is missing/blank — rendered as its own
   explicit group instead of being dropped or silently merged. */
export const NO_SESSION_KEY = "(không có session)";

/* One row per turn, deduped on (session_id, turn_index). Blank session_id is
   stringified so the dedupe key stays unique. */
function dedupeTurns(rows) {
  const seen = new Set();
  const unique = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row !== "object") continue;
    const sessionId = cleanText(row.session_id);
    const turnIndex = row.turn_index == null ? "" : String(row.turn_index);
    const key = `${sessionId}\u0000${turnIndex}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(row);
  }
  return unique;
}

function finiteCost(value) {
  if (value == null || value === "") return null;
  const cost = Number(value);
  return Number.isFinite(cost) ? cost : null;
}

/* Cost of one turn: number (0 is real), or null when never priced. */
export function turnCost(row) {
  return finiteCost(row?.cost_usd);
}

/* Overview metrics for the four-metric header. `costUsd` sums only priced
   turns and is null when nothing was priced; `pricedTurns` lets the caller
   label a partial total instead of presenting it as complete. */
export function overviewMetrics(rows) {
  const turns = dedupeTurns(rows);
  let costUsd = null;
  let pricedTurns = 0;
  let requests = 0;
  let inputTokens = 0;
  let cacheTokens = 0;
  let outputTokens = 0;
  for (const row of turns) {
    const cost = turnCost(row);
    if (cost != null) {
      costUsd = (costUsd ?? 0) + cost;
      pricedTurns += 1;
    }
    requests += Number(row.requests) || 0;
    const { input, cache } = splitPromptTokens(row.prompt_tokens, row.cached_tokens);
    inputTokens += input;
    cacheTokens += cache;
    outputTokens += Number(row.completion_tokens) || 0;
  }
  return {
    turns: turns.length,
    requests,
    costUsd,
    pricedTurns,
    // Raw prompt sum = uncached input + cache (cache is a subset of prompt),
    // so the overview can show the full input side with a cache note.
    promptTokens: inputTokens + cacheTokens,
    inputTokens,
    cacheTokens,
    outputTokens,
  };
}

/* Per-model turn coverage derived from the loaded rows: stats.by_model has
   no per-model turn count, so the UI cannot label a partial model aggregate
   without recomputing it here. Key matches the backend grouping (`model or
   "unknown"`); dedupe on (session_id, turn_index) matches the overview, and
   cost 0 stays priced while missing cost stays missing. */
export function modelTurnCoverage(rows) {
  const coverage = new Map();
  for (const row of dedupeTurns(rows)) {
    const model = cleanText(row?.model) || "unknown";
    let entry = coverage.get(model);
    if (!entry) {
      entry = { turns: 0, pricedTurns: 0 };
      coverage.set(model, entry);
    }
    entry.turns += 1;
    if (turnCost(row) != null) entry.pricedTurns += 1;
  }
  return coverage;
}

/* One entry per session (blank id → NO_SESSION_KEY). Title comes from the
   newest turn that carries one — the list arrives newest-first, so the first
   title seen wins; no per-session detail request is made. costUsd keeps the
   same null/partial semantics as overviewMetrics. */
export function aggregateSessions(rows) {
  const sessions = new Map();
  for (const row of dedupeTurns(rows)) {
    const sessionId = cleanText(row.session_id);
    const key = sessionId || NO_SESSION_KEY;
    let group = sessions.get(key);
    if (!group) {
      group = {
        key,
        sessionId: sessionId || null,
        title: "",
        turns: 0,
        requests: 0,
        costUsd: null,
        pricedTurns: 0,
        inputTokens: 0,
        cacheTokens: 0,
        outputTokens: 0,
        lastStartedAt: "",
      };
      sessions.set(key, group);
    }
    group.turns += 1;
    group.requests += Number(row.requests) || 0;
    const cost = turnCost(row);
    if (cost != null) {
      group.costUsd = (group.costUsd ?? 0) + cost;
      group.pricedTurns += 1;
    }
    const { input, cache } = splitPromptTokens(row.prompt_tokens, row.cached_tokens);
    group.inputTokens += input;
    group.cacheTokens += cache;
    group.outputTokens += Number(row.completion_tokens) || 0;
    if (cleanText(row.started_at) > group.lastStartedAt) group.lastStartedAt = cleanText(row.started_at);
    if (!group.title && cleanText(row.title)) group.title = cleanText(row.title);
  }
  const list = [...sessions.values()];
  /* Cost desc (priced first, unpriced last), then recency, then key for a
     deterministic order. */
  return list.sort((a, b) => rankCost(a) - rankCost(b)
    || (rankCost(a) === 0 ? costValue(b) - costValue(a) : 0)
    || cleanText(b.lastStartedAt).localeCompare(cleanText(a.lastStartedAt))
    || a.key.localeCompare(b.key, "vi"));
}

function costValue(group) {
  return group.costUsd ?? 0;
}

function rankCost(group) {
  return group.costUsd == null ? 1 : 0;
}

/* Fixed share denominator: the sum over the whole snapshot's model rows,
   computed before any search filter is applied. Null when nothing is priced,
   so the caller renders "—" instead of a NaN share. */
export function knownCostTotal(models) {
  let total = null;
  for (const row of Array.isArray(models) ? models : []) {
    const cost = finiteCost(row?.cost_usd);
    if (cost != null) total = (total ?? 0) + cost;
  }
  return total;
}

/* Share as a percent string, or "—" when either side is unknown or the
   denominator is not a positive number. Number(null) is 0, so unpriced
   values are rejected before coercion — an unpriced row must never pose as
   0%, and total=0 must not make NaN. */
export function shareLabel(value, total) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number) || !Number.isFinite(total) || total <= 0) return "—";
  return `${Math.round(number / total * 100)}%`;
}

/* Meter fill as a CSS var value, clamped to 0–100% for rendering safety;
   null when unknown so the meter stays empty. */
export function shareRatio(value, total) {
  if (value == null || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number) || !Number.isFinite(total) || total <= 0) return null;
  const ratio = Math.min(Math.max(number / total * 100, 0), 100);
  return `${ratio}%`;
}

/* Rates for one model from /api/config (values.pricing / values.models,
   USD per 1M tokens). Null when the model has no configured price — the UI
   then shows "—" and must not derive dollar amounts. */
export function priceRates(configValues, modelName) {
  const entry = selectedModelConfig(configValues, modelName);
  if (!entry || typeof entry !== "object") return null;
  const pick = (value) => {
    // null/blank = unknown rate, never coerced to a free $0; an explicit 0
    // (genuinely free tier) stays 0.
    if (value == null || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };
  const rates = {
    input: pick(entry.input),
    cache: pick(entry.cache),
    output: pick(entry.output),
  };
  return rates.input == null && rates.cache == null && rates.output == null ? null : rates;
}

/* Rate-derived input/cache/output dollar split for one model row. This is an
   ESTIMATE at the current configured price — historical turns were priced at
   the price of their day, so the caller must label it as such and never
   present it as the recorded total. Returns null when the model is unpriced;
   individual sides stay null when that rate is missing. */
export function estimatedCostSplit(model, rates) {
  if (!rates) return null;
  const { input, cache } = splitPromptTokens(model?.prompt_tokens, model?.cached_tokens);
  return {
    inputUsd: rates.input == null ? null : tokenCost(input, rates.input),
    cacheUsd: rates.cache == null ? null : tokenCost(cache, rates.cache),
    outputUsd: rates.output == null ? null : tokenCost(Number(model?.completion_tokens) || 0, rates.output),
  };
}
