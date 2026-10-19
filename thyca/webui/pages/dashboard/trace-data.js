import { cleanText } from "../../shared/js/format.js";
import { finiteCost } from "../../shared/js/analytics-data.js";
import { collectTraceWindow } from "../../shared/js/dashboard-today.js";

// Step/tool parsing lives in trace-steps.js; pricing lives in cost-data.js
// (its only consumer). Both are re-exported here so existing trace-data.js
// importers keep working.
export {
  activityStepsFromDetail,
  asArguments,
  executionStepsFromDetail,
  groupToolCalls,
  toolBatchesFromDetail,
  toolsFromDetail,
} from "./trace-steps.js";
export { selectedModelConfig, tokenCost } from "./cost-data.js";

export function groupTraceTurns(rows) {
  const groups = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    const sessionId = cleanText(row?.session_id);
    if (!sessionId) continue;
    let group = groups.get(sessionId);
    if (!group) {
      group = {
        sessionId,
        title: cleanText(row?.title),
        startedAt: cleanText(row?.started_at),
        endedAt: cleanText(row?.ended_at),
        kind: typeof row?.session_kind === "string" ? row.session_kind : "",
        dm: typeof row?.session_dm === "string" ? row.session_dm : "",
        bots: Array.isArray(row?.session_bots)
          ? row.session_bots.map(String).filter(Boolean) : [],
        totalTokens: 0,
        costUsd: null,
        pricedTurns: 0,
        latencyMs: 0,
        turns: [],
      };
      groups.set(sessionId, group);
    }
    group.turns.push(row);
    if (!group.kind && typeof row?.session_kind === "string" && row.session_kind) {
      group.kind = row.session_kind;
    }
    if (!group.dm && typeof row?.session_dm === "string" && row.session_dm) {
      group.dm = row.session_dm;
    }
    if (!group.bots.length && Array.isArray(row?.session_bots)) {
      group.bots = row.session_bots.map(String).filter(Boolean);
    }
    if (String(row.started_at || "") > group.startedAt) group.startedAt = String(row.started_at || "");
    if (String(row.ended_at || "") > group.endedAt) group.endedAt = String(row.ended_at || "");
    group.totalTokens += Number(row.total_tokens) || 0;
    group.latencyMs += Number(row.latency_ms) || 0;
    // costUsd sums turns that recorded a price; pricedTurns lets the caller
    // label coverage. A recorded cost can still be partial INSIDE the turn
    // (the backend sums per-message cost_usd and skips unpriced rounds), so
    // callers must label the sum as recorded-known — never as a guaranteed
    // fully priced total.
    const cost = finiteCost(row?.cost_usd);
    if (cost != null) {
      group.costUsd = (group.costUsd ?? 0) + cost;
      group.pricedTurns += 1;
    }
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      turns: group.turns.slice().sort((a, b) => Number(a.turn_index) - Number(b.turn_index)),
    }))
    .sort((a, b) => b.startedAt.localeCompare(a.startedAt) || b.sessionId.localeCompare(a.sessionId));
}

/* Bots in a trace group: participants first, then turn/breakdown
   contributors not already listed (null/"unknown"/blanks dropped). */
export function traceGroupBotIds(group) {
  const seen = new Set();
  const out = [];
  const take = (id) => {
    const key = String(id || "");
    if (!key || key === "unknown" || seen.has(key)) return;
    seen.add(key);
    out.push(key);
  };
  if (group && typeof group === "object") {
    for (const id of Array.isArray(group.bots) ? group.bots : []) take(id);
    for (const turn of Array.isArray(group.turns) ? group.turns : []) {
      if (!turn || typeof turn !== "object") continue;
      take(turn.bot_id);
      const breakdown = Array.isArray(turn.bot_breakdown) ? turn.bot_breakdown : [];
      for (const entry of breakdown) take(entry && entry.bot_id);
    }
  }
  return out;
}

/* Bot ids offered by the Trace filter: thyca first, the rest by id. */
export function traceBotOptions(groups) {
  const seen = new Set();
  for (const group of Array.isArray(groups) ? groups : []) {
    for (const id of traceGroupBotIds(group)) seen.add(id);
  }
  const rest = [...seen].filter((id) => id !== "thyca").sort((a, b) => a.localeCompare(b, "vi"));
  return seen.has("thyca") ? ["thyca", ...rest] : rest;
}

/* Filter trace groups by bot ("" = all). A group matches when the bot is
   a participant or contributed to one of its turns. */
