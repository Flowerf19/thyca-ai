import { fillChatAt, getLiveTurn, hydrateChat, initToBottom, invalidateChatHydrate, renderComposerMeter, resetToNewChatPage, restoreLiveTurn, scrollThread, updateToBottomVisibility } from "./chat/index.js";
import { icons, modes } from "./shared/data.js";
import { el } from "./shared/dom.js";
import { closeDrawer } from "./shared/drawer.js";
import { bindOverview, pagesFromStats, revealLeaf } from "./memories/index.js";
import { bindSettings, hydrateSettings } from "./settings/index.js";
import { escapeHtml } from "./shared/util.js";
import { bindTraceOverview, fillTraceAt, hydrateTrace, updateMiniPlayer } from "./trace/index.js";
import { state } from "./shared/state.js";

let modeGen = 0;
let openPageAtGen = 0;
let memoriesPoll = 0;
let lastStatsJson = "";

export function pageCard(page, index) {
  const selected = index === state.activePageIndex;
  const status = page.status ? ` data-status="${escapeHtml(page.status)}"` : "";
  const failed = page.loadError ? " data-load-error" : "";
  return `<button class="page-card${selected ? " is-active" : ""}" type="button" data-page-index="${index}" data-tone="${page.tone}"${status}${failed}${selected ? ' aria-current="page"' : ""}>
          <span class="page-card-icon">${icons[page.tone]}</span>
          <span class="page-card-copy"><strong>${page.title}${page.loadError ? " ⚠" : ""}</strong><small>${page.loadError ? "tải lỗi — bấm để thử lại" : page.date}</small></span>
          ${page.tag ? `<span class="page-tag page-tag-${page.tone}">${page.tag}</span>` : ""}
        </button>`;
}

let listQuery = "";
let listCap = 0;
let listResizeBound = false;

function listCapacity() {
  const wrap = el.pageList && el.pageList.closest(".page-list-wrap");
  if (!wrap) return 1;
  const toolbar = wrap.querySelector(".page-list-toolbar");
  const toolbarH = toolbar ? toolbar.offsetHeight : 0;
  const pagerH = 28;
  const available = wrap.clientHeight - toolbarH - pagerH - 8;
  const card = el.pageList.querySelector(".page-card");
  const gap = parseFloat(getComputedStyle(el.pageList).rowGap) || 4;
  const cardH = card ? card.offsetHeight : 56;
  const step = cardH + gap;
  if (step <= 0 || available <= 0) return 1;
  return Math.max(1, Math.floor(available / step));
}

function bindListResize() {
  if (listResizeBound) return;
  const wrap = el.pageList && el.pageList.closest(".page-list-wrap");
  if (!wrap || typeof ResizeObserver !== "function") return;
  listResizeBound = true;
  new ResizeObserver(() => {
    const cap = listCapacity();
    if (cap === listCap) return;
    renderPageList(listQuery);
  }).observe(wrap);
}

export function renderPageList(query = "", { snapToActive = false } = {}) {
  bindListResize();
  const pages = modes[state.activeMode].pages;
  const normalized = query.trim().toLocaleLowerCase("vi");
  let filtered = pages.filter((page) => `${page.title} ${page.tag} ${page.date}`.toLocaleLowerCase("vi").includes(normalized));
  if (!state.pageOrderNewest) filtered = filtered.slice().reverse();
  const cap = listCapacity();
  listCap = cap;
  if (query !== listQuery) {
    listQuery = query;
    snapToActive = true;
  }
  if (snapToActive) {
    const active = pages[state.activePageIndex];
    const at = active ? filtered.indexOf(active) : -1;
    state.listPage = at >= 0 && cap > 0 ? Math.floor(at / cap) : 0;
  }
  const pageCount = Math.max(1, Math.ceil(filtered.length / cap) || 1);
  if (state.listPage > pageCount - 1) state.listPage = pageCount - 1;
  if (state.listPage < 0) state.listPage = 0;
  const start = state.listPage * cap;
  const visible = filtered.slice(start, start + cap);
  el.pageList.innerHTML = visible.map((page) => pageCard(page, pages.indexOf(page))).join("");
  el.searchEmpty.hidden = filtered.length > 0;
  el.pageList.hidden = filtered.length === 0;
  const pager = document.getElementById("page-list-pager");
  const pos = document.getElementById("page-list-pos");
  const prev = document.getElementById("page-list-prev");
  const next = document.getElementById("page-list-next");
  if (pager) {
    const show = filtered.length > cap;
    pager.hidden = !show;
    if (pos) pos.textContent = `${state.listPage + 1} / ${pageCount}`;
    if (prev) prev.disabled = state.listPage <= 0;
    if (next) next.disabled = state.listPage >= pageCount - 1;
  }
  el.pageList.querySelectorAll(".page-card").forEach((card) =>
    card.addEventListener("click", () => {
      el.pageList.querySelectorAll(".page-card").forEach((item) => {
        item.classList.remove("is-active");
        item.removeAttribute("aria-current");
      });
      card.classList.add("is-active");
      card.setAttribute("aria-current", "page");
      const index = Number(card.dataset.pageIndex);
      openPageAt(index);
    }),
  );
}

