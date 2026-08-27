import { formatMarkdown } from "./markdown.js";

const DAY_FILE = /^(\d{4}-\d{2}-\d{2})\.md$/;
const SUGGEST_CAP = 8;
const EXPIRE_CAP = 5;
const DAY_PAGE = 8;

let openDay = "";
let dayQuery = "";
let dayPage = 0;
let lastDayLeaves = [];

export function pagesFromStats(stats) {
  const leaves = stats.leaves || [];
  return [overviewPage(stats, leaves), ...canonicalPages(stats.files || [])];
}

export function bindOverview(root, hooks = {}) {
  if (!root) return;
  activeHooks = hooks;
  const filter = root.querySelector("[data-day-filter]");
  if (filter && !filter.dataset.bound) {
    filter.dataset.bound = "1";
    filter.addEventListener("input", () => {
      dayQuery = filter.value;
      dayPage = 0;
      redrawDays(root);
    });
  }
  bindDayPager(root);
  bindDayDetails(root);
  bindForget(root, hooks.onForget);
  bindCanonical(root);
}

function bindDayPager(root) {
  root.querySelectorAll("[data-day-page]").forEach((button) => {
    button.addEventListener("click", () => {
      dayPage = Number(button.dataset.dayPage) || 0;
      redrawDays(root);
    });
  });
}

function bindDayDetails(root) {
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

function bindOnce(button, handler) {
  if (button.dataset.bound) return;
  button.dataset.bound = "1";
  button.addEventListener("click", handler);
}

function bindForget(root, onForget) {
  root.querySelectorAll("[data-forget]").forEach((button) => {
    bindOnce(button, () => {
      const sid = button.dataset.forget;
      if (!sid || !window.confirm("Xóa mem này khỏi L2?")) return;
      void forgetSession(sid, onForget);
    });
  });
  root.querySelectorAll("[data-reinforce]").forEach((button) => {
    bindOnce(button, () => {
      const sid = button.dataset.reinforce;
      if (!sid) return;
      void reinforceSession(sid, onForget, button);
    });
  });
  root.querySelectorAll("[data-edit]").forEach((button) => {
    bindOnce(button, () => {
      const sid = button.dataset.edit;
      if (!sid) return;
      openLeafEditor(root, sid);
    });
  });
  root.querySelectorAll("[data-edit-save]").forEach((button) => {
    bindOnce(button, () => {
      const sid = button.dataset.editSave;
      const card = button.closest(".mem-entry");
      if (!sid || !card) return;
      const topic = card.querySelector(".mem-edit-topic")?.value.trim() || "";
      const raw = card.querySelector(".mem-edit-body")?.value || "";
      const lines = raw.replace(/\r/g, "").split("\n");
      const summary = (lines.shift() || "").replace(/^-\s+/, "").trim();
      const content = lines.map((line) => line.replace(/^ {2}/, "")).join("\n").trim();
      if (!topic && !summary) return;
      void updateSession(sid, topic || null, summary || null, content || null, onForget, button);
    });
  });
  root.querySelectorAll("[data-edit-cancel]").forEach((button) => {
    bindOnce(button, () => redrawDays(root));
  });
  // card "Đề xuất loại bỏ": bấm để mở ngày chứa leaf
  root.querySelectorAll("[data-open-leaf]").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      activeHooks.onOpenLeaf?.(card.dataset.openLeaf);
    });
  });
}

function openLeafEditor(root, sessionId) {
  const card = [...root.querySelectorAll(".mem-entry")].find(
    (card) => card.querySelector("[data-edit]")?.dataset.edit === sessionId,
  );
  if (!card) return;
  const topic = card.querySelector("h3")?.textContent || "";
  const snippet = card.querySelector(".quote-note p")?.textContent || "";
  const block = card.querySelector(".quote-note");
  if (!block) return;
  block.innerHTML = `<div class="mem-edit">
      <input class="mem-edit-topic" value="${escapeHtml(topic)}" placeholder="tiêu đề" />
      <textarea class="mem-edit-body" rows="4">${escapeHtml(snippet)}</textarea>
      <div class="mem-entry-actions">
        <button type="button" class="mem-forget" data-edit-save="${escapeHtml(sessionId)}">Lưu</button>
        <button type="button" class="mem-reinforce" data-edit-cancel>Hủy</button>
      </div>
    </div>`;
  bindForget(root, activeHooks.onForget);
  card.querySelector(".mem-edit-topic")?.focus();
}

