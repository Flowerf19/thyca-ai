import { getJson, postJson } from "../../shared/js/http.js";
import { cleanText, formatSessionTime } from "../../shared/js/format.js";
import { messageOf } from "../../shared/js/status.js";
import {
  TEAM_BOT_ID,
  isSharedSession,
  normalizeBots,
  sessionsForBot,
  tintVarFor,
} from "./team-data.js";

// Team sidebar: two levels Bot → Session (master plan §7 FE-1). Progressive
// enhancement over sessions-sidebar.js: when GET /api/bots answers, the team
// section replaces the legacy session list; when the backend is not there
// yet, the legacy list stays exactly as today plus one muted note.
let hub = null;
let bots = [];
let level = 1;
let activeBot = "";
let sharedOpen = false;
let sharedError = "";
// Checkbox picks survive re-renders while the picker stays open.
let sharedPicked = null;

const STATUS_LABEL = {
  running: "đang làm",
  idle: "rảnh",
  paused: "tạm dừng",
  error: "lỗi",
};

function teamSection() {
  return document.querySelector("#team-nav");
}

function legacySection() {
  return document.querySelector("#chat-sidebar .sessions");
}

function showTeam() {
  teamSection()?.removeAttribute("hidden");
  const legacy = legacySection();
  if (legacy) legacy.hidden = true;
}

function showLegacy() {
  teamSection()?.setAttribute("hidden", "");
  const legacy = legacySection();
  if (legacy) legacy.hidden = false;
}

function statusLabel(status) {
  return STATUS_LABEL[String(status || "")] || "";
}

function botRow(bot) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "team-bot-item";
  button.dataset.botId = bot.id;
  if (bot.id === activeBot && level === 2) button.classList.add("is-active");
  const dot = document.createElement("span");
  dot.className = "team-bot-dot";
  dot.style.setProperty("--team-tint", tintVarFor(bot.id, bot.tint));
  dot.setAttribute("aria-hidden", "true");
  const name = document.createElement("span");
  name.className = "team-bot-name";
  name.textContent = bot.name;
  button.append(dot, name);
  const label = statusLabel(bot.status);
  if (label) {
    const badge = document.createElement("span");
    badge.className = `team-bot-status is-${bot.status}`;
    badge.textContent = label;
    button.append(badge);
  }
  button.setAttribute("aria-label", label ? `${bot.name}, ${label}` : bot.name);
  button.addEventListener("click", () => openBot(bot.id));
  return button;
}

function sessionRow(session) {
  const row = document.createElement("div");
  row.className = "session-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-item";
  button.dataset.sessionId = String(session.id || "");
  const selected = button.dataset.sessionId === hub.state.activeId;
  button.classList.toggle("is-active", selected);
  if (selected) button.setAttribute("aria-current", "page");
  const icon = document.createElement("span");
  icon.className = "session-icon";
  icon.setAttribute("aria-hidden", "true");
  const name = document.createElement("span");
  name.className = "session-name";
  name.textContent = cleanText(session.title, "Phiên trống");
  const time = document.createElement("time");
  time.dateTime = String(session.updated_at || "");
  const when = formatSessionTime(session.updated_at);
  time.textContent = isSharedSession(session) ? `${when} · chung` : when;
  button.append(icon, name, time);
  button.addEventListener("click", () => {
    if (matchMedia("(max-width: 56rem)").matches) {
      document.querySelector(".chat-shell")?.classList.remove("is-picking");
    }
    void hub.loadSession(button.dataset.sessionId);
  });
  row.append(button);
  return row;
}

function sharedPicker() {
  const wrap = document.createElement("div");
  wrap.className = "team-shared-picker";
  if (!sharedOpen) return wrap;
  const title = document.createElement("p");
  title.className = "team-shared-title";
  title.textContent = "Chọn bot cho phiên chung";
  wrap.append(title);
  if (!sharedPicked) sharedPicked = new Set(hub.state.detail?.bots || [activeBot || TEAM_BOT_ID]);
  const picked = sharedPicked;
  for (const bot of bots) {
    const label = document.createElement("label");
    label.className = "team-shared-option";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = bot.id;
    box.checked = picked.has(bot.id);
    box.addEventListener("change", () => {
      if (box.checked) picked.add(bot.id);
      else picked.delete(bot.id);
    });
    const dot = document.createElement("span");
    dot.className = "team-bot-dot";
    dot.style.setProperty("--team-tint", tintVarFor(bot.id, bot.tint));
    dot.setAttribute("aria-hidden", "true");
    label.append(box, dot, document.createTextNode(bot.name));
    wrap.append(label);
  }
  if (sharedError) {
    const error = document.createElement("p");
    error.className = "screen-status is-error";
    error.setAttribute("role", "alert");
    error.textContent = sharedError;
    wrap.append(error);
  }
  const create = document.createElement("button");
  create.type = "button";
  create.className = "screen-button is-primary";
  create.textContent = "Tạo phiên chung";
  create.addEventListener("click", () => void createShared([...picked]));
  wrap.append(create);
  return wrap;
}

