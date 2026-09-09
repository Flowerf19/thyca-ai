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
        latencyMs: Number(message.meta?.latency_ms),
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
        name: cleanText(call.skill ? `skill:${call.skill}` : call.name, "tool"),
        arguments: asArguments(call.arguments),
        output: result?.content ?? null,
        latencyMs: Number.isFinite(result?.latencyMs) ? result.latencyMs : null,
      });
    }
  }
  return tools;
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
