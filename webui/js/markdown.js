import { Marked } from "../vendor/marked.esm.js";
import { escapeHtml } from "./memories.js";

const SAFE_HREF = /^(https?:|mailto:)/i;

function safeHref(href) {
  const value = String(href || "").trim();
  return SAFE_HREF.test(value) ? value : "";
}

const marked = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    html({ text }) {
      return escapeHtml(text);
    },
    link({ href, title, tokens }) {
      const body = this.parser.parseInline(tokens);
      const safe = safeHref(href);
      if (!safe) return body;
      const extra = title ? ` title="${escapeHtml(title)}"` : "";
      return `<a href="${escapeHtml(safe)}" rel="noreferrer"${extra}>${body}</a>`;
    },
    image({ href, title, text }) {
      const safe = safeHref(href);
      if (!safe) return escapeHtml(text || "");
      const extra = title ? ` title="${escapeHtml(title)}"` : "";
      return `<img src="${escapeHtml(safe)}" alt="${escapeHtml(text || "")}"${extra}>`;
    },
  },
  hooks: {
    postprocess(html) {
      return html.replace(/<table>[\s\S]*?<\/table>/g, (block) => `<div class="md-table-wrap">${block}</div>`);
    },
  },
});

export function formatMarkdown(src) {
  const text = String(src ?? "");
  if (!text.trim()) return "";
  return marked.parse(text, { async: false });
}
