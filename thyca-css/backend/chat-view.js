import { ambientLineForEvent } from "./chat-ambient.js";
import { collapseNames, statusTextForEvent } from "./chat-status.js";
import { formatTime } from "./format.js";
import { formatMarkdown } from "./markdown.js";

const AVATAR = `<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M24 38V13"/><path d="M24 20c-7-1-11-6-10-12M24 23c7-2 11-7 10-13M24 29c-6-1-10-5-9-10M24 34c5-1 8-4 8-9"/><path class="avatar-leaf" d="M18 17c-5 0-8-3-8-7 5 0 8 2 8 7ZM29 18c5-1 8-4 8-8-5 0-8 3-8 8ZM19 28c-5 0-8-3-8-7 5 0 8 2 8 7ZM29 32c5-1 8-4 8-8-5 0-8 3-8 8Z"/><circle cx="24" cy="12" r="2.2"/><circle cx="15" cy="21" r="1.5"/><circle cx="33" cy="24" r="1.5"/></svg>`;
const TERMINAL = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6.5 8 3.5 3.5L6.5 15M12.5 15h5"/></svg>`;

function assistantHeader(stamp, ambientText = "đã viết") {
  const header = document.createElement("header");
  header.className = "live-card-header";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = AVATAR;
  const copy = document.createElement("div");
  const heading = document.createElement("h2");
  heading.textContent = "Thyca";
  const ambient = document.createElement("p");
  ambient.className = "ambient";
  ambient.textContent = stamp ? `${ambientText} · ${formatTime(stamp)}` : ambientText;
  copy.append(heading, ambient);
  header.append(avatar, copy);
  return header;
}

function toolRow(names) {
  const unique = [...new Set((names || []).filter(Boolean).map(String))];
  if (!unique.length) return null;
  const footer = document.createElement("footer");
  footer.className = "tool-row";
  const icon = document.createElement("span");
  icon.className = "terminal-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.innerHTML = TERMINAL;
  const label = document.createElement("span");
  label.className = "tool-label";
  label.textContent = "Đã dùng";
  footer.append(icon, label);
  for (const name of unique) {
    const chip = document.createElement("span");
    chip.className = "tool-chip";
    const dot = document.createElement("i");
    dot.setAttribute("aria-hidden", "true");
    chip.append(dot, document.createTextNode(name));
    footer.append(chip);
  }
  return footer;
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
  const mark = document.createElement("span");
  mark.className = "read-mark";
  mark.setAttribute("aria-label", "Đã gửi");
  mark.textContent = "✓✓";
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
  article.append(body);
  const row = toolRow(tools);
  if (row) article.append(row);
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
  const body = document.createElement("p");
  body.className = "live-copy status-copy";
  body.textContent = "Đang chờ Thyca…";
  const details = document.createElement("details");
  details.className = "thinking";
  details.open = true;
  details.innerHTML = `<summary><span class="thinking-title"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 20h4L19.5 9.5a2.1 2.1 0 0 0-3-3L6 17Z"/><path d="m14 8 2.5 2.5"/></svg><span>Tiến trình</span></span><svg class="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg></summary>`;
  const thinkingBody = document.createElement("div");
  thinkingBody.className = "thinking-body";
  const events = document.createElement("ul");
  const first = document.createElement("li");
  first.textContent = "Đã nhận tin nhắn.";
  events.append(first);
  thinkingBody.append(events);
  details.append(thinkingBody);
  article.append(header, body, details);
  root.append(article);
  return { article, ambient: header.querySelector(".ambient"), body, events, tools: [] };
}

export function updateLiveStatus(live, event) {
  const status = statusTextForEvent(event);
  if (status) live.body.textContent = status;
  live.ambient.textContent = ambientLineForEvent(event);
  if (event?.type === "tool.started" || event?.type === "skill.started") {
    live.tools.push(event.name || (event.type === "skill.started" ? "skill" : "tool"));
  }
  if (status && live.events.lastElementChild?.textContent !== status) {
    const item = document.createElement("li");
    item.textContent = status;
    live.events.append(item);
    while (live.events.children.length > 6) live.events.firstElementChild.remove();
  }
  const existing = live.article.querySelector(".tool-row");
  const next = toolRow(live.tools);
  if (existing) existing.remove();
  if (next) live.article.append(next);
  if (event?.type === "turn.failed") live.article.classList.add("is-error");
  const summary = collapseNames(live.tools);
  if (summary) live.article.dataset.tools = summary;
}
