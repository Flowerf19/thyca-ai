export function pagesFromStats(stats) {
  return [
    overviewPage(stats),
    ...filePages(stats.leaves || []),
    ...canonicalPages(stats.files || []),
  ];
}

export function overviewPage(stats) {
  const total = Number(stats.total) || 0;
  const used = Number(stats.used) || 0;
  const unused = Number(stats.unused) || 0;
  const pct = total ? Math.round((used / total) * 100) : 0;
  const suggest = stats.suggest_removal || [];
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
        ${suggestBlock(suggest)}
      </div>`,
  };
}

function filePages(leaves) {
  const groups = new Map();
  for (const leaf of leaves) {
    const key = fileKey(leaf);
    const list = groups.get(key);
    if (list) list.push(leaf);
    else groups.set(key, [leaf]);
  }
  return sortFileKeys([...groups.keys()]).map((key) => filePage(key, groups.get(key)));
}

function filePage(key, leaves) {
  const used = leaves.filter((leaf) => Number(leaf.get_count) > 0).length;
  const today = leaves.some((leaf) => leaf.is_today);
  const tag = key === "MEMORY.md" ? "memory" : today ? "today" : "daily";
  const entries = leaves
    .slice()
    .sort((a, b) => splitHeading(a).time.localeCompare(splitHeading(b).time) || a.chunk_id.localeCompare(b.chunk_id))
    .map(leafEntry)
    .join("");
  return {
    title: escapeHtml(key),
    date: escapeHtml(`${leaves.length} leaf · ${used} đã get`),
    tag,
    tone: "memories",
    kicker: escapeHtml(key),
    body: `<div class="book-reading">
        <div class="book-meta">
          <span class="book-author">markdown</span>
          <h2>${escapeHtml(key)}</h2>
          <p>${leaves.length} mem trong file này.</p>
        </div>
        <div class="mem-day-list">${entries}</div>
      </div>`,
  };
}

function canonicalPages(files) {
  return files
    .filter((file) => file && file.name)
    .map((file) => {
      const name = String(file.name);
      const content = String(file.content || "");
      return {
        title: escapeHtml(name),
        date: "inject cả file",
        tag: "canonical",
        tone: "memories",
        kicker: escapeHtml(name),
        body: `<div class="book-reading">
            <div class="book-meta">
              <span class="book-author">canonical · prompt</span>
              <h2>${escapeHtml(name)}</h2>
              <p>Nhét cả file mỗi lượt. Không đếm get.</p>
            </div>
            <pre class="mem-file">${escapeHtml(content.trim() || "(trống)")}</pre>
          </div>`,
      };
    });
}

function leafEntry(leaf) {
  const { time, topic } = splitHeading(leaf);
  const count = Number(leaf.get_count) || 0;
  const day = leaf.timeline_day || (leaf.is_today ? "hôm nay" : "");
  return `<article class="mem-entry">
      <h3>${escapeHtml(topic)}</h3>
      <blockquote class="quote-note">
        <p>${escapeHtml(leaf.snippet || "(trống)")}</p>
        <cite>${escapeHtml([time, day].filter(Boolean).join(" · ") || leaf.chunk_id)}</cite>
      </blockquote>
      <dl class="mem-facts">
        <dt>Giờ</dt><dd>${escapeHtml(time || "—")}</dd>
        <dt>Get</dt><dd>${count}${leaf.last_get_at ? ` · cuối ${escapeHtml(fmtTs(leaf.last_get_at))}` : ""}</dd>
        <dt>Hết hạn</dt><dd>${escapeHtml(fmtTs(leaf.expires_at) || "—")}</dd>
        <dt>Id</dt><dd>${escapeHtml(leaf.chunk_id || "")}</dd>
      </dl>
    </article>`;
}

function suggestBlock(rows) {
  if (!rows.length) {
    return `<div class="suggest-inline"><h3>Đề xuất loại bỏ</h3><p class="suggest-empty">Không có mem unused (trừ hôm nay).</p></div>`;
  }
  const items = rows
    .map((leaf) => {
      const { topic } = splitHeading(leaf);
      return `<li><strong>${escapeHtml(topic)}</strong><span>${escapeHtml(leaf.snippet || "")}</span><small>${escapeHtml(leafSource(leaf))}</small></li>`;
    })
    .join("");
  return `<div class="suggest-inline">
      <h3>Đề xuất loại bỏ</h3>
      <p>Chưa từng get, không gồm hôm nay. Chỉ gợi ý — không xóa từ đây.</p>
      <ul class="suggest-list">${items}</ul>
    </div>`;
}

function fileKey(leaf) {
  if (String(leaf.session_id || "").startsWith("memory#")) return "MEMORY.md";
  if (leaf.timeline_day) return `${leaf.timeline_day}.md`;
  return "unknown.md";
}

function sortFileKeys(keys) {
  return keys.sort((a, b) => {
    const dayA = a.match(/^(\d{4}-\d{2}-\d{2})\.md$/);
    const dayB = b.match(/^(\d{4}-\d{2}-\d{2})\.md$/);
    if (dayA && dayB) return dayB[1].localeCompare(dayA[1]);
    if (dayA) return -1;
    if (dayB) return 1;
    return a.localeCompare(b);
  });
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
