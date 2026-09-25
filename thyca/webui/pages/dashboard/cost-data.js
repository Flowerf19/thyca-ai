/* Pure helpers for the Chi phí journal — no DOM, no fetch. The fetch and
   rendering side lives in pages/dashboard/cost.js; paging reuses
   fetchAllTraces from shared/js/dashboard-today.js. All aggregates run over one fixed snapshot of /api/traces
   rows (one range, fully paged) so shares never change denominator mid-view.

   Semantics kept from the backend (trace.py): a "turn" is one trace row keyed
   by (session_id, turn_index) — there is no separate trace id — while
   `requests` counts model calls inside a turn, so requests != turns. Stored
   cost_usd is authoritative; null means "no priced turn yet", not $0. */

import { cleanText, formatInteger } from "../../shared/js/format.js";
import { completeDays, finiteCost, splitPromptTokens } from "../../shared/js/analytics-data.js";

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

/* Cost of one turn: number (0 is real), or null when never priced. */
export function turnCost(row) {
  return finiteCost(row?.cost_usd);
}

/* Overview metrics for the four-metric header. `costUsd` sums only priced
   turns and is null when nothing was priced; `pricedTurns` lets the caller
   label a partial total instead of presenting it as complete.
   `averageCostUsd`/`averageTurns` are the per-turn average basis: priced
   turns that are not failed — error turns are skipped, unpriced turns cannot
   contribute — so the average always divides a cost sum by exactly the turns
   that make it up, even when coverage is partial. */
export function overviewMetrics(rows) {
  const turns = dedupeTurns(rows);
  let costUsd = null;
  let pricedTurns = 0;
  let averageCostUsd = null;
  let averageTurns = 0;
  let requests = 0;
  let inputTokens = 0;
  let cacheTokens = 0;
  let outputTokens = 0;
  for (const row of turns) {
    const cost = turnCost(row);
    if (cost != null) {
      costUsd = (costUsd ?? 0) + cost;
      pricedTurns += 1;
      if (cleanText(row.status) !== "failed") {
        averageCostUsd = (averageCostUsd ?? 0) + cost;
        averageTurns += 1;
      }
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
    averageCostUsd,
    averageTurns,
    // Raw prompt sum = uncached input + cache (cache is a subset of prompt),
    // so the overview can show the full input side with a cache note.
    promptTokens: inputTokens + cacheTokens,
    inputTokens,
    cacheTokens,
    outputTokens,
  };
}

/* Per-turn average for the header: value plus its meta lines. Runs over the
   average basis (priced, non-failed turns); null value with an explanatory
   line when no turn qualifies. The skip line splits failed vs unpriced so it
   never clashes with the header total's "đã định giá N" coverage note.
   Kept here (not in cost.js) so the formula and wording stay unit-tested. */
export function averageDisplay(overview) {
  const turns = Number(overview?.averageTurns) || 0;
  const cost = overview?.averageCostUsd;
  if (!turns || cost == null) {
    return {
      value: null,
      meta: [overview?.turns ? "không có lượt nào đủ giá để tính trung bình" : "chưa có lượt nào"],
    };
  }
  const pricedTurns = Number(overview?.pricedTurns) || 0;
  const totalTurns = Number(overview?.turns) || 0;
  const skipped = [];
  const failed = Math.max(pricedTurns - turns, 0);
  const unpriced = Math.max(totalTurns - pricedTurns, 0);
  if (failed > 0) skipped.push(`${formatInteger(failed)} lỗi`);
  if (unpriced > 0) skipped.push(`${formatInteger(unpriced)} chưa định giá`);
  return {
    value: cost / turns,
    meta: [
      `${formatInteger(turns)} lượt hợp lệ`,
      ...(skipped.length ? [`bỏ ${skipped.join(" · ")}`] : []),
    ],
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

/* Token total of one by_model row: the whole prompt side (cached_tokens is
   already inside prompt_tokens) plus completion, so the Token-by-model
   table and its Đầu vào/Cache/Đầu ra breakdown always add up. */
export function modelTokens(row) {
  const prompt = Number(row?.prompt_tokens) || 0;
  const completion = Number(row?.completion_tokens) || 0;
  return prompt + completion;
}

/* Token denominator over the snapshot's models (mirrors knownCostTotal);
   null when nothing to share so meters stay empty instead of 0%. */
export function knownTokenTotal(models) {
  let total = null;
  for (const row of Array.isArray(models) ? models : []) {
    const tokens = modelTokens(row);
    if (tokens > 0) total = (total ?? 0) + tokens;
  }
  return total;
}

/* Cost per day for the overview chart: priced daily totals from stats.by_day
   (null = no priced turn that day, drawn as zero), zero-filled across the
   snapshot range so gaps read as gaps. */
export function dailyCosts(byDay, range) {
  const rows = (Array.isArray(byDay) ? byDay : [])
    .filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(String(row?.day || "")))
    .map((row) => ({ day: row.day, value: Number(row?.cost_usd) || 0 }));
  return completeDays(rows, range).map((row) => ({ day: row.day, value: Number(row.value) || 0 }));
}

/* Share formatter lives in shared/js/format.js (one copy for the Cost,
   Token and Request journals); re-exported here so existing cost-data.js
   importers keep working. */
export { shareLabel, shareRatio } from "../../shared/js/format.js";

/* Model pricing from /api/config (values.models, USD per 1M tokens, with
   the legacy values.pricing fallback). Owned here — Cost is the only
   consumer — and re-exported through trace-data.js for existing importers. */
export function selectedModelConfig(configValues, modelName) {
  const models = configValues?.models;
  const model = models && typeof models === "object" ? models[modelName] : null;
  const pricing = configValues?.pricing;
  const legacy = pricing && typeof pricing === "object" ? pricing[modelName] : null;
  return model || legacy || null;
}

export function tokenCost(tokens, rate) {
  const count = Number(tokens);
  const price = Number(rate);
  if (!Number.isFinite(count) || !Number.isFinite(price)) return null;
  return count * price / 1_000_000;
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
