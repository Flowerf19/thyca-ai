import { formatMarkdown } from "../../shared/js/markdown.js";
import { formatTime } from "../../shared/js/format.js";
import { settledThinkingNote } from "./chat-thinking.js";
import { chatBrandHeader } from "./chat-view.js";
import { chatBotForMessage } from "../../shared/js/chat-bot.js";
import {
  TEAM_BOT_ID,
  isExchangeMessage,
  teamStatusLabel,
  tintVarFor,
} from "./team-data.js";

// Team transcript cards (r9 §4.3/4.5/4.7 + master §7 FE-2/FE-3). Every card
// renders only from contract meta on the message; without that meta the
// caller falls back to the legacy transcript path, so a plain session
// renders byte-identical to today.
//
// Accepted meta shapes: bot_id/kind/trace_id/parent_span_id ride on the
// frozen contract (§6); bot_name/bot_state/bot_tint are display-only
// companions. meta.card is emitted by the backend ask_user/report_error
// tools (contract extension landed 2026-10-14). meta.decision /
// meta.handoff remain a proposed extension. Without any of them the
// caller takes the legacy transcript path, so a plain session renders
// byte-identical.
// - {bot_id, bot_name?, bot_state?} on an assistant message → per-bot block
//   whose header IS the legacy .chat-brand anatomy (stripe, lockup with
//   h2 name + quill, italic status line) with per-bot tint wired through
//   the existing --chat-brand-accent variable — no parallel team CSS.
//   Thyca rows keep the legacy --chat-brand-ink name; other bots tint the
//   name via their tint var. The status line (bot_state or "đã viết") stays
//   the thinking toggle whenever the message carries reasoning.
// - {kind: delegate|step|status} → one exchange line inside the collapse.
// - {decision: string} → "Quyết định" plain line (r9: no note-paper box).
// - {handoff: string} → handoff plain line (no box).
// - {card: {type:"question", title, subtitle?, options:[{id,label}|string]}}
//   → question card; chip click sends the label as a normal turn. No
//   approve button, no "Tạo bot" button (r9 §4.5).
// - {card: {type:"error", progress:[{label,state}], choices:[...]}}
//   → "Làm dở tới đâu" checklist + "Làm tiếp thế nào?" chips.

export function botDisplayName(message) {
  const meta = message?.meta || {};
  if (meta.bot_name) return String(meta.bot_name);
  // The old header always read "Thyca": a bare thyca id (no display
  // companion from the backend) must not render lowercase at h2 scale.
  if (String(meta.bot_id || "") === TEAM_BOT_ID) return "Thyca";
  return String(meta.bot_id || "Thyca");
}

function markdownBody(text) {
  const body = document.createElement("div");
  body.className = "live-copy markdown-body";
  body.innerHTML = formatMarkdown(String(text || ""));
  return body;
}

function stamp(message) {
  if (!message?.ts) return null;
  const time = document.createElement("time");
  time.dateTime = String(message.ts);
  time.textContent = formatTime(message.ts);
  return time;
}

// One assistant message that carries a bot identity → per-bot article.
// Returns null when the message has no bot meta (legacy path owns it).
export function teamArticleFor(message, { onChoice, bot = null } = {}) {
  if (!message || message.role !== "assistant") return null;
  const meta = message.meta || {};
  const botId = typeof meta.bot_id === "string" && meta.bot_id ? meta.bot_id : "";
  const hasCard = meta.card && typeof meta.card === "object";
  const hasDecision = typeof meta.decision === "string" && meta.decision.trim();
  const hasHandoff = (typeof meta.handoff === "string" && meta.handoff.trim())
    || (meta.handoff && typeof meta.handoff.text === "string");
  const content = typeof message.content === "string" ? message.content.trim() : "";
  if (!botId && !hasCard && !hasDecision && !hasHandoff) return null;
  // Reasoning-only or tool-only bot traffic keeps the legacy rendering
  // (thinking toggle / usage line) instead of an empty name header.
  if (!content && !hasCard && !hasDecision && !hasHandoff) {
    if ((Array.isArray(message.tool_calls) && message.tool_calls.length)
      || (typeof message.reasoning === "string" && message.reasoning.trim())) return null;
  }

  const article = document.createElement("article");
  article.className = "live-card message-assistant team-message";
  const tint = tintVarFor(botId || TEAM_BOT_ID, meta.bot_tint);
  article.style.setProperty("--team-tint", tint);
  // The header reuses the legacy .chat-brand builder verbatim (same stripe,
  // lockup, quill, status-line nodes and classes as the old Thyca header);
  // only the name text and the tint variable differ per bot. Blocks without
  // a bot identity keep the previous header-less shape. The transcript
  // binds the thinking toggle onto .chat-brand-line when reasoning exists.
  if (botId) {
    const thought = typeof message.reasoning === "string" && message.reasoning.trim()
      ? settledThinkingNote(message.reasoning)
      : null;
    const profile = bot || chatBotForMessage(message);
    const head = chatBrandHeader({
      status: teamStatusLabel(meta),
      expandable: Boolean(thought),
      name: String(meta.bot_name || "") || profile.name || botDisplayName(message),
      bot: profile,
    });
    head.dataset.bot = botId;
    // Thyca keeps the legacy --chat-brand-ink name; other bots read in
    // their own tint, through the existing tint variable (no new color).
    article.append(head);
    if (thought) article.append(thought.note);
  }
  if (content) article.append(markdownBody(message.content));
  if (hasDecision) article.append(decisionLine(meta.decision));
  if (hasHandoff) {
    article.append(handoffLine(typeof meta.handoff === "string" ? meta.handoff : meta.handoff.text));
  }
  if (hasCard && meta.card.type === "question") {
    article.append(questionCard(meta.card, onChoice));
  } else if (hasCard && meta.card.type === "error") {
    article.append(errorCard(meta.card, onChoice));
  }
  const ts = stamp(message);
  if (ts) {
    const footer = document.createElement("footer");
    footer.append(ts);
    article.append(footer);
  }
  return article;
}

