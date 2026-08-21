import { personaPages } from "./data.js";

export function pagesFromStats(stats) {
  const pages = [overviewPage(stats), ...stats.leaves.map(leafPage)];
  if (stats.suggest_removal.length) {
    pages.push(suggestPage(stats.suggest_removal));
  }
  pages.push(...personaPages);
  return pages;
}

export function overviewPage(stats) {
  const total = Number(stats.total) || 0;
  const used = Number(stats.used) || 0;
  const unused = Number(stats.unused) || 0;
  const pct = total ? Math.round((used / total) * 100) : 0;
  return {
    title: "Tổng quan",
    date: `${total} leaf`,
    tag: "stats",
    tone: "memories",
    kicker: "leaf · chỉ đếm get",
    body: `<div class="book-reading">
        <div class="book-meta">
          <span class="book-author">L2 · memory_get</span>
          <h2>Tổng quan</h2>
          <p>Search và inject nóng không tính. Không xóa từ đây.</p>
        </div>
        <div class="stat-row">
          <div><strong>${total}</strong><span>tổng</span></div>
          <div><strong>${used}</strong><span>đã get</span></div>
          <div><strong>${unused}</strong><span>chưa get</span></div>
        </div>
        <div class="progress-label"><span>Đã get</span><strong>${used} / ${total}</strong></div>
        <div class="reading-progress"><span style="width:${pct}%"></span></div>
      </div>`,
  };
}

function leafPage(leaf) {
  const heading = leafTitle(leaf);
  const count = Number(leaf.get_count) || 0;
  const tag = leaf.is_today ? "today" : count ? "used" : "unused";
  return {
    title: heading,
    date: count ? `${count} lần get` : "chưa get",
    tag,
    tone: "memories",
    kicker: escapeHtml(leaf.chunk_id || ""),
    body: `<div class="book-reading">
        <div class="book-meta">
          <span class="book-author">${escapeHtml(leaf.session_id || "")}</span>
          <h2>${escapeHtml(heading)}</h2>
          <p>${escapeHtml(leaf.snippet || "")}</p>
          <div class="progress-label"><span>Get</span><strong>${count}</strong></div>
          <div class="reading-progress${count ? " reading-progress-complete" : ""}"><span></span></div>
        </div>
      </div>`,
  };
}

function suggestPage(rows) {
  const items = rows
    .map(
      (leaf) =>
        `<li><strong>${escapeHtml(leafTitle(leaf))}</strong><small>${escapeHtml(leaf.chunk_id)}</small></li>`,
    )
    .join("");
  return {
    title: "Đề xuất loại bỏ",
    date: `${rows.length} unused`,
    tag: "suggest",
    tone: "memories",
    kicker: "unused · không gồm hôm nay",
    body: `<div class="book-reading">
        <div class="book-meta">
          <span class="book-author">Chưa từng get</span>
          <h2>Đề xuất loại bỏ</h2>
          <p>Chỉ gợi ý. Xóa bằng <code>memory_forget</code>, không phải từ trang này.</p>
        </div>
        <ul class="suggest-list">${items}</ul>
      </div>`,
  };
}

function leafTitle(leaf) {
  const raw = String(leaf.heading || leaf.chunk_id || "");
  return raw.replace(/^##\s*/, "");
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
