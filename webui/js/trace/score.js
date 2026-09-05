// Replay a JSONL slice as the event sequence the live stream would have
// emitted. The server classifies skill loads at payload build time
// (thyca/trace_api.py) and marks them with a `skill` field — arguments/paths
// never leave the server. traceScoreFromEvents re-emits skill.* from that
// marker, matching the live stream.

export function skillNameForRead(call) {
  if (!call || typeof call.skill !== "string") return null;
  return call.skill;
}

export function traceScoreFromEvents(messages) {
  const slice = Array.isArray(messages) ? messages : [];
  if (!slice.length) return [];
  const idToCall = new Map();
  for (const msg of slice) {
    if (msg.role === "assistant" && Array.isArray(msg.tool_calls)) {
      for (const call of msg.tool_calls) {
        if (call && typeof call.id === "string" && typeof call.name === "string") {
          idToCall.set(call.id, call);
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
      const call = (msg.tool_call_id && idToCall.get(msg.tool_call_id)) || null;
      const name = (call && call.name) || "tool";
      const ok = !(meta.is_error === true);
      const skillName = skillNameForRead(call);
      const kind = skillName ? "skill" : "tool";
      const publicName = skillName || name;
      seq.push({ type: `${kind}.started`, round, call_id: String(msg.tool_call_id || "call"), name: publicName });
      seq.push({ type: `${kind}.finished`, round, call_id: String(msg.tool_call_id || "call"), name: publicName, ok });
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
  return seq;
}
