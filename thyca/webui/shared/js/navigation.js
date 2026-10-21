(() => {
try {
  const prefs = JSON.parse(localStorage.getItem("thyca.ui.preferences") || "null");
  if (prefs && typeof prefs === "object") {
    const root = document.documentElement;
    if (prefs.theme) root.dataset.theme = prefs.theme;
    if (prefs.typeScale) root.dataset.typeScale = prefs.typeScale;
    root.dataset.reduceMotion = prefs.reduceMotion ? "on" : "off";
    root.dataset.sprigs = prefs.showSprig === false ? "off" : "on";
    if (prefs.language) root.lang = prefs.language;
  }
} catch {
  // Local preferences are optional; navigation must still open.
}

const routes = [
  ["index.html", "Trò chuyện", "M4 5h16v11H9l-5 4V5Z"],
  ["memories.html", "Nhật ký", "M12 6C9 4 6 4 3 5v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"],
  ["profile.html", "Hồ sơ", "M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm-7 18a7 7 0 0 1 14 0"],
  ["dashboard.html", "Dashboard", "M4 20V10h4v9M10 20V5h4v14M16 19v-7h4v7"],
  ["settings.html", "Cài đặt", "M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Zm3 5a4 4 0 1 0 0 8 4 4 0 0 0 0-8"],
];

const menuButtons = document.querySelectorAll(".menu-button");
if (menuButtons.length) {
  const dialog = document.createElement("dialog");
  dialog.id = "screen-navigation";
  dialog.className = "screen-dialog";
  dialog.setAttribute("aria-labelledby", "screen-navigation-title");
  dialog.innerHTML = `
    <div class="screen-heading">
      <h2 id="screen-navigation-title">Mục lục</h2>
      <button class="icon-button" type="button" aria-label="Đóng mục lục" autofocus>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>
      </button>
    </div>
    <nav class="screen-nav" aria-label="Các màn Thyca"></nav>`;
  const nav = dialog.querySelector("nav");
  const currentPage = location.pathname.split("/").pop() || "index.html";
  const compact = matchMedia("(max-width: 56rem)");
  const botIdPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

  function scopeLink(botId, botName) {
    const link = document.createElement("a");
    link.className = "memory-scope-choice";
    link.href = `./memories.html${botId ? `?bot_id=${encodeURIComponent(botId)}` : ""}`;
    link.textContent = botName;
    const currentIds = new URLSearchParams(location.search).getAll("bot_id");
    if (currentPage === "memories.html"
      && (botId ? currentIds.length === 1 && currentIds[0] === botId : currentIds.length === 0)) {
      link.setAttribute("aria-current", "page");
    }
    link.addEventListener("click", (event) => {
      if (currentPage !== "memories.html") return;
      const selection = new CustomEvent("thyca:memory-scope", {
        detail: { botId, botName: botId ? botName : "" },
      });
      document.dispatchEvent(selection);
      if (selection.detail.accepted === true) {
        event.preventDefault();
        dialog.close();
      } else if (selection.detail.accepted === false) {
        event.preventDefault();
        const submenu = link.parentNode;
        submenu.querySelector(".memory-scope-feedback")?.remove();
        const status = document.createElement("p");
        status.className = "memory-scope-state memory-scope-feedback is-error";
        status.setAttribute("role", "status");
        status.textContent = "Phạm vi chưa đổi được. Hãy hoàn tất thao tác đang chờ rồi thử lại.";
        submenu.append(status);
      }
    });
    return link;
  }

  function scopeState(message, error = false) {
    const note = document.createElement("p");
    note.className = `memory-scope-state${error ? " is-error" : ""}`;
    note.setAttribute("role", error ? "alert" : "status");
    note.textContent = message;
    return note;
  }

  for (const [path, label, icon] of routes) {
    const link = document.createElement("a");
    link.href = `./${path}`;
    link.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icon}"/></svg><span></span>`;
    link.querySelector("span").textContent = label;
    if (path === currentPage || (path === "settings.html" && currentPage === "provider.html")) {
      link.setAttribute("aria-current", "page");
    }
    if (path !== "memories.html") {
      nav.append(link);
      continue;
    }

    const entry = document.createElement("div");
    entry.className = "screen-nav-entry memory-navigation-entry";
    const submenu = document.createElement("div");
    submenu.id = "screen-navigation-memory-scopes";
    submenu.className = "memory-scope-submenu";
    submenu.setAttribute("role", "group");
    submenu.setAttribute("aria-label", "Phạm vi Nhật ký");
    submenu.hidden = true;
    link.setAttribute("aria-expanded", "false");
    link.setAttribute("aria-controls", submenu.id);

    let botsLoaded = false;
    let loadingBots = false;
    async function loadBots() {
      if (botsLoaded || loadingBots) return;
      loadingBots = true;
      submenu.replaceChildren(scopeLink(null, "Tổng quan"), scopeState("Đang tải danh sách bot…"));
      try {
        const response = await fetch("/api/bots", { cache: "no-store" });
        if (!response.ok) throw new Error("Bot list unavailable");
        const payload = await response.json();
        const bots = Array.isArray(payload) ? payload : payload?.bots;
        if (!Array.isArray(bots)) throw new Error("Invalid bot list");
        const choices = bots
          .filter((bot) => bot && typeof bot.id === "string"
            && bot.id.length <= 64 && botIdPattern.test(bot.id))
          .map((bot) => scopeLink(bot.id, String(bot.name || bot.id)));
        submenu.replaceChildren(scopeLink(null, "Tổng quan"), ...choices);
        if (!choices.length) submenu.append(scopeState("Chưa có bot nào."));
        botsLoaded = true;
      } catch {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "memory-scope-choice";
        retry.textContent = "Thử tải lại danh sách bot";
        retry.addEventListener("click", () => void loadBots());
        submenu.replaceChildren(
          scopeLink(null, "Tổng quan"),
          scopeState("Không tải được danh sách bot.", true),
          retry,
        );
      } finally {
        loadingBots = false;
      }
    }

    link.addEventListener("click", (event) => {
      if (!compact.matches) return;
      event.preventDefault();
      submenu.hidden = !submenu.hidden;
      link.setAttribute("aria-expanded", String(!submenu.hidden));
      if (!submenu.hidden) void loadBots();
    });
    compact.addEventListener("change", () => {
      if (compact.matches) return;
      submenu.hidden = true;
      link.setAttribute("aria-expanded", "false");
    });
    entry.append(link, submenu);
    nav.append(entry);
  }
  document.body.append(dialog);

  let opener;
  let openedByPointer = false;
  for (const button of menuButtons) {
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-controls", dialog.id);
    button.addEventListener("pointerdown", () => {
      openedByPointer = true;
    });
    button.addEventListener("keydown", () => {
      openedByPointer = false;
    });
    button.addEventListener("click", () => {
      opener = button;
      dialog.showModal();
    });
  }
  dialog.querySelector("button").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
      dialog.close();
    }
  });
  dialog.addEventListener("close", () => {
    if (!opener) return;
    if (openedByPointer) opener.blur();
    else opener.focus();
    openedByPointer = false;
  });
}

