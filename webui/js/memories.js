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
  const { time, topic } = splitHeading(leaf);
  const count = Number(leaf.get_count) || 0;
  const tag = leaf.is_today ? "today" : count ? "used" : "unused";
  const source = leafSource(leaf);
  const day = leaf.timeline_day || (leaf.is_today ? "hôm nay" : "");
  return {
    title: escapeHtml(topic),
    date: escapeHtml([day, count ? `${count} lần get` : "chưa get"].filter(Boolean).join(" · ")),
    tag,
    tone: "memories",
    kicker: escapeHtml(source),
    body: `<div class="book-reading">
        <div class="book-meta">
          <span class="book-author">${escapeHtml(source)}</span>
          <h2>${escapeHtml(topic)}</h2>
        </div>
        <blockquote class="quote-note">
          <p>${escapeHtml(leaf.snippet || "(trống)")}</p>
          <cite>${escapeHtml([time, day].filter(Boolean).join(" · ") || leaf.chunk_id)}</cite>
        </blockquote>
        <dl class="mem-facts">
          <dt>Nguồn</dt><dd>${escapeHtml(source)}</dd>
          <dt>Ngày</dt><dd>${escapeHtml(day || "—")}</dd>
          <dt>Giờ</dt><dd>${escapeHtml(time || "—")}</dd>
          <dt>Get</dt><dd>${count}${leaf.last_get_at ? ` · cuối ${escapeHtml(fmtTs(leaf.last_get_at))}` : ""}</dd>
          <dt>Hết hạn</dt><dd>${escapeHtml(fmtTs(leaf.expires_at) || "—")}</dd>
          <dt>Id</dt><dd>${escapeHtml(leaf.chunk_id || "")}</dd>
        </dl>
      </div>`,
  };
}

function suggestPage(rows) {
  const items = rows
    .map((leaf) => {
      const { topic } = splitHeading(leaf);
      return `<li><strong>${escapeHtml(topic)}</strong><span>${escapeHtml(leaf.snippet || "")}</span><small>${escapeHtml(leafSource(leaf))}</small></li>`;
    })
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

function splitHeading(leaf) {
  const raw = String(leaf.heading || "").replace(/^##\s*/, "");
  const match = raw.match(/^(\d{2}:\d{2})\s*[—\-]\s+(.+)$/);
  if (match) return { time: match[1], topic: match[2] };
  return { time: "", topic: raw || String(leaf.chunk_id || "") };
}

function leafSource(leaf) {
  if (String(leaf.session_id || "").startsWith("memory#")) return "MEMORY.md";
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

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
