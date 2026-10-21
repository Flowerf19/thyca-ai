(() => {
  const STORAGE_KEY = "thyca.ui.preferences";
  const root = document.documentElement;
  const controls = {
    theme: document.getElementById("theme"),
    typeScale: document.getElementById("type-scale"),
    language: document.getElementById("language"),
    reduceMotion: document.getElementById("reduce-motion"),
    showSprig: document.getElementById("show-sprig"),
  };
  const defaults = {
    theme: "paper",
    typeScale: "medium",
    language: "vi",
    reduceMotion: false,
    showSprig: true,
  };

  const read = () => {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      return parsed && typeof parsed === "object" ? { ...defaults, ...parsed } : defaults;
    } catch {
      return defaults;
    }
  };

  const values = () => ({
    theme: controls.theme.value,
    typeScale: controls.typeScale.value,
    language: controls.language.value,
    reduceMotion: controls.reduceMotion.checked,
    showSprig: controls.showSprig.checked,
  });

  const apply = (next = values(), { save = true } = {}) => {
    root.dataset.theme = next.theme;
    root.dataset.typeScale = next.typeScale;
    root.dataset.reduceMotion = next.reduceMotion ? "on" : "off";
    root.dataset.sprigs = next.showSprig ? "on" : "off";
    root.lang = next.language;
    if (save) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        // The visual controls still apply when storage is unavailable.
      }
    }
  };

  const hydrate = (next) => {
    controls.theme.value = next.theme;
    controls.typeScale.value = next.typeScale;
    controls.language.value = next.language;
    controls.reduceMotion.checked = Boolean(next.reduceMotion);
    controls.showSprig.checked = next.showSprig !== false;
    apply(next, { save: false });
  };

  Object.values(controls).forEach((control) => {
    control.addEventListener("change", () => apply());
  });
  document.querySelector(".settings-reset")?.addEventListener("click", () => {
    hydrate(defaults);
    apply(defaults);
  });

  hydrate(read());
})();

/* Team v1 (FE-4): Cài đặt › Bot — read-only roster from GET /api/bots.
   Separate IIFE so the preferences module above stays untouched. Changes go
   through Thyca in chat; each row links there. Registry unavailable →
   honest "chưa sẵn sàng" line, never fake rows. */
(() => {
  const STATUS = { running: "đang làm", idle: "rảnh", active: "đang bật", paused: "tạm dừng", error: "lỗi" };

  const list = document.getElementById("bot-list");
  const status = document.getElementById("bot-list-status");
  if (!list || !status) return; // not on settings.html

  // Wax-seal avatar marks (shared renderer, loaded as a module; this file
  // stays a classic script). Rows render immediately and the mark fills in
  // when the module lands.
  let renderAvatar = null;
  let botLookFor = null;
  const avatarNodes = new Map();
  const fillAvatar = (bot, node) => {
    if (!node || !botLookFor) return;
    const tint = `var(--color-${botLookFor(bot).tint})`;
    node.style.setProperty("--team-tint", tint);
    node.parentElement?.style.setProperty("--team-tint", tint);
    if (renderAvatar) node.innerHTML = renderAvatar(bot);
  };
  import("../../shared/js/bot-avatar.js")
    .then((mod) => {
      renderAvatar = mod.botMark;
      botLookFor = mod.botLookFor;
      for (const { bot, node } of avatarNodes.values()) fillAvatar(bot, node);
    })
    .catch((error) => {
      console.error("Không tải được biểu tượng bot trên trang cài đặt.", error);
    });

  const note = (text) => {
    status.textContent = text;
  };

  fetch("/api/bots", { headers: { Accept: "application/json" } })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((payload) => {
      const raw = Array.isArray(payload) ? payload : payload?.bots;
      const bots = (Array.isArray(raw) ? raw : []).filter(
        (bot) => bot && typeof bot.id === "string" && bot.id,
      );
      if (!bots.length) {
        note("Đội bot chưa sẵn sàng — mới có Thyca.");
        return;
      }
      list.replaceChildren();
      for (const bot of bots) {
        const row = document.createElement("article");
        row.className = "fold-row settings-row";
        const info = document.createElement("div");
        const name = document.createElement("h3");
        name.textContent = bot.name || bot.id;
        const sub = document.createElement("p");
        sub.className = "screen-muted";
        const parts = [bot.role, bot.system ? "luôn bật" : STATUS[bot.status]].filter(Boolean);
        sub.textContent = parts.length ? parts.join(" · ") : bot.id;
        info.append(name, sub);
        const identity = document.createElement("div");
        identity.className = "settings-field";
        const avatar = document.createElement("span");
        avatar.className = "settings-bot-avatar";
        avatar.setAttribute("aria-hidden", "true");
        avatarNodes.set(bot.id, { bot, node: avatar });
        identity.append(avatar, info);
        fillAvatar(bot, avatar);
        const link = document.createElement("a");
        link.className = "screen-button";
        link.href = "./index.html";
        link.textContent = "Nhắn trong Chat";
        row.append(identity, link);
        list.append(row);
      }
    })
    .catch(() => {
      note("Đội bot chưa sẵn sàng — mới có Thyca.");
    });
})();