// "Quyết định" and handoff sit directly on the page — r9 removed the
// note-paper box (team-frontend §2.1 overrules the stale handoff doc).
export function decisionLine(text) {
  const line = document.createElement("p");
  line.className = "team-decision";
  const label = document.createElement("strong");
  label.textContent = "Quyết định — ";
  line.append(label, document.createTextNode(String(text || "").trim()));
  return line;
}

export function handoffLine(text) {
  const line = document.createElement("p");
  line.className = "team-handoff";
  line.textContent = String(text || "").trim();
  return line;
}

// Inter-bot exchange collapsed to one line; <details> expands it (r9 §4.3).
export function exchangeDetails(messages) {
  const list = (Array.isArray(messages) ? messages : []).filter(isExchangeMessage);
  const names = [...new Set(list.map((item) => String(item?.meta?.bot_id || "")).filter(Boolean))];
  const details = document.createElement("details");
  details.className = "team-exchange";
  const summary = document.createElement("summary");
  const lead = document.createElement("span");
  lead.className = "team-exchange-lead";
  lead.textContent = names.length
    ? `${names.join(" và ")} đã trao đổi ${list.length} tin · `
    : `Các bot đã trao đổi ${list.length} tin · `;
  const open = document.createElement("span");
  open.className = "team-exchange-toggle";
  open.textContent = "Xem các bot đã làm gì";
  summary.append(lead, open);
  details.append(summary);
  for (const item of list) {
    const row = document.createElement("div");
    row.className = "team-exchange-row";
    row.style.setProperty("--team-tint", tintVarFor(item?.meta?.bot_id, item?.meta?.bot_tint));
    const who = document.createElement("strong");
    who.textContent = botDisplayName(item);
    row.append(who);
    const body = typeof item.content === "string" && item.content.trim()
      ? markdownBody(item.content)
      : null;
    if (body) row.append(body);
    details.append(row);
  }
  details.addEventListener("toggle", () => {
    details.querySelector(".team-exchange-toggle").textContent = details.open
      ? "Thu gọn"
      : "Xem các bot đã làm gì";
  });
  return details;
}

function choiceChips(options, onChoice) {
  const wrap = document.createElement("div");
  wrap.className = "team-choices";
  for (const option of Array.isArray(options) ? options : []) {
    const label = typeof option === "string" ? option : String(option?.label || option?.id || "");
    if (!label.trim()) continue;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "screen-button team-choice";
    chip.textContent = label;
    chip.addEventListener("click", () => onChoice?.(label));
    wrap.append(chip);
  }
  return wrap;
}

export function questionCard(card, onChoice) {
  const section = document.createElement("section");
  section.className = "team-card team-question";
  section.setAttribute("aria-label", String(card.title || "Câu hỏi"));
  const title = document.createElement("h3");
  title.textContent = String(card.title || "Chọn một cách");
  section.append(title);
  if (typeof card.subtitle === "string" && card.subtitle.trim()) {
    const sub = document.createElement("p");
    sub.className = "team-card-sub";
    sub.textContent = card.subtitle.trim();
    section.append(sub);
  }
  section.append(choiceChips(card.options, onChoice));
  return section;
}

export function errorCard(card, onChoice) {
  const section = document.createElement("section");
  section.className = "team-card team-error-card";
  section.setAttribute("aria-label", "Bot gặp lỗi");
  const doneTitle = document.createElement("h3");
  doneTitle.textContent = "Làm dở tới đâu";
  section.append(doneTitle);
  const list = document.createElement("ul");
  list.className = "team-progress";
  for (const step of Array.isArray(card.progress) ? card.progress : []) {
    const label = typeof step === "string" ? step : String(step?.label || "");
    if (!label.trim()) continue;
    const state = typeof step === "object" ? String(step.state || "") : "";
    const row = document.createElement("li");
    row.className = `team-progress-row is-${state || "todo"}`;
    const mark = document.createElement("span");
    mark.className = "team-progress-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = state === "done" ? "✓" : state === "stuck" ? "!" : "○";
    row.append(mark, document.createTextNode(label));
    list.append(row);
  }
  section.append(list);
  const nextTitle = document.createElement("h3");
  nextTitle.textContent = "Làm tiếp thế nào?";
  section.append(nextTitle);
  if (typeof card.hint === "string" && card.hint.trim()) {
    const hint = document.createElement("p");
    hint.className = "team-card-sub";
    hint.textContent = card.hint.trim();
    section.append(hint);
  }
  section.append(choiceChips(card.choices || card.options, onChoice));
  return section;
}
