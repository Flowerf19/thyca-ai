/* Shared markdown renderer (GFM) — exposes window.formatMarkdown.
   Same contract as the old UI: raw HTML is escaped, links/images only
   allow http(s)/mailto, tables get a scroll wrapper. */
import { Marked } from './vendor/marked.esm.js';
'use strict';

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const SAFE_HREF = /^(https?:|mailto:)/i;
const safeHref = href => SAFE_HREF.test(String(href || '').trim()) ? String(href).trim() : '';

const marked = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    html: ({ text }) => esc(text),
    link({ href, title, tokens }) {
      const body = this.parser.parseInline(tokens);
      const safe = safeHref(href);
      if (!safe) return body;
      return `<a href="${esc(safe)}" rel="noreferrer"${title ? ` title="${esc(title)}"` : ''}>${body}</a>`;
    },
    image({ href, title, text }) {
      const safe = safeHref(href);
      if (!safe) return esc(text || '');
      return `<img src="${esc(safe)}" alt="${esc(text || '')}"${title ? ` title="${esc(title)}"` : ''}>`;
    },
  },
  hooks: {
    postprocess: html => html.replace(/<table>[\s\S]*?<\/table>/g, t => `<div class="md-table-wrap">${t}</div>`),
  },
});

window.formatMarkdown = value => {
  const text = String(value ?? '');
  return text.trim() ? marked.parse(text, { async: false }) : '';
};
