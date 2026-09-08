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
    const prompt = Number(row.prompt_tokens) || 0;
    const cache = Math.min(Math.max(Number(row.cached_tokens) || 0, 0), Math.max(prompt, 0));
    const input = Math.max(prompt - cache, 0);
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
