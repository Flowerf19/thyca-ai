// Leaf entry builders for the Memories overview (split from memories.js).
// Pure render helpers over /api/memory/stats payloads — no module state.

import { escapeHtml } from "../shared/util.js";

export const DAY_FILE = /^(\d{4}-\d{2}-\d{2})\.md$/;
const EXPIRE_CAP = 5;

export function expireBlock(rows) {
  const list = (rows || []).slice(0, EXPIRE_CAP);
  if (!list.length) return "";
  const items = list
    .map((leaf) => {
      const { topic } = splitHeading(leaf);
      const gets = Number(leaf.get_count) || 0;
      const searches = Number(leaf.search_count) || 0;
      return `<li><strong>${escapeHtml(topic)}</strong><small>${escapeHtml(leafSource(leaf))} · hết hạn ${escapeHtml(fmtTs(leaf.expires_at) || "—")} · get ${gets} / search ${searches}</small></li>`;
    })
    .join("");
  return `<div class="suggest-inline">
      <h3>Sắp hết hạn</h3>
      <p>Trong 14 ngày. Chỉ xem — không xóa từ đây.</p>
      <ul class="suggest-list">${items}</ul>
    </div>`;
}


export function leafEntry(leaf, reason = "") {
  const { time, topic } = splitHeading(leaf);
  const gets = Number(leaf.get_count) || 0;
  const searches = Number(leaf.search_count) || 0;
  const day = leaf.timeline_day || (leaf.is_today ? "hôm nay" : "");
  const chunkDay = chunkDate(leaf.chunk_id);
  const meta = [
    time,
    chunkDay || (day && day !== "hôm nay" ? day : ""),
    `get ${gets}`,
    `search ${searches}`,
    leaf.expires_at ? `hết hạn ${fmtTs(leaf.expires_at)}` : "",
  ].filter(Boolean).join(" · ");
  return `<article class="mem-entry" data-chunk-id="${escapeHtml(leaf.chunk_id || "")}" data-topic="${escapeHtml(topic)}" data-snippet="${escapeHtml(leaf.snippet || "")}">
      <h3>${escapeHtml(topic)}</h3>
      <blockquote class="quote-note">
        <p>${escapeHtml(leaf.snippet || "(trống)")}</p>
        <cite class="mem-entry-cite">${reason ? `<span>${escapeHtml(reason)}</span>` : ""}${meta ? `<span>${escapeHtml(meta)}</span>` : ""}</cite>
      </blockquote>
      <div class="mem-entry-actions">
        <button type="button" class="mem-reinforce" data-edit="${escapeHtml(leaf.session_id || "")}">Sửa</button>
        <button type="button" class="mem-forget" data-forget="${escapeHtml(leaf.session_id || "")}">Quên</button>
        <button type="button" class="mem-reinforce" data-reinforce="${escapeHtml(leaf.session_id || "")}">Gia hạn</button>
      </div>
    </article>`;
}

// chunk_id dạng "YYYY-MM-DD#hash#n" → "YYYY-MM-DD"
function chunkDate(chunkId) {
  const day = String(chunkId || "").split("#")[0];
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : "";
}

const RANK_CAP = 8;

export function rankLeaves(leaves, kind) {
  const list = (leaves || []).slice();
  if (kind === "get") {
    list.sort((a, b) => (Number(b.get_count) || 0) - (Number(a.get_count) || 0));
  } else if (kind === "search") {
    list.sort((a, b) => (Number(b.search_count) || 0) - (Number(a.search_count) || 0));
  } else {
    list.sort((a, b) => useScore(a) - useScore(b));
  }
  return list.slice(0, RANK_CAP);
}

function useScore(leaf) {
  return (Number(leaf.get_count) || 0) + (Number(leaf.search_count) || 0);
}

export function sortLeaves(leaves) {
  return leaves
    .slice()
    .sort((a, b) => splitHeading(a).time.localeCompare(splitHeading(b).time) || String(a.chunk_id).localeCompare(String(b.chunk_id)));
}

export function fileKey(leaf) {
  if (leaf.timeline_day) return `${leaf.timeline_day}.md`;
  return "unknown.md";
}

export function sortFileKeys(keys) {
  return keys.sort((a, b) => {
    const dayA = a.match(DAY_FILE);
    const dayB = b.match(DAY_FILE);
    if (dayA && dayB) return dayB[1].localeCompare(dayA[1]);
    if (dayA) return -1;
    if (dayB) return 1;
    return a.localeCompare(b);
  });
}

export function splitHeading(leaf) {
  const raw = String(leaf.heading || "").replace(/^##\s*/, "");
  const match = raw.match(/^(\d{2}:\d{2})\s*[—\-]\s+(.+)$/);
  if (match) return { time: match[1], topic: match[2] };
  return { time: "", topic: raw || String(leaf.chunk_id || "") };
}

function leafSource(leaf) {
  if (leaf.is_today) return `daily · hôm nay`;
  if (leaf.timeline_day) return `daily · ${leaf.timeline_day}`;
  return String(leaf.source_kind || "leaf");
}

function fmtTs(value) {
  if (!value) return "";
  const text = String(value);
  if (text.length >= 16 && text[10] === "T") return `${text.slice(0, 10)} ${text.slice(11, 16)} UTC`;
  return text;
}