export function filterTraceGroups(groups, botId) {
  const id = String(botId || "");
  if (!id) return Array.isArray(groups) ? groups : [];
  return (Array.isArray(groups) ? groups : []).filter((group) =>
    traceGroupBotIds(group).includes(id));
}


export function formatRecordText(value) {
  if (value == null || value === "") return "—";
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return "—";
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        return formatRecordText(JSON.parse(trimmed));
      } catch {
        return value;
      }
    }
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const lines = value.map(formatRecordText).filter((line) => line !== "—");
    return lines.length ? lines.join("\n") : "—";
  }
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (!entries.length) return "—";
    return entries.map(([key, nested]) => {
      const text = formatRecordText(nested);
      return text.includes("\n") ? `${key}:\n${text}` : `${key}: ${text}`;
    }).join("\n");
  }
  return String(value);
}

// Trace-local time formatting: the shared format helpers render in the
// viewer's zone, while Trace must render in the IANA zone from /api/config.
// Returns null when the zone is missing or not a valid IANA name.
export function traceTimeFormatter(timeZone) {
  const zone = cleanText(timeZone);
  if (!zone) return null;
  try {
    return {
      zone,
      time: new Intl.DateTimeFormat("vi-VN", {
        timeZone: zone,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }),
      full: new Intl.DateTimeFormat("vi-VN", {
        timeZone: zone,
        dateStyle: "medium",
        timeStyle: "medium",
      }),
    };
  } catch {
    return null;
  }
}

// { text, title, ok } — ok=false when the zone or the value is missing; the
// clock part of the raw timestamp is shown then, never dressed up as zoned
// time, and the title keeps the raw value for inspection.
export function formatTraceTimestamp(value, formatter) {
  const raw = cleanText(value);
  if (!raw) return { text: "—", title: "", ok: false };
  const date = new Date(raw);
  if (formatter && !Number.isNaN(date.getTime())) {
    return { text: formatter.time.format(date), title: formatter.full.format(date), ok: true };
  }
  const clock = raw.match(/[T ](\d{2}:\d{2}(:\d{2})?)/);
  return { text: clock ? clock[1] : raw, title: raw, ok: false };
}

// Payload text for a disclosure block. null = nothing recorded (caller omits
// the block); strings — including empty and a literal "—" — render verbatim;
// structured values go through formatRecordText.
export function formatStepPayload(value) {
  if (value == null) return null;
  if (typeof value === "string") return value;
  return formatRecordText(value);
}

// Page through the /api/traces window until the reported total is covered or
// the API stops returning rows. fetchPage({limit, offset}) resolves one page
// {traces, total} — injected so tests run without fetch/DOM. Overlapping pages
// dedupe on (session_id, turn_index). complete=false means the window was not
// fully read: callers must show incomplete instead of treating rows as all.
// The loop is the shared collectTraceWindow with the Trace policy: fetch
// errors and short windows return instead of throwing, blank-session rows
// are skipped but counted toward the total, and pages without a total end
// the window when short.
export async function collectTracePages(fetchPage, { limit = 200 } = {}) {
  return collectTraceWindow(fetchPage, {
    limit,
    objectArg: true,
    skipBlankSession: true,
    indexKey: (value) => `${Number(value)}`,
    catchFetchError: true,
    tolerateMissingTotal: true,
    checkCoverageOnRepeat: false,
    onIncomplete: "return",
    incompleteError: (site, ctx) => (site === "repeat-page"
      ? new Error("Trang dữ liệu lặp lại, không đọc tiếp được cửa sổ trace.")
      : new Error(`Đọc được ${ctx.rows}/${ctx.total} lượt trong cửa sổ trace.`)),
  });
}

export function firstUserText(detail) {
  const message = (detail?.messages || []).find((item) => item?.role === "user");
  return cleanText(message?.content, "Không có nội dung user trong trace.");
}

export function finalAssistantText(detail) {
  const message = [...(detail?.messages || [])]
    .reverse()
    .find((item) => item?.role === "assistant" && typeof item.content === "string" && item.content.trim());
  return cleanText(message?.content, "Không có nội dung assistant trong trace.");
}

/* Team v1: delegation tree over transcript messages carrying trace meta
   (contract §6: bot_id/trace_id/parent_span_id). Implemented once in the
   chat lane's team-data.js and re-exported here for the Trace view. */
export { buildDelegationTree } from "../chat/team-data.js";
