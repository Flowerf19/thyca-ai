import { ambientLineForEvent } from "./chat-ambient.js";
import { collapseNames, statusTextForEvent } from "./chat-status.js";
import { formatTime } from "./format.js";
import { formatMarkdown } from "./markdown.js";

const PENCIL = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 20h4L19.5 9.5a2.1 2.1 0 0 0-3-3L6 17Z"/><path d="m14 8 2.5 2.5"/></svg>`;
function setAmbientText(ambient, text) {
  const label = ambient.querySelector(".ambient-label");
  if (label) {
    label.textContent = text;
  } else {
    ambient.textContent = text;
  }
}

function assistantHeader(stamp, ambientText = "đã viết") {
  const header = document.createElement("header");
  header.className = "live-card-header";
  const heading = document.createElement("h2");
  heading.textContent = "Thyca";
  const ambient = document.createElement("p");
  ambient.className = "ambient";
  const icon = document.createElement("span");
  icon.className = "ambient-icon";
  icon.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.className = "ambient-label";
  label.textContent = stamp ? `${ambientText} · ${formatTime(stamp)}` : ambientText;
  ambient.append(icon, label);
  header.append(heading, ambient);
  return header;
}

function displayToolName(rawName) {
  const name = String(rawName || "tool");
  return name.startsWith("memory_") ? "memories" : name;
}

function countTools(names) {
  const counts = new Map();
  for (const rawName of names || []) {
    if (!rawName) continue;
    const name = displayToolName(rawName);
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return counts;
}

function toolRow(completedNames, activeNames = []) {
  const completed = countTools(completedNames);
  const active = countTools(activeNames);
  const names = [...new Set([...completed.keys(), ...active.keys()])];
  if (!names.length) {
    const empty = document.createElement("p");
    empty.className = "tool-row is-empty";
    empty.textContent = "Phiên này không dùng tool nào";
    return empty;
  }
  const row = document.createElement("p");
  row.className = "tool-row";
  const label = document.createElement("span");
  label.className = "tool-label";
  label.textContent = active.size ? "Tool đang dùng:" : "Tool đã dùng:";
  const body = document.createElement("span");
  body.className = "tool-row-body";
  body.textContent = names.map((name) => {
    const count = (completed.get(name) || 0) + (active.get(name) || 0);
    return `${name} x${count}`;
  }).join(", ");
  row.append(label, body);
  return row;
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

function assistantMessage(message, tools) {
  const article = document.createElement("article");
  article.className = "live-card message-assistant";
  article.append(assistantHeader(message.ts));
  const body = document.createElement("div");
  body.className = "live-copy markdown-body";
  body.innerHTML = formatMarkdown(message.content);
  article.append(body, toolRow(tools));
  return article;
}

export function renderConversation(root, messages) {
  const nodes = [];
  const pendingTools = [];
  for (const message of Array.isArray(messages) ? messages : []) {
    if (!message || message.role === "system") continue;
    if (message.role === "user") {
      nodes.push(userMessage(message));
      continue;
    }
    if (message.role !== "assistant") continue;
    for (const call of message.tool_calls || []) {
      if (call && call.name) pendingTools.push(call.name);
    }
    if (typeof message.content === "string" && message.content.trim()) {
      nodes.push(assistantMessage(message, pendingTools));
      pendingTools.length = 0;
    }
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

export function createLiveStatus(root) {
  const article = document.createElement("article");
  article.className = "live-card live-status";
  article.setAttribute("aria-label", "Thyca đang trả lời");
  article.setAttribute("aria-live", "polite");
  const header = assistantHeader("", ambientLineForEvent(null));
  const details = document.createElement("details");
  details.className = "thinking";
  details.open = true;
  details.innerHTML = `<summary><span class="thinking-title">${PENCIL}<span>Tiến trình</span></span><svg class="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg></summary>`;
  const thinkingBody = document.createElement("div");
  thinkingBody.className = "thinking-body";
  const events = document.createElement("ul");
  const first = document.createElement("li");
  first.textContent = "Đã nhận tin nhắn.";
  events.append(first);
  thinkingBody.append(events);
  details.append(thinkingBody);
  article.append(header, details);
  root.append(article);
  return {
    article,
    ambient: header.querySelector(".ambient"),
    events,
    activeTools: new Map(),
    completedTools: [],
  };
}

export function updateLiveStatus(live, event) {
  const status = statusTextForEvent(event);
  setAmbientText(live.ambient, ambientLineForEvent(event));
  const startsTool = event?.type === "tool.started" || event?.type === "skill.started";
  const finishesTool = event?.type === "tool.finished" || event?.type === "skill.finished";
  const callKey = event?.call_id || `${event?.type}:${event?.name || "tool"}`;
  if (startsTool) {
    live.activeTools.set(callKey, event.name || (event.type === "skill.started" ? "skill" : "tool"));
  } else if (finishesTool) {
    const name = live.activeTools.get(callKey) || event.name || "tool";
    live.activeTools.delete(callKey);
    live.completedTools.push(name);
  }
  if (status && live.events.lastElementChild?.textContent !== status) {
    const item = document.createElement("li");
    item.textContent = status;
    live.events.append(item);
    while (live.events.children.length > 6) live.events.firstElementChild.remove();
  }
  const existing = live.article.querySelector(".tool-row");
  const next = toolRow(live.completedTools, [...live.activeTools.values()]);
  if (existing) existing.remove();
  if (next && !next.classList.contains("is-empty")) live.article.append(next);
  if (event?.type === "turn.failed") live.article.classList.add("is-error");
  const summary = collapseNames([
    ...live.completedTools,
    ...live.activeTools.values(),
  ]);
  if (summary) live.article.dataset.tools = summary;
}
