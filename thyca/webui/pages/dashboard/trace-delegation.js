import { formatTime } from "../../shared/js/format.js";
import { buildDelegationTree } from "./trace-data.js";
import { tintVarFor } from "../chat/team-data.js";

// Delegation tree (FE-5): appended to an opened turn disclosure, grouping the
// turn's messages by bot_id/trace_id/parent_span_id meta (contract §6).
// Messages are the backend's own transcript — the tree only regroups them.
// Without trace meta the block says so honestly instead of inventing lanes.
function nodeLabel(node) {
  const bot = node.botId || "thyca";
  const kind = node.kind ? ` · ${node.kind}` : "";
  return `${bot}${kind}`;
}

function nodeText(node) {
  const message = node.message || {};
  if (typeof message.content === "string" && message.content.trim()) {
    const first = message.content.trim().split("\n")[0];
    return first.length > 140 ? `${first.slice(0, 140)}…` : first;
  }
  const calls = Array.isArray(message.tool_calls) ? message.tool_calls : [];
  if (calls.length) return `gọi ${calls.map((call) => call?.skill || call?.name || "?").join(", ")}`;
  return "";
}

function renderNode(node) {
  const item = document.createElement("li");
  item.className = "delegation-node";
  item.style.setProperty("--team-tint", tintVarFor(node.botId, node.message?.meta?.bot_tint));
  const head = document.createElement("div");
  head.className = "delegation-head";
  const who = document.createElement("strong");
  who.textContent = nodeLabel(node);
  head.append(who);
  const text = nodeText(node);
  if (text) {
    const copy = document.createElement("span");
    copy.className = "delegation-text";
    copy.textContent = text;
    head.append(copy);
  }
  if (node.message?.ts) {
    const time = document.createElement("time");
    time.dateTime = String(node.message.ts);
    time.textContent = formatTime(node.message.ts);
    head.append(time);
  }
  item.append(head);
  if (node.children.length) {
    const kids = document.createElement("ul");
    kids.className = "delegation-kids";
    for (const child of node.children) kids.append(renderNode(child));
    item.append(kids);
  }
  return item;
}

// Returns an element to append, or null when there is nothing to show.
// A turn whose messages carry no trace meta gets one muted line (the caller
// appends it); a turn with no messages at all gets null.
export function renderDelegation(detail) {
  const messages = Array.isArray(detail?.messages) ? detail.messages : [];
  if (!messages.length) return null;
  const wrap = document.createElement("section");
  wrap.className = "trace-delegation";
  wrap.setAttribute("aria-label", "Cây giao việc");
  const title = document.createElement("h3");
  title.className = "cost-model-heading";
  title.textContent = "Cây giao việc";
  wrap.append(title);
  const roots = buildDelegationTree(messages);
  const traced = roots.some((node) => node.traceId || node.children.length
    || (node.message?.meta && ("trace_id" in node.message.meta || "parent_span_id" in node.message.meta)));
  if (!traced) {
    const note = document.createElement("p");
    note.className = "screen-note";
    note.textContent = "Lượt này chưa có dữ liệu giao việc (backend chưa gắn trace_id).";
    wrap.append(note);
    return wrap;
  }
  const tree = document.createElement("ul");
  tree.className = "delegation-tree";
  for (const node of roots) tree.append(renderNode(node));
  wrap.append(tree);
  return wrap;
}
