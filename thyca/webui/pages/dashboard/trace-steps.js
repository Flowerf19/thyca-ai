import { cleanText } from "../../shared/js/format.js";

// Step/tool parsing for one trace-turn detail payload. Pure helpers, no DOM,
// no fetch — re-exported through trace-data.js so existing importers keep
// working. `formatRecordText` stays in trace-data.js (payload text rendering).

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
