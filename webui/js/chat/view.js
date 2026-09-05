import { el } from "../shared/dom.js";
import { formatMarkdown } from "../shared/markdown.js";
import { escapeHtml } from "../shared/util.js";
import { state } from "../shared/state.js";

export const EMPTY_BODY =
  '<div class="new-page-empty"><span aria-hidden="true">+</span><p>Chưa có tin nào.</p><small>Nói điều đầu tiên để mở phiên.</small></div>';
export const LOAD_ERROR_BODY =
  '<div class="new-page-empty"><span aria-hidden="true">!</span><p>Không tải được phiên này.</p><small>Kiểm tra mạng rồi bấm lại tab.</small></div>';

export function threadHtml(messages) {
  const parts = [];
  for (const message of messages) {
    if (!message || message.role === "system" || message.role === "tool") continue;
    if (message.role === "assistant" && message.tool_calls?.length && !message.content) continue;
    // meta-only messages (kind: "naming") carry no chat content — never a bubble
    if (message.role === "assistant" && !message.content && !message.tool_calls?.length) continue;
    if (message.role === "user" || message.role === "assistant") {
      parts.push(entryHtml(message.role, message.content || ""));
    }
  }
  if (!parts.length) return EMPTY_BODY;
  return `<div class="entry-list">${parts.join("")}</div>`;
}

export function statusHtml(ambient = "") {
  return `<article class="entry entry-thyca entry-status" aria-label="Thyca đang nghĩ" aria-live="off">
      <div class="entry-thyca-head"><time>thyca</time><p class="status-ambient">${escapeHtml(ambient)}</p></div>
    </article>`;
}

export function slideStatus(ticker, next) {
  const outgoing = ticker.querySelector(".status-line:not(.is-out)");
  const incoming = document.createElement("span");
  incoming.className = "status-line is-in";
  incoming.textContent = next;
  ticker.append(incoming);
  if (outgoing) outgoing.classList.add("is-out");
  window.setTimeout(() => {
    if (outgoing) outgoing.remove();
    incoming.classList.remove("is-in");
  }, 200);
}

export function scrollThread() {
  if (!el.notebook) return;
  // Đợi layout ổn định (font/ảnh/markdown) rồi mới scroll,
  // nếu không scrollHeight đo sớm sẽ hụt. Rọi lại 1 nhịp sau 300ms.
  const doScroll = () => {
    if (!el.notebook || !el.notebook.isConnected) return;
    el.notebook.scrollTo({
      top: el.notebook.scrollHeight,
      behavior: reduceMotion() ? "auto" : "smooth",
    });
    updateToBottomVisibility();
  };
  const settle = () => {
    doScroll();
    window.setTimeout(() => {
      if (!isNearBottom()) doScroll();
    }, 300);
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => requestAnimationFrame(settle));
  } else {
    settle();
  }
}

const TO_BOTTOM_PX = 200;
let toBottomBound = false;

export function isNearBottom(margin = TO_BOTTOM_PX) {
  if (!el.notebook) return true;
  const distance = el.notebook.scrollHeight - el.notebook.scrollTop - el.notebook.clientHeight;
  return distance <= margin;
}

export function updateToBottomVisibility() {
  const button = el.toBottom;
  if (!button || !el.notebook) return;
  const scrollable = el.notebook.scrollHeight > el.notebook.clientHeight + TO_BOTTOM_PX;
  const show = state.activeMode === "chat" && scrollable && !isNearBottom();
  button.hidden = !show;
}

export function initToBottom() {
  if (toBottomBound || !el.notebook || !el.toBottom) return;
  toBottomBound = true;
  el.notebook.addEventListener("scroll", () => updateToBottomVisibility(), { passive: true });
  el.toBottom.addEventListener("click", () => scrollThread());
}

export function reduceMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function entryHtml(role, content) {
  const cls = role === "user" ? "entry-user" : "entry-thyca";
  const stamp =
    role === "assistant" ? "<time>thyca</time>" : "";
  return `<article class="entry ${cls}">${stamp}<div class="entry-copy">${formatMarkdown(content)}</div></article>`;
}
