import { beginOutgoingTurn, createChatSession, discardRunningLiveTurns, isViewingSession, removeStatus, sendChatTurn, settleIncoming } from "./chat/index.js";
import { el } from "./shared/dom.js";
import { closeDrawer, hideDrawerIfMobile, toggleDrawer } from "./shared/drawer.js";
import { renderMode, renderPage, renderPageList } from "./render.js";
import { getJson, postJson } from "./shared/util.js";
import { state } from "./shared/state.js";
import { hydrateSettings, renderModeSettings } from "./settings/index.js";

const IDLE_MS = 15 * 60 * 1000;
const IDLE_REMEMBER =
  "Hãy nhớ những điều đáng giữ trong phiên này.";
let idleTimer = 0;
const idleArmed = new Set();
let idleFromNudge = false;

function clearError() {
  el.field.classList.remove("is-error", "is-success");
  el.line.removeAttribute("aria-invalid");
  el.hint.textContent = "";
  el.hint.className = "hint";
}

function showError(message) {
  el.field.classList.add("is-error");
  el.line.setAttribute("aria-invalid", "true");
  el.hint.textContent = message;
  el.hint.className = "hint is-error";
}

function setBusy(busy) {
  el.send.disabled = busy;
  el.line.disabled = busy;
  const newer = document.getElementById("new-page");
  if (newer) newer.disabled = busy;
  el.field.classList.toggle("is-loading", busy);
  if (el.idleRemember) el.idleRemember.disabled = busy;
  if (el.idleDismiss) el.idleDismiss.disabled = busy;
}

function hideIdle() {
  if (el.idleNudge) el.idleNudge.hidden = true;
}

function sessionKey() {
  return state.activeSessionId || "";
}

function noteSend() {
  const key = sessionKey();
  if (!key) return;
  if (idleFromNudge) idleArmed.delete(key);
  else idleArmed.add(key);
  idleFromNudge = false;
}

async function showIdle() {
  const key = sessionKey();
  if (!state.chatLive || state.activeMode !== "chat" || el.send.disabled) return;
  if (!key || !idleArmed.has(key)) return;
  const detail = await getJson(`/api/sessions/${key}`);
  if (!detail || detail.ask_remember !== true) return;
  if (sessionKey() !== key || !idleArmed.has(key)) return;
  if (el.idleNudge) el.idleNudge.hidden = false;
}

function armIdle() {
  hideIdle();
  window.clearTimeout(idleTimer);
  idleTimer = 0;
  if (!state.chatLive || !idleArmed.has(sessionKey())) return;
  idleTimer = window.setTimeout(() => {
    void showIdle();
  }, IDLE_MS);
}

async function openNewPage() {
  el.line.value = "";
  clearError();
  if (state.activeMode !== "chat") {
    await renderMode("chat");
  }
  if (state.chatLive) {
    try {
      await createChatSession();
    } catch (error) {
      showError(error instanceof Error ? error.message : "Không tạo được phiên.");
      return;
    }
  }
  renderPage(state.activePageIndex);
  el.line.focus();
  closeDrawer();
}

async function submitLine() {
  const text = el.line.value.trim();
  if (!text) {
    showError("Chưa có chữ để gửi. Viết một câu trước.");
    el.line.focus();
    return;
  }
  clearError();
  el.line.value = "";
  beginOutgoingTurn(text);
  setBusy(true);
  try {
    if (state.chatLive) {
      const detail = await sendChatTurn(text);
      if (isViewingSession(detail.id)) {
        if (!settleIncoming()) renderPage(state.activePageIndex);
        else renderPageList(el.pageSearch.value);
      } else {
        // Turn finished in the background: page.body already updated by
        // applyDetail. restoreLiveTurn remounts the thinking line on return.
        renderPageList(el.pageSearch.value);
      }
    } else {
      const wait = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 1000;
      await new Promise((resolve) => window.setTimeout(resolve, wait));
      if (state.activeMode === "chat") removeStatus();
    }
    if (state.activeMode === "chat") {
      el.field.classList.add("is-success");
      el.hint.textContent = "Đã gửi vào phiên này.";
      el.hint.className = "hint is-success";
      noteSend();
      armIdle();
    } else {
      idleFromNudge = false;
    }
  } catch (error) {
    idleFromNudge = false;
    const viewing = state.activeMode === "chat";
    const failed = viewing && el.pageBody.querySelector(".entry-status.is-error");
    if (!failed) {
      discardRunningLiveTurns();
      if (viewing) removeStatus();
    }
    showError(error instanceof Error ? error.message : "Không gửi được.");
  } finally {
    setBusy(false);
    el.line.focus();
  }
}

