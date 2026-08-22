import { fillChatAt, hydrateChat, stopThinkingCycle } from "./chat.js";
import { icons, modes } from "./data.js";
import { el } from "./dom.js";
import { closeDrawer } from "./drawer.js";
import { pagesFromStats } from "./memories.js";
import { state } from "./state.js";

let modeGen = 0;
let memoriesPoll = 0;
let lastStatsJson = "";

export function pageCard(page, index) {
  const selected = index === state.activePageIndex;
  return `<button class="page-card${selected ? " is-active" : ""}" type="button" data-page-index="${index}" data-tone="${page.tone}"${selected ? ' aria-current="page"' : ""}>
          <span class="page-card-icon">${icons[page.tone]}</span>
          <span class="page-card-copy"><strong>${page.title}</strong><small>${page.date}</small></span>
          <span class="page-tag page-tag-${page.tone}">${page.tag}</span>
        </button>`;
}

export function renderPageList(query = "") {
  const pages = modes[state.activeMode].pages;
  const normalized = query.trim().toLocaleLowerCase("vi");
  const filtered = pages.filter((page) => `${page.title} ${page.tag} ${page.date}`.toLocaleLowerCase("vi").includes(normalized));
  el.pageList.innerHTML = filtered.map((page) => pageCard(page, pages.indexOf(page))).join("");
  el.searchEmpty.hidden = filtered.length > 0;
  el.pageList.hidden = filtered.length === 0;
  el.pageList.querySelectorAll(".page-card").forEach((card) =>
    card.addEventListener("click", () => {
      el.pageList.querySelectorAll(".page-card").forEach((item) => {
        item.classList.remove("is-active");
        item.removeAttribute("aria-current");
      });
      card.classList.add("is-active");
      card.setAttribute("aria-current", "page");
      const index = Number(card.dataset.pageIndex);
      if (state.activeMode === "chat" && state.chatLive) {
        void fillChatAt(index).then(() => {
          renderPage(index);
          closeDrawer();
        });
        return;
      }
      state.activePageIndex = index;
      renderPage(index);
      closeDrawer();
    }),
  );
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

export function setTracePlaying(playing) {
  const playerButton = document.getElementById("player-button");
  const playerLabel = document.getElementById("player-label");
  if (playerButton) {
    playerButton.setAttribute("aria-pressed", String(playing));
    playerButton.classList.toggle("is-playing", playing);
    playerButton.querySelector(".player-symbol").textContent = playing ? "Ⅱ" : "▶";
    playerLabel.textContent = playing ? "Đang phát lại" : "Phát lại lượt";
  }
  el.miniPlay.setAttribute("aria-pressed", String(playing));
  el.miniPlay.setAttribute("aria-label", playing ? "Dừng phát lại" : "Phát lại lượt");
  el.miniPlay.classList.toggle("is-playing", playing);
  el.miniPlay.querySelector(".mini-play-symbol").textContent = playing ? "Ⅱ" : "▶";
}

export function bindPlayer() {
  const playerButton = document.getElementById("player-button");
  if (!playerButton) return;
  playerButton.addEventListener("click", () => {
    setTracePlaying(playerButton.getAttribute("aria-pressed") !== "true");
  });
}

export function renderPage(pageIndex = 0) {
  const data = modes[state.activeMode];
  const page = data.pages[pageIndex] || data.pages[0];
  state.activePageIndex = data.pages.indexOf(page);
  el.notebook.dataset.mode = state.activeMode;
  el.modeBreadcrumb.textContent = data.label;
  el.pageListLabel.textContent = data.listLabel;
  el.pageHeader.innerHTML = `<div class="page-header-copy"><p class="page-kicker">${page.kicker || data.kicker}</p><h1>${page.title}</h1><p class="page-note">${data.note}</p></div><div class="page-header-mark" aria-hidden="true">${icons[state.activeMode]}<span>${data.label}</span></div>`;
  el.pageBody.innerHTML = page.body || data.body;
  el.form.hidden = state.activeMode !== "chat";
  el.miniPlayer.hidden = state.activeMode !== "trace";
  setTracePlaying(false);
  renderPageList(el.pageSearch.value);
  renderChips();
  if (state.activeMode === "trace") bindPlayer();
}

export async function renderMode(mode) {
  const gen = ++modeGen;
  state.activeMode = mode;
  state.activePageIndex = 0;
  stopThinkingCycle();
  stopMemoriesPoll();
  if (mode === "memories") {
    lastStatsJson = "";
    await hydrateMemories();
    if (gen !== modeGen) return;
    startMemoriesPoll();
  }
  if (mode === "chat") {
    try {
      await hydrateChat();
    } catch {
      state.chatLive = false;
    }
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
      listLabel: "File md",
      kicker: "leaf · chỉ đếm get",
      note: "Search và inject nóng không tính. Không xóa từ đây.",
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
