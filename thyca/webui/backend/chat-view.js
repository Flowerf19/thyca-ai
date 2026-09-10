import {
  brandForEvent,
  IDLE_TAGLINE,
  usageLine,
} from "./chat-status.js";
import { formatTime } from "./format.js";
import { formatMarkdown } from "./markdown.js";

function chatBrandHeader({ state = "idle", status = IDLE_TAGLINE } = {}) {
  const header = document.createElement("header");
  header.className = "chat-brand";
  header.dataset.state = state;
  const stripe = document.createElement("span");
  stripe.className = "chat-brand-stripe";
  stripe.setAttribute("aria-hidden", "true");
  const body = document.createElement("div");
  body.className = "chat-brand-body";
  const lockup = document.createElement("div");
  lockup.className = "chat-brand-lockup";
  const heading = document.createElement("h2");
  heading.textContent = "Thyca";
  const quill = document.createElement("span");
  quill.className = "chat-brand-quill";
  quill.setAttribute("aria-hidden", "true");
  lockup.append(heading, quill);
  const line = document.createElement("p");
  line.className = "chat-brand-line";
  const ink = document.createElement("span");
  ink.className = "chat-brand-ink";
  ink.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.className = "chat-brand-status";
  label.textContent = status;
  line.append(ink, label);
  body.append(lockup, line);
  header.append(stripe, body);
  return header;
}

function brandRoot(target) {
  if (!target) return { article: null, header: null };
  const article = target.article
    ?? (target.classList?.contains("live-status") ? target : null);
  const header = target.brand
    ?? article?.querySelector(".chat-brand")
    ?? target.querySelector?.(".chat-brand")
    ?? (target.classList?.contains("chat-brand") ? target : null);
  return { article, header };
}

export function setChatBrand(target, { state, status } = {}) {
  const { article, header } = brandRoot(target);
  if (!header) return;
  if (state) {
    header.dataset.state = state;
    if (state === "error") article?.classList.add("is-error");
  }
  if (status) {
    const label = header.querySelector(".chat-brand-status");
    if (label) label.textContent = status;
  }
}

function assistantHeader(stamp) {
  const status = stamp ? `đã viết · ${formatTime(stamp)}` : "đã viết";
  return chatBrandHeader({ state: "idle", status });
}

// One "what ran" line: skills and tools share it, formatted by usageLine().
// The live card omits emptyText; a settled transcript shows the placeholder.
function usageRow(completedNames, activeNames = [], emptyText = "") {
  const line = usageLine(completedNames, activeNames);
  const row = document.createElement("p");
  row.className = "usage-row";
  if (!line) {
    if (!emptyText) return null;
    row.classList.add("is-empty");
    row.textContent = emptyText;
    return row;
  }
  const label = document.createElement("span");
  label.className = "usage-label";
  label.textContent = line.label;
  const body = document.createElement("span");
  body.className = "usage-row-body";
  body.textContent = line.body;
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

function assistantMessage(segments, ts) {
  const article = document.createElement("article");
  article.className = "live-card message-assistant";
  article.append(assistantHeader(ts));
  let used = false;
  for (const segment of segments) {
    const body = document.createElement("div");
    body.className = "live-copy markdown-body";
    body.innerHTML = formatMarkdown(segment.content);
    article.append(body);
    if ((segment.names || []).length) {
      used = true;
      article.append(usageRow(segment.names));
    }
  }
  if (!used) article.append(usageRow([], [], "Phiên này không dùng tool nào"));
  return article;
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
    if (typeof message.content === "string" && message.content.trim()) {
      pendingParts.push({
        content: message.content,
        names: pendingNames.splice(0),
      });
      pendingTs = message.ts || pendingTs;
    }
  }
  flushAssistant();
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

export function createLiveStatus(root) {
  const article = document.createElement("article");
  article.className = "live-card live-status";
  article.setAttribute("aria-label", "Thyca đang trả lời");
  article.setAttribute("aria-live", "polite");
  const header = chatBrandHeader(brandForEvent({ type: "turn.accepted" }));
  article.append(header);
  root.append(article);
  return {
    article,
    brand: header,
    active: new Map(),
    completed: [],
  };
}

export function updateLiveStatus(live, event) {
  const brand = brandForEvent(event);
  setChatBrand(live, {
    state: brand.state,
    status: brand.status || undefined,
  });
  const starts = event?.type === "tool.started" || event?.type === "skill.started";
  const finishes = event?.type === "tool.finished" || event?.type === "skill.finished";
  const callKey = event?.call_id || `${event?.type}:${event?.name || "tool"}`;
  if (starts) {
    live.active.set(callKey, event.name || "tool");
  } else if (finishes) {
    const name = live.active.get(callKey) || event.name || "tool";
    live.active.delete(callKey);
    live.completed.push(name);
  }
  live.article.querySelectorAll(".usage-row").forEach((node) => node.remove());
  const next = usageRow(live.completed, [...live.active.values()]);
  if (next) live.article.append(next);
}
