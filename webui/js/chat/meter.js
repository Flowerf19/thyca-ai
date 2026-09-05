// Composer usage meter: last-turn fresh/cache/cost under the chat box.
// Semantics mirror Trace (INPUT=fresh = prompt − cached, CACHE, cost).
// Pure logic (no document/window at import) — safe for Node-based tests.
import { fmtCompact, fmtCost, fmtInt } from "../shared/util.js";

// Group messages into turns like thyca/trace.py turns_from_session:
// drop system, slice at each user message. Returns the last slice.
export function lastTurnSlice(messages) {
  if (!Array.isArray(messages) || !messages.length) return [];
  const filtered = messages.filter((m) => m && m.role !== "system");
  let cur = null;
  let last = [];
  for (const msg of filtered) {
    if (msg.role === "user") {
      if (cur) last = cur;
      cur = [msg];
    } else if (cur) {
      cur.push(msg);
    }
  }
  if (cur) last = cur;
  return last;
}

// Sum prompt/cached/cost over assistant meta.usage/meta.cost_usd in one turn slice. Mirrors _sum_tokens (naming kind included — Trace counts
// it as a request too, so the meter matches Recent rows).
export function sumLastTurnUsage(messages) {
  const slice = lastTurnSlice(messages);
  let prompt = null;
  let cached = null;
  let cost = null;
  let hasUsage = false;
  let hasCost = false;
  for (const msg of slice) {
    if (!msg || msg.role !== "assistant") continue;
    const meta = msg.meta || {};
    const usage = meta.usage;
    if (usage && typeof usage === "object") {
      const pt = usage.prompt_tokens;
      const ct = usage.cached_tokens;
      if (Number.isInteger(pt)) {
        prompt = (prompt || 0) + pt;
        hasUsage = true;
      }
      if (Number.isInteger(ct)) {
        cached = (cached || 0) + ct;
        hasUsage = true;
      }
    }
    const c = meta.cost_usd;
    if (typeof c === "number" && Number.isFinite(c)) {
      cost = (cost || 0) + c;
      hasCost = true;
    }
  }
  if (!hasUsage) {
    prompt = null;
    cached = null;
  } else if (cached == null) {
    cached = 0;
  }
  if (!hasCost) cost = null;
  const fresh = prompt != null ? Math.max(prompt - (cached || 0), 0) : null;
  return { prompt, cached, fresh, cost };
}

// Compact one-liner: fresh · cache · cost. Returns "" when no usage at all.
export function meterText(summary) {
  if (!summary || summary.fresh == null) return "";
  const parts = [`input ${fmtCompact(summary.fresh)}`];
  if (summary.cached) parts.push(`cache ${fmtCompact(summary.cached)}`);
  parts.push(fmtCost(summary.cost));
  return parts.join(" · ");
}

// Full-precision tooltip for the meter line.
export function meterTitle(summary) {
  if (!summary || summary.fresh == null) return "";
  const bits = [`input ${fmtInt(summary.fresh)}`];
  if (summary.cached) bits.push(`cache ${fmtInt(summary.cached)}`);
  bits.push(fmtCost(summary.cost));
  return `lượt vừa rồi — ${bits.join(" · ")}`;
}

// Render into a .hint-style node. No usage → "—" placeholder, keeps the row
// height stable instead of collapsing composer-meta.
export function renderComposerMeter(node, messages) {
  if (!node) return;
  const summary = sumLastTurnUsage(messages);
  const text = meterText(summary);
  if (!text) {
    node.textContent = "—";
    node.removeAttribute("title");
    return;
  }
  node.textContent = text;
  const title = meterTitle(summary);
  if (title) node.setAttribute("title", title);
  else node.removeAttribute("title");
}

export function clearComposerMeter(node) {
  if (!node) return;
  node.textContent = "—";
  node.removeAttribute("title");
}