// Một điểm mở tab duy nhất: gắn guard race cho chat (TASK-010),
// trace giữ nguyên fillTraceAt, các mode khác render trực tiếp.
function openPageAt(index) {
  const pages = modes[state.activeMode].pages;
  const page = pages[index];
  if (!page) return;
  if (state.activeMode === "chat" && state.chatLive) {
    const gen = (openPageAtGen += 1);
    void fillChatAt(index).then((ok) => {
      // Lượt cũ về trễ: bỏ, không render đè tab mới.
      if (gen !== openPageAtGen) return;
      if (ok === false) showPageError();
      renderPage(state.activePageIndex);
      closeDrawer();
    });
    return;
  }
  if (state.activeMode === "trace" && page && page.sessionId) {
    void fillTraceAt(index).then(() => {
      renderPage(index);
      closeDrawer();
    });
    return;
  }
  state.activePageIndex = index;
  renderPage(index);
  closeDrawer();
}

// Lỗi tải session: báo ở hint composer thay vì trang trắng (TASK-011).
function showPageError() {
  if (state.activeMode !== "chat" || !el.hint) return;
  el.hint.textContent = "Không tải được phiên này — kiểm tra mạng rồi bấm lại tab.";
  el.hint.className = "hint is-error";
}

export function renderChips() {
  el.chips.innerHTML = modes[state.activeMode].chips.map((chip) => `<button type="button" class="suggestion-chip">${chip}</button>`).join("");
  el.chips.querySelectorAll(".suggestion-chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      el.line.value = `${chip.textContent}: `;
      el.line.focus();
    }),
  );
}

export function renderPage(pageIndex = 0) {
  const data = modes[state.activeMode];
  const page = data.pages[pageIndex] || data.pages[0];
  initToBottom();
  state.activePageIndex = data.pages.indexOf(page);
  el.notebook.dataset.mode = state.activeMode;
  el.topbar.dataset.mode = state.activeMode;
  if (el.toBottom) el.toBottom.hidden = true;
  // chat giữ kicker ~/.thyca · model; memories/trace: mark (icon + label) ở góc trái
  if (state.activeMode === "chat") {
    el.modeBreadcrumb.classList.remove("crumb-mark");
    el.modeBreadcrumb.textContent = data.kicker;
  } else {
    el.modeBreadcrumb.classList.add("crumb-mark");
    el.modeBreadcrumb.innerHTML = `${icons[state.activeMode]}<span>${data.label}</span>`;
  }
  el.pageListLabel.textContent = data.listLabel;
  const noteText = page.note || data.note;
  el.pageHeader.hidden = Boolean(page.hideTitle);
  el.pageHeader.innerHTML = page.hideTitle
    ? ""
    : `<div class="page-header-copy"><h1>${page.title}</h1>${noteText ? `<p class="page-note">${noteText}</p>` : ""}</div>`;
  el.pageBody.innerHTML = page.body || data.body;
  if (state.activeMode === "chat") {
    restoreLiveTurn(el.pageBody, page);
  }
  if (state.activeMode === "memories") {
    bindOverview(el.pageBody, {
      onForget: () => {
        lastStatsJson = "";
        void hydrateMemories({ keepPage: true });
      },
      onOpenLeaf: (chunkId) => void revealLeaf(chunkId),
    });
  }
  if (state.activeMode === "settings") {
    bindSettings(el.pageBody);
  }
  el.form.hidden = state.activeMode !== "chat";
  if (state.activeMode === "chat") {
    // Switch tab chat = xuống cuối (scrollThread đợi layout ổn định qua rAF).
    scrollThread();
    // Meter dưới composer = last-turn fresh/cache/out/ctx/cost của tab đang xem;
    // phiên trống (messages rỗng) hiện "—".
    const live = getLiveTurn(page.sessionId);
    renderComposerMeter(el.meter, page.messages, live && live.running ? null : el.toolMeter);
  }
  if (state.activeMode === "trace") {
    updateMiniPlayer(page);
    bindTraceOverview(el.pageBody, {
      onRefilter: () => renderPage(0),
      onTurn: () => renderPage(state.activePageIndex),
    });
  } else {
    el.miniPlayer.hidden = true;
  }
  renderPageList(el.pageSearch.value, { snapToActive: true });
  renderChips();
  updateToBottomVisibility();
}