async function createShared(botIds) {
  sharedError = "";
  const chosen = botIds.filter(Boolean);
  if (chosen.length < 2) {
    sharedError = "Phiên chung cần ít nhất hai bot.";
    render();
    return;
  }
  try {
    // Contract §6: POST /api/sessions accepts {bots}. The backend on main
    // does not yet — the honest failure below is the unavailable state.
    const detail = await postJson("/api/sessions", { bots: chosen });
    sharedOpen = false;
    sharedPicked = null;
    await hub.afterTeamSession(detail);
  } catch (error) {
    sharedError = messageOf(error, "Chưa tạo được phiên chung — backend đội bot chưa sẵn sàng.");
    render();
  }
}

function openBot(botId) {
  level = 2;
  activeBot = botId;
  sharedOpen = false;
  sharedError = "";
  sharedPicked = null;
  render();
}

function openTeam() {
  level = 1;
  activeBot = "";
  sharedOpen = false;
  sharedError = "";
  sharedPicked = null;
  render();
}

export function renderTeamSidebar() {
  render();
}

function render() {
  const section = teamSection();
  if (!section || !bots.length) return;
  showTeam();
  const list = section.querySelector("#team-bot-list");
  list?.replaceChildren();
  if (level === 1) {
    const head = section.querySelector("#team-nav-title");
    if (head) head.textContent = "Đội";
    const back = section.querySelector("#team-nav-back");
    if (back) back.hidden = true;
    for (const bot of bots) list?.append(botRow(bot));
    const actions = section.querySelector("#team-nav-actions");
    actions?.replaceChildren(sharedToggleButton(), allSessionsButton(), sharedPicker());
  } else {
    const bot = bots.find((item) => item.id === activeBot);
    const head = section.querySelector("#team-nav-title");
    if (head) head.textContent = bot ? bot.name : "Đội";
    const back = section.querySelector("#team-nav-back");
    if (back) {
      back.hidden = false;
      back.onclick = openTeam;
    }
    const mine = sessionsForBot(hub.state.sessions, activeBot);
    if (!mine.length) {
      const empty = document.createElement("p");
      empty.className = "sidebar-state";
      empty.textContent = bot
        ? `${bot.name} chưa có phiên nào — nhắn trong phiên hiện tại để bắt đầu.`
        : "Bot này chưa có phiên nào.";
      list?.append(empty);
    } else {
      for (const session of mine) list?.append(sessionRow(session));
    }
    const actions = section.querySelector("#team-nav-actions");
    actions?.replaceChildren(sharedToggleButton(), allSessionsButton(), sharedPicker());
  }
}

function sharedToggleButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "screen-button";
  button.textContent = sharedOpen ? "Đóng" : "＋ Phiên chung";
  button.setAttribute("aria-expanded", String(sharedOpen));
  button.addEventListener("click", () => {
    sharedOpen = !sharedOpen;
    sharedError = "";
    render();
  });
  return button;
}

function allSessionsButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-action";
  button.textContent = "Tất cả phiên";
  button.setAttribute("aria-label", "Xem tất cả phiên");
  button.addEventListener("click", showLegacy);
  return button;
}

export function teamBots() {
  return bots.slice();
}

export function teamBotById(id) {
  return bots.find((bot) => bot.id === String(id || "")) || null;
}

export async function initTeamSidebar(entryHub) {
  hub = entryHub;
  const section = teamSection();
  if (!section) return;
  let payload = null;
  try {
    payload = await getJson("/api/bots");
  } catch {
    payload = null;
  }
  bots = normalizeBots(payload);
  if (!bots.length) {
    // Backend has no team registry yet: legacy list stays, one muted note.
    const nav = document.querySelector("#session-list");
    if (nav && !nav.querySelector(".team-unavailable")) {
      const note = document.createElement("p");
      note.className = "sidebar-state team-unavailable";
      note.textContent = "Đội bot chưa sẵn sàng — hiện tất cả phiên.";
      nav.prepend(note);
    }
    return;
  }
  render();
}

// After a team session is created elsewhere (or sessions reload), the level-2
// list must follow the newest state.
export function syncTeamSessions() {
  if (bots.length && !teamSection()?.hidden) render();
}