const headerTime = document.querySelector("#header-datetime");
const headerEdition = document.querySelector("#header-edition");
if (headerTime) {
  const now = new Date();
  headerTime.dateTime = now.toISOString();
  headerTime.textContent = new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(now);
}
if (headerEdition) {
  fetch("/api/config/status", { cache: "no-store" })
    .then((response) => response.json())
    .then((data) => {
      const version = String(data?.version || "").trim();
      headerEdition.textContent = version ? `Thyca · phiên bản ${version}` : "Thyca";
    })
    .catch(() => {
      headerEdition.textContent = "Thyca";
    });
}

const settingsShell = document.querySelector(".settings-shell");
if (settingsShell) {
  const compact = matchMedia("(max-width: 56rem)");
  const file = location.pathname.split("/").pop() || "settings.html";
  let chosen = file === "provider.html" || Boolean(location.hash);
  const sync = () => {
    settingsShell.classList.toggle("is-picking", compact.matches && !chosen);
  };
  settingsShell.querySelectorAll(".session-list a").forEach((link) => {
    link.addEventListener("click", (event) => {
      if (!compact.matches || file !== "settings.html") return;
      const href = link.getAttribute("href") || "";
      if (href.includes("provider.html")) return;
      event.preventDefault();
      chosen = true;
      const hash = href.includes("#") ? href.slice(href.indexOf("#")) : "#chung";
      history.replaceState(null, "", hash);
      if (hash !== "#chung") document.querySelector(hash)?.scrollIntoView();
      sync();
    });
  });
  document.querySelector(".settings-shell .screen-back")?.addEventListener("click", () => {
    if (file === "provider.html") {
      location.href = "settings.html";
      return;
    }
    chosen = false;
    history.replaceState(null, "", "settings.html");
    sync();
  });
  compact.addEventListener("change", sync);
  sync();
}

for (const wrap of document.querySelectorAll(".thyca-search")) {
  const input = wrap.querySelector(".thyca-search__input");
  const clear = wrap.querySelector(".thyca-search__clear");
  if (!input) continue;
  const sync = () => wrap.classList.toggle("is-filled", Boolean(input.value));
  input.addEventListener("input", sync);
  input.addEventListener("search", sync);
  clear?.addEventListener("click", () => {
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    sync();
  });
  sync();
}
})();
