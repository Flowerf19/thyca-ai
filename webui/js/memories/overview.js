import { escapeHtml } from "../shared/util.js";
import { bindCanonical, canonicalPages } from "./canonical.js";
import { DAY_FILE } from "./leaf.js";
import {
  expireBlock,
  fileKey,
  leafEntry,
  rankLeaves,
  sortLeaves,
  sortFileKeys,
  splitHeading,
} from "./leaf.js";

const DAY_PAGE = 8;

let openDay = "";
let dayQuery = "";
let dayPage = 0;
let dayView = "day";
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
  bindDayViews(root);
  bindDayDetails(root);
  bindForget(root, hooks.onForget);
  bindCanonical(root, hooks);
}

function bindDayViews(root) {
  root.querySelectorAll("[data-day-view]").forEach((button) => {
    bindOnce(button, () => {
      dayView = button.dataset.dayView || "day";
      dayPage = 0;
      redrawDays(root);
    });
  });
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
      if (!sid || !window.confirm("Quên mem này khỏi L2?")) return;
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
      window.alert(`Không quên được (${response.status}). Thử lại nhé.`);
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
  const block = mount.closest(".suggest-inline");
  if (block) {
    const heading = block.querySelector("h3");
    if (heading) heading.textContent = viewTitle();
  }
  const filter = root.querySelector(".day-filter");
  if (filter) filter.hidden = dayView !== "day";
  root.querySelectorAll("[data-day-view]").forEach((button) => {
    button.classList.toggle("is-on", (button.dataset.dayView || "day") === dayView);
  });
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
  return {
    title: "Tổng quan",
    hideTitle: true,
    date: `${total} leaf`,
    tag: "",
    tone: "memories",
    kicker: "leaf · get và search",
    body: `<div class="book-reading">
        <div class="stat-row">
          <div><span>Tổng:</span> <strong>${total}</strong></div>
          <div><span>Đã get:</span> <strong>${used}</strong></div>
          <div><span>Đã search:</span> <strong>${searched}</strong></div>
          <div><span>Chưa đụng:</span> <strong>${untouched}</strong></div>
        </div>
        ${dayBlock(leaves)}
        ${expireBlock(stats.expiring)}
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
      <h3>${escapeHtml(viewTitle())}</h3>
      <nav class="day-view">
        ${viewTab("day", "Theo ngày")}
        ${viewTab("get", "Dùng nhiều")}
        ${viewTab("search", "Tìm nhiều")}
        ${viewTab("least", "Dùng ít")}
      </nav>
      ${dayView === "day" ? `<label class="day-filter">
        <span class="visually-hidden">Lọc ngày</span>
        <input type="search" data-day-filter value="${escapeHtml(dayQuery)}" placeholder="2026-08-20" autocomplete="off" />
      </label>` : ""}
      <div data-day-list>${dayListHtml(leaves)}</div>
    </div>`;
}

function viewTitle() {
  if (dayView === "get") return "Dùng nhiều nhất";
  if (dayView === "search") return "Tìm nhiều nhất";
  if (dayView === "least") return "Ít được dùng";
  return "Theo ngày";
}

function viewTab(id, label) {
  const on = dayView === id ? " class=\"is-on\"" : "";
  return `<button type="button"${on} data-day-view="${id}">${escapeHtml(label)}</button>`;
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
  if (dayView !== "day") {
    const ranked = rankLeaves(leaves, dayView);
    if (!ranked.length) return `<p class="suggest-empty">Chưa có leaf.</p>`;
    return `<div class="mem-day-list">${ranked.map((leaf) => leafEntry(leaf)).join("")}</div>`;
  }
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
