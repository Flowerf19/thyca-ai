// Composer usage meter: last-turn fresh/cache/out/ctx/cost under the chat box.
// Semantics mirror Trace (INPUT=fresh = prompt − cached, CACHE, OUTPUT, cost).
// ctx = prompt_tokens of the last non-naming LLM round (one-request window),
// not the summed prompt (that is fresh+cache). Pure logic — safe for Node tests.
import { collapseNames } from "./status.js";
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

// Sum prompt/cached/completion/cost over assistant meta in one turn slice.
// Mirrors _sum_tokens (naming included in sums — Trace counts it as a request).
// ctx skips naming: that call is appended after the turn and is not the window.
export function sumLastTurnUsage(messages) {
  const slice = lastTurnSlice(messages);
  let prompt = null;
  let cached = null;
  let completion = null;
  let ctx = null;
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
      const cot = usage.completion_tokens;
      if (Number.isInteger(pt)) {
        prompt = (prompt || 0) + pt;
        hasUsage = true;
        if (meta.kind !== "naming") ctx = pt;
      }
      if (Number.isInteger(ct)) {
        cached = (cached || 0) + ct;
        hasUsage = true;
      }
      if (Number.isInteger(cot)) {
        completion = (completion || 0) + cot;
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
    completion = null;
    ctx = null;
  } else {
    if (cached == null) cached = 0;
    if (completion == null) completion = 0;
  }
  if (!hasCost) cost = null;
  const fresh = prompt != null ? Math.max(prompt - (cached || 0), 0) : null;
  return { prompt, cached, fresh, completion, ctx, cost };
}

// Last-turn tool names, first-seen with counts. Empty when the turn used none.
export function lastTurnTools(messages) {
  const names = [];
  for (const msg of lastTurnSlice(messages)) {
    if (!msg || msg.role !== "assistant" || !Array.isArray(msg.tool_calls)) continue;
    for (const call of msg.tool_calls) {
      const name = String(call && call.name ? call.name : "").trim();
      if (name) names.push(name);
    }
  }
  return collapseNames(names);
}

// Compact one-liner: input · cache · output · context · cost. Returns "" when no usage.
export function meterText(summary) {
  if (!summary || summary.fresh == null) return "";
  const parts = [`input ${fmtCompact(summary.fresh)}`];
  if (summary.cached) parts.push(`cache ${fmtCompact(summary.cached)}`);
  if (summary.completion) parts.push(`output ${fmtCompact(summary.completion)}`);
  if (summary.ctx != null) parts.push(`context ${fmtCompact(summary.ctx)}`);
  parts.push(`cost ${fmtCost(summary.cost)}`);
  return parts.join(" · ");
}

// Full-precision tooltip for the meter line.
export function meterTitle(summary) {
  if (!summary || summary.fresh == null) return "";
  const bits = [`input ${fmtInt(summary.fresh)}`];
  if (summary.cached) bits.push(`cache ${fmtInt(summary.cached)}`);
  if (summary.completion) bits.push(`output ${fmtInt(summary.completion)}`);
  if (summary.ctx != null) bits.push(`context ${fmtInt(summary.ctx)}`);
  bits.push(`cost ${fmtCost(summary.cost)}`);
  return `lượt vừa rồi — ${bits.join(" · ")}`;
}

// Render into a .hint-style node. No usage → "—" placeholder, keeps the row
// height stable instead of collapsing composer-meta.
export function renderComposerMeter(node, messages, toolsNode) {
  if (node) {
    const summary = sumLastTurnUsage(messages);
    const text = meterText(summary);
    if (!text) {
      node.textContent = "—";
      node.removeAttribute("title");
    } else {
      node.replaceChildren();
      for (const part of text.split(" · ")) {
        const span = document.createElement("span");
        span.textContent = part;
        node.append(span);
      }
      const title = meterTitle(summary);
      if (title) node.setAttribute("title", title);
      else node.removeAttribute("title");
    }
  }
  if (toolsNode) {
    const tools = lastTurnTools(messages);
    toolsNode.textContent = tools;
    toolsNode.hidden = !tools;
    if (tools) toolsNode.setAttribute("title", tools);
    else toolsNode.removeAttribute("title");
  }
}

export function clearComposerMeter(node, toolsNode) {
  if (node) {
    node.textContent = "—";
    node.removeAttribute("title");
  }
  if (toolsNode) {
    toolsNode.textContent = "";
    toolsNode.hidden = true;
    toolsNode.removeAttribute("title");
  }
}