function bind() {
  el.modeList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (!button) return;
    renderMode(button.dataset.mode);
    armIdle();
  });
  const searchToggle = document.getElementById("search-toggle");
  const searchField = document.getElementById("search-field");
  const searchToolbar = searchToggle && searchToggle.closest(".library-heading");
  const openSearch = () => {
    if (!searchField || !searchToggle) return;
    searchField.hidden = false;
    searchToggle.setAttribute("aria-expanded", "true");
    searchToolbar?.classList.add("is-searching");
    el.pageSearch.focus();
  };
  const closeSearch = () => {
    if (!searchField || !searchToggle || el.pageSearch.value.trim()) return;
    searchField.hidden = true;
    searchToggle.setAttribute("aria-expanded", "false");
    searchToolbar?.classList.remove("is-searching");
    el.pageSearch.blur();
  };
  searchToggle?.addEventListener("click", () => openSearch());
  el.pageSearch.addEventListener("input", () => renderPageList(el.pageSearch.value));
  el.pageSearch.addEventListener("blur", () => {
    window.setTimeout(closeSearch, 0);
  });
  el.pageList.addEventListener("click", (event) => {
    if (event.target.closest(".page-card")) armIdle();
  });
  document.getElementById("sort-pages").addEventListener("click", (event) => {
    state.pageOrderNewest = !state.pageOrderNewest;
    event.currentTarget.textContent = state.pageOrderNewest ? "Hoạt động gần đây" : "Cũ nhất";
    renderPageList(el.pageSearch.value, { snapToActive: true });
  });
  document.getElementById("new-page")?.addEventListener("click", () => {
    void openNewPage().then(armIdle);
  });
  document.getElementById("page-list-prev")?.addEventListener("click", () => {
    state.listPage -= 1;
    renderPageList(el.pageSearch.value);
  });
  document.getElementById("page-list-next")?.addEventListener("click", () => {
    state.listPage += 1;
    renderPageList(el.pageSearch.value);
  });
  el.openSidebar.addEventListener("click", toggleDrawer);
  el.closeSidebar.addEventListener("click", closeDrawer);
  el.scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key.toLocaleLowerCase() === "n" && document.activeElement !== el.pageSearch && document.activeElement !== el.line) {
      event.preventDefault();
      document.getElementById("new-page")?.click();
    }
    if (event.key === "/" && document.activeElement !== el.pageSearch && document.activeElement !== el.line) {
      event.preventDefault();
      openSearch();
    }
    if (event.key === "Escape") {
      if (document.activeElement === el.pageSearch) {
        el.pageSearch.value = "";
        renderPageList("");
        closeSearch();
        event.preventDefault();
        return;
      }
      closeDrawer();
      el.pageSearch.blur();
    }
  });
  document.getElementById("focus-mode").addEventListener("click", (event) => {
    const focused = el.appShell.classList.toggle("is-focus-mode");
    event.currentTarget.textContent = focused ? "Hiện mục lục" : "Tập trung";
    event.currentTarget.setAttribute("aria-pressed", String(focused));
  });
  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitLine();
  });
  el.line.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
    if (event.shiftKey) return;
    event.preventDefault();
    el.form.requestSubmit();
  });
  el.line.addEventListener("input", () => {
    if (el.line.value.trim()) clearError();
    armIdle();
  });
  el.idleRemember?.addEventListener("click", () => {
    hideIdle();
    idleFromNudge = true;
    el.line.value = IDLE_REMEMBER;
    void submitLine();
  });
  el.idleDismiss?.addEventListener("click", () => {
    armIdle();
  });
}

bind();
hideDrawerIfMobile();

async function bootProviderGate() {
  const status = await getJson("/api/config/status");
  if (!status || status.ready !== false) return;
  state.chatLive = false;
  el.line.disabled = true;
  el.send.disabled = true;
  el.hint.textContent = "Cần cấu hình provider trước khi chat.";
  el.hint.className = "hint is-error";
  await renderModeSettings();
  // Re-check when returning to chat: a saved config may have made it ready.
  const fresh = await getJson("/api/config/status");
  if (fresh && fresh.ready !== false) {
    state.chatLive = true;
    el.line.disabled = false;
    el.send.disabled = false;
    el.hint.textContent = "";
    el.hint.className = "hint";
  }
}

void bootProviderGate();
renderMode("chat");
