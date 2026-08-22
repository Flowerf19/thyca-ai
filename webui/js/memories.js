const DAY_FILE = /^(\d{4}-\d{2}-\d{2})\.md$/;
const SUGGEST_CAP = 8;
const EXPIRE_CAP = 5;

let openDay = "";

export function pagesFromStats(stats) {
  const leaves = stats.leaves || [];
  return [overviewPage(stats, leaves), ...memoryPages(leaves), ...canonicalPages(stats.files || [])];
}

export function bindOverview(root) {
  if (!root) return;
  root.querySelectorAll("details[data-day]").forEach((el) => {
    el.addEventListener("toggle", () => {
      if (el.open) {
        openDay = el.dataset.day || "";
        root.querySelectorAll("details[data-day]").forEach((other) => {
          if (other !== el) other.open = false;
        });
      } else if (openDay === el.dataset.day) {
        openDay = "";
      }
    });
  });
}

function overviewPage(stats, leaves) {
  const total = Number(stats.total) || 0;
  const used = Number(stats.used) || 0;
  const searched = Number(stats.searched) || 0;
  const untouched = Number(stats.untouched) || 0;
  const suggest = (stats.suggest_removal || []).slice(0, SUGGEST_CAP);
  return {
    title: "Tổng quan",
    date: `${total} leaf`,
    tag: "stats",
    tone: "memories",
    kicker: "leaf · get và search",
    body: `<div class="book-reading">
        <div class="book-meta">
          <span class="book-author">L2 · get / search</span>
          <h2>Tổng quan</h2>
          <p>Get = đọc đủ. Search = đã hiện trong kết quả. Hot không đếm. Không xóa từ đây.</p>
        </div>
        <div class="stat-row">
          <div><strong>${total}</strong><span>tổng</span></div>
          <div><strong>${used}</strong><span>đã get</span></div>
          <div><strong>${searched}</strong><span>đã search</span></div>
          <div><strong>${untouched}</strong><span>chưa đụng</span></div>
        </div>
        ${dayBlock(leaves)}
        ${expireBlock(stats.expiring)}
        ${suggestBlock(suggest)}
      </div>`,
  };
}

function dayBlock(leaves) {
  const groups = new Map();
  for (const leaf of leaves) {
    const key = fileKey(leaf);
    if (!DAY_FILE.test(key)) continue;
    const list = groups.get(key);
    if (list) list.push(leaf);
    else groups.set(key, [leaf]);
  }
  const keys = sortFileKeys([...groups.keys()]);
  if (!keys.length) {
    return `<div class="suggest-inline"><h3>Theo ngày</h3><p class="suggest-empty">Chưa có daily.</p></div>`;
  }
  const items = keys.map((key) => dayAccordion(key, groups.get(key))).join("");
  return `<div class="suggest-inline"><h3>Theo ngày</h3><div class="day-acc-list">${items}</div></div>`;
}

function dayAccordion(key, leaves) {
  const used = leaves.filter((leaf) => Number(leaf.get_count) > 0).length;
  const searched = leaves.filter((leaf) => Number(leaf.search_count) > 0).length;
  const today = leaves.some((leaf) => leaf.is_today);
  const entries = sortLeaves(leaves).map(leafEntry).join("");
  const open = openDay === key ? " open" : "";
  return `<details class="day-acc" name="mem-day" data-day="${escapeHtml(key)}"${open}>
      <summary>
        <strong>${escapeHtml(key)}</strong>
        <span>${leaves.length} leaf · ${used} đã get · ${searched} search</span>
        <small>${today ? "hôm nay" : "daily"}</small>
      </summary>
      <div class="mem-day-list">${entries}</div>
    </details>`;
}

function expireBlock(rows) {
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

function memoryPages(leaves) {
  const mem = leaves.filter((leaf) => String(leaf.session_id || "").startsWith("memory#"));
  if (!mem.length) return [];
  const used = mem.filter((leaf) => Number(leaf.get_count) > 0).length;
  const searched = mem.filter((leaf) => Number(leaf.search_count) > 0).length;
  const entries = sortLeaves(mem).map(leafEntry).join("");
  return [
    {
      title: "MEMORY.md",
      date: escapeHtml(`${mem.length} leaf · ${used} đã get · ${searched} search`),
      tag: "memory",
      tone: "memories",
      kicker: "MEMORY.md",
      body: `<div class="book-reading">
        <div class="book-meta">
          <span class="book-author">markdown</span>
          <h2>MEMORY.md</h2>
          <p>${mem.length} mem trong file này.</p>
        </div>
        <div class="mem-day-list">${entries}</div>
      </div>`,
    },
  ];
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
  const gets = Number(leaf.get_count) || 0;
  const searches = Number(leaf.search_count) || 0;
  const day = leaf.timeline_day || (leaf.is_today ? "hôm nay" : "");
  return `<article class="mem-entry">
      <h3>${escapeHtml(topic)}</h3>
      <blockquote class="quote-note">
        <p>${escapeHtml(leaf.snippet || "(trống)")}</p>
        <cite>${escapeHtml([time, day].filter(Boolean).join(" · ") || leaf.chunk_id)}</cite>
      </blockquote>
      <dl class="mem-facts">
        <dt>Giờ</dt><dd>${escapeHtml(time || "—")}</dd>
        <dt>Get</dt><dd>${gets}${leaf.last_get_at ? ` · cuối ${escapeHtml(fmtTs(leaf.last_get_at))}` : ""}</dd>
        <dt>Search</dt><dd>${searches}${leaf.last_search_at ? ` · cuối ${escapeHtml(fmtTs(leaf.last_search_at))}` : ""}</dd>
        <dt>Hết hạn</dt><dd>${escapeHtml(fmtTs(leaf.expires_at) || "—")}</dd>
        <dt>Id</dt><dd>${escapeHtml(leaf.chunk_id || "")}</dd>
      </dl>
    </article>`;
}

function suggestBlock(rows) {
  if (!rows.length) {
    return `<div class="suggest-inline"><h3>Đề xuất loại bỏ</h3><p class="suggest-empty">Không có gợi ý.</p></div>`;
  }
  const items = rows
    .map((leaf) => {
      const { topic } = splitHeading(leaf);
      return `<li><strong>${escapeHtml(topic)}</strong><small>${escapeHtml(leafSource(leaf))} · hết hạn ${escapeHtml(fmtTs(leaf.expires_at) || "—")}</small></li>`;
    })
    .join("");
  return `<div class="suggest-inline">
      <h3>Đề xuất loại bỏ</h3>
      <p>Chưa get và chưa từng hiện trong search. Không gồm hôm nay. Chỉ gợi ý — không xóa từ đây.</p>
      <ul class="suggest-list">${items}</ul>
    </div>`;
}

function sortLeaves(leaves) {
  return leaves
    .slice()
    .sort((a, b) => splitHeading(a).time.localeCompare(splitHeading(b).time) || String(a.chunk_id).localeCompare(String(b.chunk_id)));
}

function fileKey(leaf) {
  if (String(leaf.session_id || "").startsWith("memory#")) return "MEMORY.md";
  if (leaf.timeline_day) return `${leaf.timeline_day}.md`;
  return "unknown.md";
}

function sortFileKeys(keys) {
  return keys.sort((a, b) => {
    const dayA = a.match(DAY_FILE);
    const dayB = b.match(DAY_FILE);
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
