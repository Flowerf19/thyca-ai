import {
  brandForEvent,
  collapseNames,
  IDLE_TAGLINE,
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

function displayToolName(rawName) {
  const name = String(rawName || "tool");
  return name.startsWith("memory_") ? "memories" : name;
}

function tally(names, normalize) {
  const counts = new Map();
  for (const rawName of names || []) {
    if (!rawName) continue;
    const name = normalize(rawName);
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return counts;
}

// One "what ran" line. Skills and tools are different registers, so each
// gets its own row: a skill load must never read as a tool call.
function activityRow(completedNames, activeNames, shape) {
  const completed = tally(completedNames, shape.normalize);
  const active = tally(activeNames, shape.normalize);
  const names = [...new Set([...completed.keys(), ...active.keys()])];
  const row = document.createElement("p");
  row.className = shape.rowClass;
  if (!names.length) {
    if (!shape.emptyText) return null;
    row.classList.add("is-empty");
    row.textContent = shape.emptyText;
    return row;
  }
  const label = document.createElement("span");
  label.className = shape.labelClass;
  label.textContent = active.size ? shape.activeLabel : shape.doneLabel;
  const body = document.createElement("span");
  body.className = shape.bodyClass;
  body.textContent = names.map((name) => {
    const count = (completed.get(name) || 0) + (active.get(name) || 0);
    return `${name} x${count}`;
  }).join(", ");
  row.append(label, body);
  return row;
}

function toolRow(completedNames, activeNames = [], emptyText = "") {
  return activityRow(completedNames, activeNames, {
    normalize: displayToolName,
    rowClass: "tool-row",
    labelClass: "tool-label",
    bodyClass: "tool-row-body",
    activeLabel: "Tool đang dùng:",
    doneLabel: "Tool đã dùng:",
    emptyText,
  });
}

function skillRow(completedNames, activeNames = []) {
  return activityRow(completedNames, activeNames, {
    normalize: (name) => String(name),
    rowClass: "skill-row",
    labelClass: "skill-label",
    bodyClass: "skill-row-body",
    activeLabel: "Skill đang mở:",
    doneLabel: "Skill đã mở:",
  });
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
  let usedTools = false;
  let usedSkills = false;
  for (const segment of segments) {
    const body = document.createElement("div");
    body.className = "live-copy markdown-body";
    body.innerHTML = formatMarkdown(segment.content);
    article.append(body);
    if ((segment.skills || []).length) {
      usedSkills = true;
      article.append(skillRow(segment.skills));
    }
    if ((segment.tools || []).length) {
      usedTools = true;
      article.append(toolRow(segment.tools));
    }
  }
  if (!usedTools && !usedSkills) {
    article.append(toolRow([], [], "Phiên này không dùng tool nào"));
  }
  return article;
}

export function renderConversation(root, messages) {
  const nodes = [];
  const pendingTools = [];
  const pendingSkills = [];
  const pendingParts = [];
  let pendingTs = "";

  const flushAssistant = () => {
    if (!pendingParts.length) {
      pendingTools.length = 0;
      pendingSkills.length = 0;
      return;
    }
    nodes.push(assistantMessage(pendingParts.splice(0), pendingTs));
    pendingTools.length = 0;
    pendingSkills.length = 0;
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
      // A skill load arrives tagged by the backend, so replay labels it as a
      // skill instead of the bare `read` that carried it.
      if (typeof call.skill === "string" && call.skill) pendingSkills.push(call.skill);
      else if (call.name) pendingTools.push(call.name);
    }
    if (typeof message.content === "string" && message.content.trim()) {
      pendingParts.push({
        content: message.content,
        tools: pendingTools.splice(0),
        skills: pendingSkills.splice(0),
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
    activeTools: new Map(),
    completedTools: [],
    activeSkills: new Map(),
    completedSkills: [],
  };
}

export function updateLiveStatus(live, event) {
  const brand = brandForEvent(event);
  setChatBrand(live, {
    state: brand.state,
    status: brand.status || undefined,
  });
  const isSkill = event?.type === "skill.started" || event?.type === "skill.finished";
  const startsTool = event?.type === "tool.started" || isSkill;
  const finishesTool = event?.type === "tool.finished" || isSkill;
  const callKey = event?.call_id || `${event?.type}:${event?.name || "tool"}`;
  const active = isSkill ? live.activeSkills : live.activeTools;
  const completed = isSkill ? live.completedSkills : live.completedTools;
  if (startsTool) {
    active.set(callKey, event.name || (isSkill ? "skill" : "tool"));
  } else if (finishesTool) {
    const name = active.get(callKey) || event.name || (isSkill ? "skill" : "tool");
    active.delete(callKey);
    completed.push(name);
  }
  live.article.querySelectorAll(".skill-row, .tool-row").forEach((node) => node.remove());
  const skills = skillRow(live.completedSkills, [...live.activeSkills.values()]);
  const tools = toolRow(live.completedTools, [...live.activeTools.values()]);
  if (skills) live.article.append(skills);
  if (tools) live.article.append(tools);
  const summary = collapseNames([
    ...live.completedTools,
    ...live.activeTools.values(),
  ]);
  if (summary) live.article.dataset.tools = summary;
  const skillSummary = collapseNames([
    ...live.completedSkills,
    ...live.activeSkills.values(),
  ]);
  if (skillSummary) live.article.dataset.skills = skillSummary;
}
