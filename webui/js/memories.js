import { formatMarkdown } from "./markdown.js";
import { escapeHtml } from "./util.js";
import { bindCanonical, canonicalPages } from "./memories-canonical.js";
import { DAY_FILE } from "./memories-leaf.js";
import {
  expireBlock,
  fileKey,
  leafEntry,
  sortLeaves,
  sortFileKeys,
  splitHeading,
  suggestBlock,
} from "./memories-leaf.js";

const SUGGEST_CAP = 8;
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
  bindCanonical(root, hooks);
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
  const topic = card.dataset.topic || "";
  const snippet = card.dataset.snippet || "";
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
    if (!response.ok) {
      window.alert(`Không lưu được (${response.status}). Thử lại nhé.`);
      return;
    }
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
    if (!response.ok) {
      window.alert(`Không gia hạn được (${response.status}). Thử lại nhé.`);
      return;
    }
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
    if (!response.ok) {
      window.alert(`Không xóa được (${response.status}). Thử lại nhé.`);
      return;
    }
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
