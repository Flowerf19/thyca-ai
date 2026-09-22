import { usageLine } from "./chat-status.js";

// Brand + usage-row kit shared by the settled transcript and the live card.
// The implementations live in transcript.js (settled) and live-status.js
// (live); this module keeps the shared builders plus compat re-exports so
// existing `chat-view.js` imports keep working.
export { renderConversation, renderEmpty, renderError } from "./transcript.js";
export { createLiveStatus, resetLiveStatus, updateLiveStatus } from "./live-status.js";

export function chatBrandHeader({ state = "idle", status = "đã viết", expandable = false } = {}) {
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