export async function renderMode(mode) {
  const gen = ++modeGen;
  state.activeMode = mode;
  state.activePageIndex = 0;
  state.listPage = 0;
  stopMemoriesPoll();
  if (mode === "memories") {
    lastStatsJson = "";
    await hydrateMemories();
    if (gen !== modeGen) return;
    startMemoriesPoll();
  }
  if (mode === "chat") {
    invalidateChatHydrate();
    try {
      await hydrateChat();
    } catch {
      state.chatLive = false;
    }
    if (gen !== modeGen) return;
    // Giữ session đang xem nếu hydrate giữ được nó; chỉ mở phiên mới
    // khi hydrate không khôi phục được vị trí cũ (TASK-012).
    const kept = (modes.chat.pages || []).findIndex(
      (page) => page.sessionId && page.sessionId === state.activeSessionId,
    );
    if (kept >= 0) {
      state.activePageIndex = kept;
    } else {
      resetToNewChatPage();
    }
  }
  if (mode === "trace") {
    try {
      await hydrateTrace();
    } catch {
      /* static mock: no API */
    }
    if (gen !== modeGen) return;
  }
  if (mode === "settings") {
    await hydrateSettings();
    if (gen !== modeGen) return;
  }
  renderPage(state.activePageIndex);
  el.modeList.querySelectorAll(".mode-link").forEach((button) => {
    const selected = button.dataset.mode === mode;
    button.classList.toggle("is-active", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function startMemoriesPoll() {
  stopMemoriesPoll();
  memoriesPoll = window.setInterval(() => {
    if (state.activeMode === "memories") void hydrateMemories({ keepPage: true });
  }, 3000);
}

function stopMemoriesPoll() {
  if (memoriesPoll) {
    window.clearInterval(memoriesPoll);
    memoriesPoll = 0;
  }
}

async function hydrateMemories({ keepPage = false } = {}) {
  try {
    const response = await fetch("/api/memory/stats", { cache: "no-store" });
    if (!response.ok) return;
    const stats = await response.json();
    if (typeof stats.total !== "number" || !Array.isArray(stats.leaves)) return;
    const snapshot = JSON.stringify(stats);
    if (snapshot === lastStatsJson) return;
    lastStatsJson = snapshot;
    const pages = pagesFromStats(stats);
    modes.memories = {
      label: "Memories",
      listLabel: "Canonical",
      kicker: "leaf · get và search",
      note: "",
      chips: [],
      pages,
    };
    const count = el.modeList.querySelector('[data-mode="memories"] .mode-count');
    if (count) count.textContent = String(stats.total);
    if (keepPage && state.activeMode === "memories") {
      const index = Math.min(state.activePageIndex, Math.max(pages.length - 1, 0));
      renderPage(index);
    }
  } catch {
    /* static mock: no API */
  }
}
