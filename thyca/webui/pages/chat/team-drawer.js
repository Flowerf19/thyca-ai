import { botMark } from "../../shared/js/bot-avatar.js";
import { tintVarFor } from "./team-data.js";

// Bot profile drawer (r9 §4.6): read-only. Anything the user wants changed
// goes through "Nhắn Thyca" (prefills the composer). Technical specs live
// under a "Nâng cao" <details>. Opens from the participant strip and the
// Settings › Bot list; shows an honest empty state when the registry is
// unavailable.
let hub = null;

function closeButton(dialog) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-button bot-drawer-close";
  button.setAttribute("aria-label", "Đóng hồ sơ bot");
  button.textContent = "✕";
  button.addEventListener("click", () => dialog.close());
  return button;
}

function specRows(bot) {
  const rows = [];
  const push = (label, value) => {
    if (value == null || value === "" || (Array.isArray(value) && !value.length)) return;
    rows.push([label, Array.isArray(value) ? value.join(", ") : String(value)]);
  };
  push("ID", bot.id);
  push("Vai trò", bot.role);
  push("Avatar", bot.avatar);
  push("Model", bot.model);
  push("Quyền chạy lệnh", bot.bash);
  push("Kỹ năng", bot.skills);
  push("Lịch chạy", bot.schedule);
  push("Trạng thái", bot.status);
  return rows;
}

export function renderBotDrawer(bot) {
  const dialog = document.querySelector("#bot-drawer");
  const body = document.querySelector("#bot-drawer-body");
  if (!dialog || !body) return;
  body.replaceChildren();
  const data = bot && typeof bot === "object" ? bot : null;
  const head = document.createElement("header");
  head.className = "bot-drawer-head";
  head.style.setProperty("--team-tint", tintVarFor(data?.id, data?.tint));
  if (data) {
    const avatar = document.createElement("span");
    avatar.className = "bot-drawer-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.innerHTML = botMark(data);
    head.append(avatar);
  }
  const heading = document.createElement("div");
  heading.className = "bot-drawer-heading";
  const name = document.createElement("h2");
  name.id = "bot-drawer-name";
  name.textContent = data?.name || data?.id || "Chưa có hồ sơ bot";
  heading.append(name);
  if (data?.role) {
    const role = document.createElement("p");
    role.className = "bot-drawer-role";
    role.textContent = data.role;
    heading.append(role);
  }
  head.append(heading);
  head.append(closeButton(dialog));
  body.append(head);

  if (!data) {
    const empty = document.createElement("p");
    empty.className = "screen-note";
    empty.textContent = "Backend đội bot chưa sẵn sàng nên chưa có hồ sơ để xem.";
    body.append(empty);
  } else {
    if (data.description) {
      const desc = document.createElement("p");
      desc.className = "bot-drawer-desc";
      desc.textContent = data.description;
      body.append(desc);
    }
    const can = document.createElement("section");
    can.className = "bot-drawer-section";
    const canTitle = document.createElement("h3");
    canTitle.textContent = "Làm được gì";
    can.append(canTitle);
    // No invented abilities: when the registry supplies none, say so.
    if (Array.isArray(data.abilities) && data.abilities.length) {
      const canList = document.createElement("ul");
      for (const item of data.abilities) {
        const row = document.createElement("li");
        row.textContent = String(item);
        canList.append(row);
      }
      can.append(canList);
    } else {
      const note = document.createElement("p");
      note.className = "screen-note";
      note.textContent = "Chưa có dữ liệu về khả năng của bot này.";
      can.append(note);
    }
    const cantTitle = document.createElement("h3");
    cantTitle.textContent = "Không làm";
    can.append(cantTitle);
    if (Array.isArray(data.limits) && data.limits.length) {
      const cantList = document.createElement("ul");
      for (const item of data.limits) {
        const row = document.createElement("li");
        row.textContent = String(item);
        cantList.append(row);
      }
      can.append(cantList);
    } else {
      const note = document.createElement("p");
      note.className = "screen-note";
      note.textContent = "Chưa có dữ liệu về giới hạn của bot này.";
      can.append(note);
    }
    body.append(can);

    const advanced = document.createElement("details");
    advanced.className = "bot-drawer-advanced";
    const summary = document.createElement("summary");
    summary.textContent = "Nâng cao";
    advanced.append(summary);
    const specs = specRows(data);
    if (!specs.length) {
      const note = document.createElement("p");
      note.className = "screen-note";
      note.textContent = "Chưa có thông số kỹ thuật.";
      advanced.append(note);
    } else {
      const table = document.createElement("dl");
      table.className = "bot-drawer-specs";
      for (const [label, value] of specs) {
        const term = document.createElement("dt");
        term.textContent = label;
        const def = document.createElement("dd");
        def.textContent = value;
        table.append(term, def);
      }
      advanced.append(table);
    }
    body.append(advanced);
  }

  const foot = document.createElement("footer");
  foot.className = "bot-drawer-foot";
  const cta = document.createElement("button");
  cta.type = "button";
  cta.className = "screen-button is-primary";
  cta.textContent = "Nhắn Thyca";
  cta.addEventListener("click", () => {
    dialog.close();
    hub?.messageThycaAbout?.(data?.id || "");
  });
  foot.append(cta);
  const hint = document.createElement("p");
  hint.className = "screen-note";
  hint.textContent = data
    ? "Muốn đổi gì ở bot này thì nhắn Thyca — hồ sơ ở đây chỉ để đọc."
    : "Khi đội bot sẵn sàng, hồ sơ từng bot sẽ hiện ở đây.";
  foot.append(hint);
  body.append(foot);

  if (typeof dialog.showModal === "function") dialog.showModal();
}

export function openBotDrawer(botId) {
  const bot = hub?.teamRoster?.().find?.((item) => item.id === String(botId || "")) || null;
  renderBotDrawer(bot);
}

export function initTeamDrawer(entryHub) {
  hub = entryHub;
}
