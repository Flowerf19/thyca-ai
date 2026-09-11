import { cleanText } from "./format.js";

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
    if (row.cost_usd != null && Number.isFinite(Number(row.cost_usd))) {
      group.costUsd = (group.costUsd || 0) + Number(row.cost_usd);
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

export function toolsFromDetail(detail) {
  const messages = Array.isArray(detail?.messages) ? detail.messages : [];
  const results = new Map();
  for (const message of messages) {
    if (message?.role === "tool" && message.tool_call_id) {
      results.set(String(message.tool_call_id), {
        content: message.content,
        latencyMs: finiteMs(message.meta?.latency_ms),
      });
    }
  }
  const tools = [];
  for (const message of messages) {
    if (message?.role !== "assistant") continue;
    for (const call of message.tool_calls || []) {
      if (!call?.id && !call?.name) continue;
      const result = results.get(String(call.id || ""));
      tools.push({
        id: cleanText(call.id),
        name: cleanText(call.name, "tool"),
        skill: cleanText(call.skill) || null,
        arguments: asArguments(call.arguments),
        output: result?.content ?? null,
        latencyMs: result?.latencyMs ?? null,
      });
    }
  }
  return tools;
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
export function toolDisplayName(tool) {
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
