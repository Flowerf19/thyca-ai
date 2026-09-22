import { bindThinkingToggle, elapsedLabel, settledThinkingNote } from "./chat-thinking.js";
import { formatTime } from "../../shared/js/format.js";
import { formatMarkdown } from "../../shared/js/markdown.js";
import { chatBrandHeader, usageRow } from "./chat-view.js";

function assistantHeader({ expandable = false, latencyMs } = {}) {
  const status = Number.isFinite(latencyMs) && latencyMs >= 0
    ? `đã viết · ${elapsedLabel(Math.round(latencyMs / 1000))}`
    : "đã viết";
  return chatBrandHeader({ state: "idle", status, expandable });
}

function userMessage(message) {
  const article = document.createElement("article");
  article.className = "message-user";
  const body = document.createElement("p");
  body.textContent = String(message.content || "");
  const footer = document.createElement("footer");
  const time = document.createElement("time");
  time.dateTime = String(message.ts || "");
  time.textContent = formatTime(message.ts);
  const mark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  mark.classList.add("read-mark");
  mark.setAttribute("viewBox", "0 0 12 10");
  mark.setAttribute("role", "img");
  mark.setAttribute("aria-label", "Đã nhận");
  mark.innerHTML = '<path d="M1.4 5.4 4.2 8.2 10.6 1.6"/>';
  footer.append(time, mark);
  article.append(body, footer);
  return article;
}

function assistantMessage(segments, ts) {
  const article = document.createElement("article");
  article.className = "live-card message-assistant";
  const blocks = segments.map((segment) => ({
    segment,
    thought: settledThinkingNote(segment.reasoning),
  }));
  const thoughts = blocks.map((block) => block.thought).filter(Boolean);
  const latencyMs = blocks.reduce((total, block) => {
    const ms = block.segment.latencyMs;
    if (!Number.isFinite(ms) || ms < 0) return total;
    return (total ?? 0) + ms;
  }, null);
  const header = assistantHeader({
    expandable: thoughts.length > 0,
    latencyMs,
  });
  article.append(header);
  if (thoughts.length) {
    bindThinkingToggle(header.querySelector(".chat-brand-line"), {
      bodies: thoughts.map((item) => item.body),
      notes: thoughts.map((item) => item.note),
    });
  }
  // Chronological within a round: thinking, then the reply text produced
  // during think, then the tool line (tools execute after think). The tool
  // line used to sit in the thinking note above the text, reading backwards
  // for replies that announce the tool call ("để mình check...").
  for (const { segment, thought } of blocks) {
    if (thought) article.append(thought.note);
    if (typeof segment.content === "string" && segment.content.trim()) {
      const body = document.createElement("div");
      body.className = "live-copy markdown-body";
      body.innerHTML = formatMarkdown(segment.content);
      article.append(body);
    }
    const row = usageRow(segment.names || []);
    if (row) article.append(row);
  }
  const footer = document.createElement("footer");
  footer.append(againButton());
  if (ts) {
    const time = document.createElement("time");
    time.dateTime = String(ts);
    time.textContent = formatTime(ts);
    footer.append(time);
  }
  article.append(footer);
  return article;
}

// A round that thought nothing and went straight to tools renders as a bare
// "Đã dùng" shell — noise. Fold its tool names into the previous segment's
// line when nothing (no content) separates the two, so consecutive tool
// usage collapses into one cumulative line on the thinking that preceded it.
function foldToolOnlyParts(parts) {
  for (let i = 1; i < parts.length; i++) {
    const part = parts[i];
    const previous = parts[i - 1];
    if (part.reasoning || !previous) continue;
    if (String(previous.content || "").trim()) continue;
    previous.names.push(...part.names);
    part.names = [];
  }
}

export function renderConversation(root, messages) {
  const nodes = [];
  const pendingNames = [];
  const pendingParts = [];
  let pendingTs = "";

  const flushAssistant = () => {
    if (!pendingParts.length) {
      pendingNames.length = 0;
      return;
    }
    foldToolOnlyParts(pendingParts);
    nodes.push(assistantMessage(pendingParts.splice(0), pendingTs));
    pendingNames.length = 0;
    pendingTs = "";
  };

  for (const message of Array.isArray(messages) ? messages : []) {
    if (!message || message.role === "system") continue;
    if ((message.meta || {}).kind === "naming") continue;
    if (message.role === "user") {
      flushAssistant();
      nodes.push(userMessage(message));
      continue;
    }
    if (message.role !== "assistant") continue;
    for (const call of message.tool_calls || []) {
      if (!call) continue;
      // A skill load arrives tagged by the backend so replay keeps the skill
      // name instead of the bare `read` that carried it.
      if (typeof call.skill === "string" && call.skill) pendingNames.push(call.skill);
      else if (call.name) pendingNames.push(call.name);
    }
    const reasoning = typeof message.reasoning === "string" ? message.reasoning : "";
    const content = typeof message.content === "string" ? message.content : "";
    if (reasoning || content.trim()) {
      const latency = Number((message.meta || {}).latency_ms);
      pendingParts.push({
        content,
        names: pendingNames.splice(0),
        reasoning,
        latencyMs: Number.isFinite(latency) && latency >= 0 ? latency : null,
      });
      pendingTs = message.ts || pendingTs;
    }
  }
  flushAssistant();
  const last = nodes[nodes.length - 1];
  if (last?.classList.contains("message-user")) {
    last.querySelector("footer")?.prepend(againButton());
  }
  root.replaceChildren(...nodes);
  return nodes.length;
}

export function renderEmpty(root, title = "Bắt đầu một trang mới", note = "Nói điều đầu tiên để mở phiên trò chuyện.") {
  const empty = document.createElement("div");
  empty.className = "conversation-empty";
  const mark = document.createElement("span");
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = "+";
  const heading = document.createElement("h2");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = note;
  empty.append(mark, heading, copy);
  root.replaceChildren(empty);
}

export function renderError(root, message, retry) {
  const box = document.createElement("div");
  box.className = "backend-state is-error";
  box.setAttribute("role", "alert");
  const heading = document.createElement("strong");
  heading.textContent = "Không tải được cuộc trò chuyện";
  const copy = document.createElement("p");
  copy.textContent = message;
  box.append(heading, copy);
  if (typeof retry === "function") {
    const button = document.createElement("button");
    button.className = "screen-button";
    button.type = "button";
    button.textContent = "Thử lại";
    button.addEventListener("click", retry, { once: true });
    box.append(button);
  }
  root.replaceChildren(box);
}

function againButton() {
  const again = document.createElement("button");
  again.type = "button";
  again.className = "again";
  again.setAttribute("aria-label", "Thử lại");
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  icon.innerHTML = '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M3 3v5h5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>';
  const againLabel = document.createElement("span");
  againLabel.textContent = "Thử lại";
  again.append(icon, againLabel);
  return again;
}