async function updateSession(sessionId, topic, summary, content, onForget, button) {
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/memory/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, topic, summary, content }),
    });
    if (!response.ok) return;
    if (typeof onForget === "function") onForget();
  } catch {
    /* static mock */
  } finally {
    if (button) button.disabled = false;
  }
}

async function reinforceSession(sessionId, onForget, button) {
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/memory/reinforce", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!response.ok) return;
    if (typeof onForget === "function") onForget();
  } catch {
    /* static mock */
  } finally {
    if (button) button.disabled = false;
  }
}

async function forgetSession(sessionId, onForget) {
  try {
    const response = await fetch("/api/memory/forget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!response.ok) return;
    if (typeof onForget === "function") onForget();
  } catch {
    /* static mock */
  }
}

let activeHooks = {};

function redrawDays(root) {
  const mount = root.querySelector("[data-day-list]");
  if (!mount) return;
  mount.innerHTML = dayListHtml(lastDayLeaves);
  bindDayPager(root);
  bindDayDetails(root);
  bindForget(root, activeHooks.onForget);
}

function overviewPage(stats, leaves) {
  const total = Number(stats.total) || 0;
  const used = Number(stats.used) || 0;
  const searched = Number(stats.searched) || 0;
  const untouched = Number(stats.untouched) || 0;
  const suggest = (stats.suggest_removal || []).slice(0, SUGGEST_CAP);
  return {
    title: "Tổng quan",
    hideTitle: true,
    date: `${total} leaf`,
    tag: "",
    tone: "memories",
    kicker: "leaf · get và search",
    body: `<div class="book-reading">
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
  lastDayLeaves = leaves;
  const groups = dayGroups(leaves);
  const keys = sortFileKeys([...groups.keys()]);
  if (!keys.length) {
    return `<div class="suggest-inline"><h3>Theo ngày</h3><p class="suggest-empty">Chưa có daily.</p></div>`;
  }
  return `<div class="suggest-inline">
      <h3>Theo ngày</h3>
      <label class="day-filter">Lọc ngày
        <input type="search" data-day-filter value="${escapeHtml(dayQuery)}" placeholder="2026-08-20" autocomplete="off" />
      </label>
      <div data-day-list>${dayListHtml(leaves)}</div>
    </div>`;
}

function dayGroups(leaves) {
  const groups = new Map();
  for (const leaf of leaves) {
    const key = fileKey(leaf);
    if (!DAY_FILE.test(key)) continue;
    const list = groups.get(key);
    if (list) list.push(leaf);
    else groups.set(key, [leaf]);
  }
  return groups;
}

function dayListHtml(leaves) {
  const groups = dayGroups(leaves);
  const keys = filterDayKeys(sortFileKeys([...groups.keys()]), dayQuery);
  const pages = Math.max(1, Math.ceil(keys.length / DAY_PAGE));
  if (dayPage >= pages) dayPage = pages - 1;
  if (dayPage < 0) dayPage = 0;
  const slice = keys.slice(dayPage * DAY_PAGE, dayPage * DAY_PAGE + DAY_PAGE);
  if (!slice.length) {
    return `<p class="suggest-empty">Không thấy ngày khớp.</p>`;
  }
  const items = slice.map((key) => dayAccordion(key, groups.get(key))).join("");
  return `<div class="day-acc-list">${items}</div>${dayPager(pages)}`;
}

export function filterDayKeys(keys, query) {
  const needle = String(query || "").trim().toLocaleLowerCase("vi");
  if (!needle) return keys;
  return keys.filter((key) => key.toLocaleLowerCase("vi").includes(needle));
}

function dayPager(pages) {
  if (pages <= 1) return "";
  return `<nav class="day-pager">
      <button type="button" data-day-page="${dayPage - 1}" ${dayPage <= 0 ? "disabled" : ""}>Trước</button>
      <span>${dayPage + 1} / ${pages}</span>
      <button type="button" data-day-page="${dayPage + 1}" ${dayPage + 1 >= pages ? "disabled" : ""}>Sau</button>
    </nav>`;
}

function dayAccordion(key, leaves) {
  const used = leaves.filter((leaf) => Number(leaf.get_count) > 0).length;
  const searched = leaves.filter((leaf) => Number(leaf.search_count) > 0).length;
  const today = leaves.some((leaf) => leaf.is_today);
  const entries = sortLeaves(leaves).map((leaf) => leafEntry(leaf)).join("");
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

function canonicalPages(files) {
  const order = { "USER.md": 0, "SOUL.md": 1, "IDENTITY.md": 2 };
  const labels = { "USER.md": "ghi chú của user", "SOUL.md": "chỉ dẫn cho agent", "IDENTITY.md": "chỉ dẫn cho agent" };
  const descs = {
    "USER.md": "thông tin về bạn — tên, sở thích, bối cảnh",
    "SOUL.md": "cách agent nói chuyện và trả lời",
    "IDENTITY.md": "danh tính và giới hạn của agent",
  };
  const list = files
    .filter((file) => file && file.name)
    .sort((a, b) => (order[a.name] ?? 9) - (order[b.name] ?? 9));
  const sections = [];
  let prevLayer = null;
  for (const file of list) {
    const name = String(file.name);
    const layer = name === "USER.md" ? "user" : "self";
    // ngăn giữa 2 lớp: ghi chú của user — ghi chú của bản thân
    if (prevLayer === "user" && layer === "self") {
      sections.push(`<div class="canon-divider" role="separator"><span>chỉ dẫn cho agent</span></div>`);
    }
    prevLayer = layer;
    const content = String(file.content || "");
    sections.push(`<article class="mem-entry">
        <p class="canon-label">${escapeHtml(labels[name] || "")}</p>
        <h3>${escapeHtml(name)}</h3>
        <p class="canon-desc">${escapeHtml(descs[name] || "")}</p>
        <div class="mem-md" data-canonical="${escapeHtml(name)}" data-raw="${escapeHtml(content)}">${formatMarkdown(content) || "(trống)"}</div>
        <div class="mem-entry-actions mem-canonical-actions">
          <button type="button" class="mem-reinforce" data-canonical-edit="${escapeHtml(name)}">Sửa</button>
        </div>
      </article>`);
  }
  return [
    {
      title: "Hồ sơ",
      date: `${escapeHtml(String(list.length))} file · inject mỗi lượt`,
      tag: "",
      tone: "memories",
      kicker: "canonical · prompt",
      body: `<div class="canon-list">${sections.join("")}</div>`,
    },
  ];
}

function leafEntry(leaf, reason = "") {
  const { time, topic } = splitHeading(leaf);
  const gets = Number(leaf.get_count) || 0;
  const searches = Number(leaf.search_count) || 0;
  const day = leaf.timeline_day || (leaf.is_today ? "hôm nay" : "");
  // cite 2 dòng: giờ - ngày / get · search · hết hạn · id
  const chunkDay = chunkDate(leaf.chunk_id);
  const citeTop = [time, chunkDay || (day && day !== "hôm nay" ? day : "")].filter(Boolean).join(" - ");
  const citeMeta = [
    `get ${gets}`,
    `search ${searches}`,
    leaf.expires_at ? `hết hạn ${fmtTs(leaf.expires_at)}` : "",
    leaf.chunk_id ? `id ${leaf.chunk_id}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const cite = [reason, citeTop, citeMeta].filter(Boolean);
  const openable = reason ? ` data-open-leaf="${escapeHtml(leaf.chunk_id || "")}" title="Bấm để xem leaf trong ngày"` : "";
  return `<article class="mem-entry${reason ? " is-suggest" : ""}" data-chunk-id="${escapeHtml(leaf.chunk_id || "")}"${openable}>
      <h3>${escapeHtml(topic)}</h3>
      <blockquote class="quote-note">
        <p>${escapeHtml(leaf.snippet || "(trống)")}</p>
        <cite class="mem-entry-cite">${cite.map((line) => `<span>${escapeHtml(line)}</span>`).join("") || escapeHtml(leaf.chunk_id || "")}</cite>
      </blockquote>
      <div class="mem-entry-actions">
        <button type="button" class="mem-reinforce" data-edit="${escapeHtml(leaf.session_id || "")}">Sửa</button>
        <button type="button" class="mem-forget" data-forget="${escapeHtml(leaf.session_id || "")}">Xóa</button>
        <button type="button" class="mem-reinforce" data-reinforce="${escapeHtml(leaf.session_id || "")}">Gia hạn</button>
      </div>
    </article>`;
}

// chunk_id dạng "YYYY-MM-DD#hash#n" → "YYYY-M-D"
function chunkDate(chunkId) {
  const day = String(chunkId || "").split("#")[0];
  if (!/^\d{4}-\d{2}-\d{2}/.test(day)) return "";
  const [y, m, d] = day.split("-");
  return `${y}-${Number(m)}-${Number(d)}`;
}

function suggestBlock(rows) {
  if (!rows.length) {
    return `<div class="suggest-inline"><h3>Đề xuất loại bỏ</h3><p class="suggest-empty">Không có gợi ý.</p></div>`;
  }
  // card giống leaf trong "Theo ngày", lý do nằm trong card, bấm để xem trong ngày
  const cards = rows
    .map((leaf) => leafEntry(leaf, "chưa get/search · ≥ 7 ngày — có thể xóa"))
    .join("");
  return `<div class="suggest-inline">
      <h3>Đề xuất loại bỏ</h3>
      <div class="mem-day-list">${cards}</div>
    </div>`;
}

function sortLeaves(leaves) {
  return leaves
    .slice()
    .sort((a, b) => splitHeading(a).time.localeCompare(splitHeading(b).time) || String(a.chunk_id).localeCompare(String(b.chunk_id)));
}

function fileKey(leaf) {
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

// ---- sửa file canonical (SOUL/USER/IDENTITY.md) ----

function bindCanonical(root) {
  root.querySelectorAll("[data-canonical-edit]").forEach((button) => {
    bindOnce(button, () => openCanonicalEditor(root, button.dataset.canonicalEdit));
  });
  // Lưu/Hủy là nút động (tạo khi mở editor) → delegation trên .mem-md
  root.querySelectorAll("[data-canonical]").forEach((wrap) => {
    if (wrap.dataset.canonicalBound) return;
    wrap.dataset.canonicalBound = "1";
    wrap.addEventListener("click", (event) => {
      const save = event.target.closest("[data-canonical-save]");
      if (save) {
        const text = wrap.querySelector("textarea")?.value;
        if (typeof text !== "string") return;
        save.disabled = true;
        void saveCanonical(save.dataset.canonicalSave, text, wrap, save);
        return;
      }
      const cancel = event.target.closest("[data-canonical-cancel]");
      if (cancel) {
        delete wrap.dataset.editing;
        wrap.innerHTML = formatMarkdown(wrap.dataset.raw || "") || "(trống)";
      }
    });
  });
}

function cssEscapeAttr(value) {
  return window.CSS?.escape ? CSS.escape(value) : String(value).replace(/"/g, '\\"');
}

function openCanonicalEditor(root, name) {
  const wrap = root.querySelector(`[data-canonical="${cssEscapeAttr(name)}"]`);
  if (!wrap || wrap.dataset.editing) return;
  wrap.dataset.editing = "1";
  const raw = wrap.dataset.raw || "";
  wrap.innerHTML = `<textarea class="mem-edit-body mem-edit-body-tall" spellcheck="false"></textarea>
      <div class="mem-entry-actions mem-canonical-actions">
        <button type="button" class="mem-reinforce" data-canonical-save="${escapeHtml(name)}">Lưu</button>
        <button type="button" class="mem-forget" data-canonical-cancel="${escapeHtml(name)}">Hủy</button>
      </div>`;
  const area = wrap.querySelector("textarea");
  if (area) {
    area.value = raw;
    area.focus();
  }
}

async function saveCanonical(name, content, wrap, button) {
  button.disabled = true;
  try {
    /* static mock */
    const res = await fetch("/api/memory/canonical", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, content }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    wrap.dataset.raw = content;
    delete wrap.dataset.editing;
    wrap.innerHTML = formatMarkdown(content) || "(trống)";
    // refresh stats để số liệu khớp
    activeHooks.onForget?.();
  } catch {
    button.disabled = false;
    window.alert("Không lưu được file. Thử lại nhé.");
  }
}

// Bấm card gợi ý: mở đúng ngày trong "Theo ngày" rồi nhảy tới leaf
export async function revealLeaf(chunkId) {
  const day = `${String(chunkId || "").split("#")[0]}.md`;
  if (DAY_FILE.test(day)) openDay = day;
  await activeHooks.onForget?.();
  setTimeout(() => {
    const card = document.querySelector(`[data-chunk-id="${window.CSS?.escape ? CSS.escape(chunkId) : chunkId}"]`);
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("is-flash");
    setTimeout(() => card.classList.remove("is-flash"), 1800);
  }, 500);
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
