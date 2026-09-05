// Turn timeline for the trace detail paper — spans derived from detail.messages
// only (no new API), each row a native <details> with a latency bar.

import { escapeHtml, fmtLatency } from "../shared/util.js";

const SPAN_TEXT_LIMIT = 4000;

function truncateBody(text) {
  const raw = String(text == null ? "" : text).trim();
  if (!raw) return "";
  return escapeHtml(raw.length <= SPAN_TEXT_LIMIT ? raw : `${raw.slice(0, SPAN_TEXT_LIMIT)} …`);
}

function spanPre(text) {
  const body = truncateBody(text);
  return body ? `<pre>${body}</pre>` : "";
}

function publicJson(value) {
  return spanPre(JSON.stringify(stripArgs(value), null, 2));
}

function stripArgs(value) {
  if (Array.isArray(value)) return value.map(stripArgs);
  if (!value || typeof value !== "object") return value;
  const out = { ...value };
  if (Array.isArray(out.tool_calls)) {
    out.tool_calls = out.tool_calls.map((call) => {
      if (!call || typeof call !== "object") return call;
      return { id: call.id, name: call.name };
    });
  }
  return out;
}

// think/act/observe/naming share the same redacted-JSON body.
function thinkBody(msg) {
  return publicJson(msg);
}

function actBody(tools) {
  return publicJson(tools);
}

function observeBody(msg) {
  return publicJson(msg);
}

function namingBody(msg) {
  return publicJson(msg);
}

// Only spans present in messages: think#n, tools -> act, final text -> observe, meta.kind==naming -> naming.
// Each row is a native <details>; summary keeps label + latency bar, body holds the payload.
export function timelineSpans(messages, turnLat) {
  const spans = [];
  let thinkCount = 0;
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") continue;
    if (msg.role === "assistant") {
      const meta = msg.meta || {};
      if (meta.kind === "naming") {
        spans.push({ label: "naming", latency: Number(meta.latency_ms) || 0, body: namingBody(msg) });
        continue;
      }
      thinkCount += 1;
      const hasTools = Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0;
      if (hasTools) {
        spans.push({ label: `think #${thinkCount}`, latency: Number(meta.latency_ms) || 0, body: thinkBody(msg) });
        spans.push({ label: "act", latency: 0, tools: [] });
      } else {
        // assistant text without tool_calls ends the loop -> observe (không đẩy thêm think trùng nội dung)
        spans.push({ label: "observe", latency: Number(meta.latency_ms) || 0, body: observeBody(msg) });
      }
    } else if (msg.role === "tool" && spans.length > 0 && spans[spans.length - 1].label === "act") {
      const act = spans[spans.length - 1];
      act.latency += Number((msg.meta || {}).latency_ms) || 0;
      act.tools.push(msg);
    }
  }
  let elapsed = 0;
  return spans
    .map((span) => {
      const body = span.label === "act" ? actBody(span.tools) : span.body || "";
      const startPct = turnLat > 0 ? Math.min(100, (elapsed / turnLat) * 100) : 0;
      const widthPct =
        turnLat > 0 && span.latency > 0 ? Math.min(100 - startPct, (span.latency / turnLat) * 100) : 0;
      if (span.latency > 0) elapsed += span.latency;
      const time =
        span.latency > 0 ? `<span class="trace-span-time">${escapeHtml(fmtLatency(span.latency))}</span>` : `<span class="trace-span-time"></span>`;
      return `<li class="is-done">
          <details class="trace-span">
            <summary><span class="phase-name">${escapeHtml(span.label)}</span>${time}<div class="track-rule"><span class="trace-span-spacer" style="flex-basis:${startPct}%"></span><span class="trace-span-fill" style="flex-basis:${widthPct}%"></span></div></summary>
            <div class="trace-span-body">${body}</div>
          </details>
        </li>`;
    })
    .join("");
}