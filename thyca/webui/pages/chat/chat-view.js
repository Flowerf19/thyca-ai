import { usageLine } from "./chat-status.js";
import { botMark } from "../../shared/js/bot-avatar.js";
import { chatBotForMessage } from "../../shared/js/chat-bot.js";
import { tintVarFor } from "./team-data.js";

// Brand + usage-row kit shared by the settled transcript and the live card.
// The implementations live in transcript.js (settled) and live-status.js
// (live); this module keeps the shared builders plus compat re-exports so
// existing `chat-view.js` imports keep working.
export { renderConversation, renderEmpty, renderError } from "./transcript.js";
export { createLiveStatus, resetLiveStatus, updateLiveStatus } from "./live-status.js";
export { chatBotForMessage, chatBotForTurn } from "../../shared/js/chat-bot.js";

const BRAND_AVATAR_STATE = { busy: "thinking", idle: "done", error: "error" };

function brandAvatarProfile(bot, state) {
  const profile = bot && typeof bot === "object"
    ? bot
    : chatBotForMessage(null);
  const mapped = BRAND_AVATAR_STATE[String(state || "")];
  if (!mapped) return profile;
  return { ...profile, status: "running", agentStatus: { state: mapped } };
}

export function chatBrandHeader({ state = "idle", status = "đã viết", expandable = false, name = "", bot = null } = {}) {
  const profile = bot && typeof bot === "object" ? bot : chatBotForMessage(null);
  const avatarBot = brandAvatarProfile(profile, state);
  const header = document.createElement("header");
  header.className = "chat-brand";
  header.style.setProperty("--avatar-tint", tintVarFor(profile.id));
  header.style.setProperty("--team-tint", tintVarFor(profile.id));
  header.style.setProperty(
    "--chat-brand-accent",
    "var(--avatar-tint, var(--team-tint, var(--color-accent)))");
  header.dataset.state = state;
  header._botProfile = profile;
  const stripe = document.createElement("span");
  stripe.className = "chat-brand-stripe";
  stripe.setAttribute("aria-hidden", "true");
  const body = document.createElement("div");
  body.className = "chat-brand-body";
  const lockup = document.createElement("div");
  lockup.className = "chat-brand-lockup";
  const avatar = document.createElement("span");
  avatar.className = "chat-brand-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = botMark(avatarBot);
  const heading = document.createElement("h2");
  heading.textContent = name || profile.name || "Thyca";
  lockup.append(avatar, heading);
  const line = document.createElement(expandable ? "button" : "p");
  line.className = "chat-brand-line";
  if (expandable) line.type = "button";
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
    const slot = header.querySelector(".chat-brand-avatar");
    if (slot) {
      const profile = brandAvatarProfile(header._botProfile, state);
      slot.innerHTML = botMark(profile);
    }
  }
  if (status) {
    const label = header.querySelector(".chat-brand-status");
    if (label) label.textContent = status;
  }
}

// One "what ran" line: skills and tools share it, formatted by usageLine().
// Nothing runs → no line at all (the assistant's own prose stands alone).
export function usageRowSkeleton() {
  const row = document.createElement("p");
  row.className = "usage-row";
  const label = document.createElement("span");
  label.className = "usage-label";
  const body = document.createElement("span");
  body.className = "usage-row-body";
  row.append(label, body);
  return row;
}

export function fillUsageRow(row, completedNames, activeNames = []) {
  const line = usageLine(completedNames, activeNames);
  if (!line) return false;
  row.querySelector(".usage-label").textContent = line.label;
  row.querySelector(".usage-row-body").textContent = line.body;
  return true;
}

export function usageRow(completedNames, activeNames = []) {
  const row = usageRowSkeleton();
  return fillUsageRow(row, completedNames, activeNames) ? row : null;
}
