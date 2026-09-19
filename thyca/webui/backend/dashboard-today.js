import { cleanText } from "./format.js";

/* "Hôm nay" block on the Tổng quan screen: today's run metrics plus the
   recent-runs journal, both computed from the same trace rows the Trace
   screen uses (/api/traces). Kept in backend/ so the numbers and the
   journal share one implementation. */

const VIETNAM_OFFSET_MS = 7 * 60 * 60 * 1000;

function vietnamDayKey(value) {
  const date = new Date(String(value || ""));
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() + VIETNAM_OFFSET_MS).toISOString().slice(0, 10);
}

function todayKey() {
  return new Date(Date.now() + VIETNAM_OFFSET_MS).toISOString().slice(0, 10);
}

export function recentTraces(rows, limit = 5) {
  return (Array.isArray(rows) ? rows : [])
    .filter((row) => cleanText(row?.session_id))
    .sort((a, b) => String(b?.started_at || "").localeCompare(String(a?.started_at || "")))
    .slice(0, limit);
}

export function todaySummary(rows) {
  const today = todayKey();
  const todays = (Array.isArray(rows) ? rows : []).filter(
    (row) => vietnamDayKey(row?.started_at) === today,
  );
  const finished = todays.filter((row) => row?.status === "completed" || row?.status === "failed");
  const completed = todays.filter((row) => row?.status === "completed");
  const durations = finished.map((row) => Number(row?.latency_ms) || 0);
  const cost = todays.reduce((sum, row) => sum + (Number(row?.cost_usd) || 0), 0);
  const average = durations.length
    ? durations.reduce((sum, value) => sum + value, 0) / durations.length
    : null;
  return {
    runs: todays.length,
    completed: completed.length,
    failed: todays.length - completed.length,
    successRate: finished.length ? completed.length / finished.length : null,
    averageMs: average,
    costUsd: cost || null,
  };
}
