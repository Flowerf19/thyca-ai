import { createChatSession, sendChatTurn } from "./chat.js";
import { el } from "./dom.js";
import { closeDrawer, hideDrawerIfMobile, toggleDrawer } from "./drawer.js";
import { renderMode, renderPage, renderPageList, setTracePlaying } from "./render.js";
import { state } from "./state.js";

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
  if (busy) {
    el.hint.textContent = "Thyca đang nghĩ…";
    el.hint.className = "hint is-loading";
  }
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
  setBusy(true);
  try {
    if (state.chatLive) {
      await sendChatTurn(text);
      renderPage(state.activePageIndex);
    } else {
      const wait = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 650;
      await new Promise((resolve) => window.setTimeout(resolve, wait));
    }
    el.field.classList.add("is-success");
    el.hint.textContent = "Đã gửi vào phiên này.";
    el.hint.className = "hint is-success";
    el.line.value = "";
  } catch (error) {
    showError(error instanceof Error ? error.message : "Không gửi được.");
  } finally {
    setBusy(false);
    el.line.focus();
  }
}

function bind() {
  el.modeList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (button) renderMode(button.dataset.mode);
  });
  el.pageSearch.addEventListener("input", () => renderPageList(el.pageSearch.value));
  document.getElementById("sort-pages").addEventListener("click", (event) => {
    state.pageOrderNewest = !state.pageOrderNewest;
    event.currentTarget.textContent = state.pageOrderNewest ? "Mới nhất" : "Cũ nhất";
    el.pageList.classList.toggle("is-reversed", !state.pageOrderNewest);
  });
  document.getElementById("new-page").addEventListener("click", () => {
    void openNewPage();
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
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      el.form.requestSubmit();
    }
  });
  el.line.addEventListener("input", () => {
    if (el.line.value.trim()) clearError();
  });
  el.miniPlay.addEventListener("click", () => {
    setTracePlaying(el.miniPlay.getAttribute("aria-pressed") !== "true");
  });
}

bind();
hideDrawerIfMobile();
renderMode("chat");
