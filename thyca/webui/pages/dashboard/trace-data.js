import { cleanText } from "../../shared/js/format.js";

// Known turn cost: number (0 is real), or null when never priced. null and
// "" both mean "no price recorded" — never fake zero (same semantics as
// cost-data.js finiteCost; not imported to avoid an import cycle).
function finiteCost(value) {
  if (value == null || value === "") return null;
  const cost = Number(value);
  return Number.isFinite(cost) ? cost : null;
}

export function groupTraceTurns(rows) {
  const groups = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    const sessionId = cleanText(row?.session_id);
    if (!sessionId) continue;
    let group = groups.get(sessionId);
    if (!group) {
      group = {
        sessionId,
        title: cleanText(row?.title, sessionId),
        startedAt: cleanText(row?.started_at),
        endedAt: cleanText(row?.ended_at),
        totalTokens: 0,
        costUsd: null,
        pricedTurns: 0,
        latencyMs: 0,
        turns: [],
      };
      groups.set(sessionId, group);
    }
    group.turns.push(row);
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

export function asArguments(value) {
  if (value == null || value === "") return {};
  if (typeof value === "string") {
    try {
      return asArguments(JSON.parse(value));
    } catch {
      return { value };
    }
  }
  if (Array.isArray(value)) return { items: value };
  if (typeof value === "object") return value;
  return { value };
}

function toolResults(messages) {
  const results = new Map();
  for (const message of messages) {
    if (message?.role === "tool" && message.tool_call_id) {
      results.set(String(message.tool_call_id), {
        content: message.content,
        latencyMs: finiteMs(message.meta?.latency_ms),
        // Recorded error flag from the agent loop (agent/observe.py writes
        // meta.is_error) — the only trusted source for "this call failed".
        isError: message.meta?.is_error === true,
      });
    }
  }
  return results;
}

function toolCallsFromAssistant(message, results) {
  return (Array.isArray(message?.tool_calls) ? message.tool_calls : [])
    .filter((call) => call?.id || call?.name)
    .map((call) => {
      const result = results.get(String(call.id || ""));
      return {
        id: cleanText(call.id),
        name: cleanText(call.name, "tool"),
        skill: cleanText(call.skill) || null,
        arguments: asArguments(call.arguments),
        output: result?.content ?? null,
        latencyMs: result?.latencyMs ?? null,
      };
    });
}

function toolBatches(detail) {
  const messages = Array.isArray(detail?.messages) ? detail.messages : [];
  const results = toolResults(messages);
  return messages
    .filter((message) => message?.role === "assistant" && Array.isArray(message.tool_calls))
    .map((message) => toolCallsFromAssistant(message, results))
    .filter((batch) => batch.length);
}

// One step per think round, then that round's tools. Naming messages stay out
// so the activity line is Input → thinking → tools → thinking → Output.
export function activityStepsFromDetail(detail) {
  const messages = Array.isArray(detail?.messages) ? detail.messages : [];
  const results = toolResults(messages);
  const steps = [];
  for (const message of messages) {
    if (message?.role !== "assistant") continue;
    if ((message.meta || {}).kind === "naming") continue;
    steps.push({
      type: "thinking",
      content: typeof message.content === "string" ? message.content : "",
      latencyMs: finiteMs(message.meta?.latency_ms),
    });
    const batch = toolCallsFromAssistant(message, results);
    if (batch.length) steps.push({ type: "tools", groups: groupToolCalls(batch) });
  }
  return steps;
}

export function toolBatchesFromDetail(detail) {
  return toolBatches(detail);
}

// Flat execution journal for one turn, in real message order: user input,
// each assistant round followed by the tool calls that round issued, then
// the assistant reply. Every call stays an individual step (id kept, same-name
// calls never merged) and is only nested under the round that called it —
// no group-by-name, no parent tree beyond that round relation.
export function executionStepsFromDetail(detail) {
  const messages = Array.isArray(detail?.messages) ? detail.messages : [];
  const results = toolResults(messages);
  const steps = [];
  for (const message of messages) {
    if (message?.role === "user") {
      steps.push({
        type: "input",
        ts: cleanText(message?.ts) || null,
        content: typeof message?.content === "string" ? message.content : "",
      });
      continue;
    }
    if (message?.role !== "assistant" || (message.meta || {}).kind === "naming") continue;
    const ts = cleanText(message?.ts) || null;
    const content = typeof message?.content === "string" ? message.content : "";
    const latencyMs = finiteMs(message?.meta?.latency_ms);
    const calls = Array.isArray(message?.tool_calls) ? message.tool_calls : [];
    if (calls.length) {
      steps.push({ type: "thinking", ts, content, latencyMs });
      for (const call of calls) {
        if (!call?.id && !call?.name) continue;
        const result = results.get(String(call.id || ""));
        steps.push({
          type: "tool",
          ts,
          id: cleanText(call.id),
          name: cleanText(call.name, "tool"),
          skill: cleanText(call.skill) || null,
          parseError: cleanText(call.parse_error) || null,
          isError: result?.isError === true,
          arguments: asArguments(call.arguments),
          // null = no result message recorded at all; "" = a recorded but
          // empty result. The renderer must keep the two apart.
          output: result?.content ?? null,
          latencyMs: result?.latencyMs ?? null,
        });
      }
    } else {
      steps.push({ type: "output", ts, content, latencyMs });
    }
  }
  return steps;
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
export async function collectTracePages(fetchPage, { limit = 200 } = {}) {
  const rows = [];
  const seen = new Set();
  let offset = 0;
  // Rows dropped for a missing session_id: they count toward the server's
  // total but can never join `rows`, so completeness is judged on
  // rows + skipped, not rows alone.
  let skipped = 0;
  for (;;) {
    let page;
    try {
      page = await fetchPage({ limit, offset });
    } catch (error) {
      return { rows, complete: false, error };
    }
    const traces = Array.isArray(page?.traces) ? page.traces : [];
    const total = Number(page?.total);
    let added = 0;
    for (const row of traces) {
      const sessionId = cleanText(row?.session_id);
      if (!sessionId) {
        skipped += 1;
        continue;
      }
      const key = `${sessionId}\u0000${Number(row?.turn_index)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push(row);
      added += 1;
    }
    if (!traces.length) {
      // Window exhausted. Without a usable total there is nothing left to
      // expect; with one, fewer rows than total means pages went missing.
      if (!Number.isFinite(total)) return { rows, complete: true };
      if (rows.length >= total) return { rows, complete: true };
      return {
        rows,
        complete: false,
        error: new Error(`Đọc được ${rows.length}/${total} lượt trong cửa sổ trace.`),
      };
    }
    if (!added) {
      // A full page of already-seen rows: the offset is not advancing —
      // stop instead of looping forever.
      return {
        rows,
        complete: false,
        error: new Error("Trang dữ liệu lặp lại, không đọc tiếp được cửa sổ trace."),
      };
    }
    offset += traces.length;
    if (Number.isFinite(total) && offset >= total) {
      // offset counts RAW page rows while dedupe collapses overlaps, so it can
      // pass total while deduped rows are still missing (rows [0,1] then [1,2]
      // with total 4 never read row 3). Only the deduped count — plus the rows
      // skipped as invalid — proves the window was fully read.
      if (rows.length + skipped >= total) return { rows, complete: true };
      return {
        rows,
        complete: false,
        error: new Error(`Đọc được ${rows.length}/${total} lượt trong cửa sổ trace.`),
      };
    }
    if (!Number.isFinite(total) && traces.length < limit) return { rows, complete: true };
  }
}

export function toolsFromDetail(detail) {
  return toolBatches(detail).flat();
}

// Missing latency is null, never 0: Number(null) === 0 would print "0ms".
function finiteMs(value) {
  if (value == null || value === "") return null;
  const ms = Number(value);
  return Number.isFinite(ms) && ms >= 0 ? ms : null;
}

// What to print for one call: the skill name when the call loaded a skill,
// otherwise the tool name exactly as the agent called it. No lookup table —
// tools and skills the agent adds or removes show up as they are recorded.
function toolDisplayName(tool) {
  const skill = cleanText(tool?.skill);
  return skill || cleanText(tool?.name, "tool");
}

// One catalog entry per distinct name, first-seen order, with every call kept
// for the detail view. A skill load is its own entry (kind "skill") so it never
// merges into an ordinary read of the same file-reading tool.
export function groupToolCalls(tools) {
  const groups = new Map();
  for (const tool of Array.isArray(tools) ? tools : []) {
    const kind = cleanText(tool?.skill) ? "skill" : "tool";
    const name = toolDisplayName(tool);
    const key = `${kind}\u0000${name}`;
    let group = groups.get(key);
    if (!group) {
      group = { name, kind, count: 0, latencyMs: null, calls: [] };
      groups.set(key, group);
    }
    group.count += 1;
    const latency = finiteMs(tool?.latencyMs);
    if (latency !== null) group.latencyMs = (group.latencyMs || 0) + latency;
    group.calls.push({
      order: group.count,
      id: cleanText(tool?.id),
      arguments: tool?.arguments ?? {},
      output: tool?.output ?? null,
      latencyMs: latency,
    });
  }
  return [...groups.values()];
}

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
