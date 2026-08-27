// Build a normalized staff score for a historical turn from its JSONL slice.
// Reuses the living-room grammar in staff-map.js (C major, 4/4, I–vi–IV–V).
import { scoreFromEvents } from "./staff-map.js";

export function traceScoreFromMessages(messages) {
  const slice = Array.isArray(messages) ? messages : [];
  if (!slice.length) return scoreFromEvents([]);
  const idToName = new Map();
  for (const msg of slice) {
    if (msg.role === "assistant" && Array.isArray(msg.tool_calls)) {
      for (const call of msg.tool_calls) {
        if (call && typeof call.id === "string" && typeof call.name === "string") {
          idToName.set(call.id, call.name);
        }
      }
    }
  }
  const seq = [{ type: "turn.accepted" }];
  for (const msg of slice) {
    if (msg.role === "assistant") {
      const meta = msg.meta || {};
      const round = Number.isInteger(meta.round)
        ? meta.round
        : seq.filter((e) => e.type === "llm.started").length + 1;
      const toolCount = Array.isArray(msg.tool_calls) ? msg.tool_calls.length : 0;
      seq.push({ type: "llm.started", round });
      seq.push({ type: "llm.finished", round, tool_count: toolCount });
    } else if (msg.role === "tool") {
      const meta = msg.meta || {};
      const round = Number.isInteger(meta.round) ? meta.round : 1;
      const name = (msg.tool_call_id && idToName.get(msg.tool_call_id)) || "tool";
      const ok = !(meta.is_error === true);
      seq.push({ type: "tool.started", round, call_id: String(msg.tool_call_id || "call"), name });
      seq.push({ type: "tool.finished", round, call_id: String(msg.tool_call_id || "call"), name, ok });
    }
  }
  const last = slice[slice.length - 1];
  const lastMeta = (last && last.meta) || {};
  const failed =
    !last ||
    last.role !== "assistant" ||
    lastMeta.status === "loop_limit" ||
    last.content === "loop limit reached" ||
    lastMeta.finish_reason === "error";
  seq.push({ type: failed ? "turn.failed" : "turn.completed" });
  return scoreFromEvents(seq);
}
