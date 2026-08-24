import { beginOutgoingTurn, createChatSession, getJson, removeStatus, sendChatTurn, settleIncoming, stopStatusCycle } from "./chat.js";
import { el } from "./dom.js";
import { closeDrawer, hideDrawerIfMobile, toggleDrawer } from "./drawer.js";
import { renderMode, renderPage, renderPageList, setTracePlaying } from "./render.js";
import { state } from "./state.js";

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
  if (state.chatLive) {
    try {
      await createChatSession();
    } catch (error) {
      showError(error instanceof Error ? error.message : "Không tạo được phiên.");
      return;
    }
  } else {
    await renderMode("chat");
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
      await sendChatTurn(text);
      if (state.activeMode !== "chat") {
        idleFromNudge = false;
        return;
      }
      if (!settleIncoming()) renderPage(state.activePageIndex);
      else renderPageList(el.pageSearch.value);
    } else {
      const wait = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 1000;
      await new Promise((resolve) => window.setTimeout(resolve, wait));
      removeStatus();
    }
    if (state.activeMode !== "chat") {
      idleFromNudge = false;
      return;
    }
    el.field.classList.add("is-success");
    el.hint.textContent = "Đã gửi vào phiên này.";
    el.hint.className = "hint is-success";
    noteSend();
    armIdle();
  } catch (error) {
    idleFromNudge = false;
    removeStatus();
    showError(error instanceof Error ? error.message : "Không gửi được.");
  } finally {
    setBusy(false);
    stopStatusCycle();
    el.line.focus();
  }
}

function bind() {
  el.modeList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (button) {
      renderMode(button.dataset.mode);
      armIdle();
    }
  });
  el.pageSearch.addEventListener("input", () => renderPageList(el.pageSearch.value));
  el.pageList.addEventListener("click", (event) => {
    if (event.target.closest(".page-card")) armIdle();
  });
  document.getElementById("sort-pages").addEventListener("click", (event) => {
    state.pageOrderNewest = !state.pageOrderNewest;
    event.currentTarget.textContent = state.pageOrderNewest ? "Mới nhất" : "Cũ nhất";
    el.pageList.classList.toggle("is-reversed", !state.pageOrderNewest);
  });
  document.getElementById("new-page").addEventListener("click", () => {
    void openNewPage().then(armIdle);
  });
  el.openSidebar.addEventListener("click", toggleDrawer);
  el.closeSidebar.addEventListener("click", closeDrawer);
  el.scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key.toLocaleLowerCase() === "n" && document.activeElement !== el.pageSearch && document.activeElement !== el.line) {
      event.preventDefault();
      document.getElementById("new-page").click();
    }
    if (event.key === "/" && document.activeElement !== el.pageSearch && document.activeElement !== el.line) {
      event.preventDefault();
      el.pageSearch.focus();
    }
    if (event.key === "Escape") {
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
  el.miniPlay.addEventListener("click", () => {
    setTracePlaying(el.miniPlay.getAttribute("aria-pressed") !== "true");
  });
}

bind();
hideDrawerIfMobile();
renderMode("chat");
