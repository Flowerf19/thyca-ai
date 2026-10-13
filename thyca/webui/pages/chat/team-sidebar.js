import { getJson, postJson } from "../../shared/js/http.js";
import { cleanText, formatSessionTime } from "../../shared/js/format.js";
import { messageOf } from "../../shared/js/status.js";
import {
  TEAM_BOT_ID,
  findMainSession,
  isMainSessionDetail,
  isSharedSession,
  normalizeBots,
  partitionBotSessions,
  sessionsForBot,
  summarizeUnread,
  tintVarFor,
  verifySharedSession,
} from "./team-data.js";
import {
  getRememberedDm,
  getSeen,
  markSeen,
  rememberDm,
} from "./team-unread.js";

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
// Main-window (DM) resolve state for CFE-1: creation is async, level 2
// renders immediately with known sessions while the fixed DM resolves.
let mainResolving = false;
let mainError = "";

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

// Way back from the legacy list to the team view (the "Tất cả phiên"
// button above is otherwise a one-way door). Keeps the current level —
// returning from legacy must not reset where the user was browsing.
export function showTeamView() {
  if (!bots.length) return;
  render();
}

// The legacy heading is static HTML that sessions-sidebar never rebuilds
// (it only replaces #session-list children), so a button placed here
// survives every renderSessions() call.
function ensureLegacyBackButton() {
  const heading = document.querySelector("#chat-sidebar .sessions .sessions-heading");
  if (!heading || heading.querySelector("[data-team-back]")) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-action";
  button.dataset.teamBack = "1";
  button.textContent = "Đội bot";
  button.setAttribute("aria-label", "Về danh sách bot");
  button.addEventListener("click", showTeamView);
  heading.append(button);
}

function statusLabel(status) {
  return STATUS_LABEL[String(status || "")] || "";
}

/* Unread state for one session (CFE-2): first sight stores a silent baseline
   (never a mass unread); later renders compare the summary against it.
   Opening a session clears it (app.js marks seen in renderDetail). */
function sessionUnread(session) {
  const id = String(session?.id || "");
  if (!id) return { unread: false, count: 0 };
  const seen = getSeen(id);
  if (!seen) {
    markSeen(id, session);
    return { unread: false, count: 0 };
  }
  const result = summarizeUnread(seen, session);
  return { unread: result.unread, count: result.count };
}

function botUnread(botId) {
  let count = 0;
  let dot = false;
  for (const session of sessionsForBot(hub?.state?.sessions, botId)) {
    const state = sessionUnread(session);
    if (state.unread) {
      dot = true;
      count += state.count;
    }
  }
  return { unread: dot, count };
}

function unreadBadge(state) {
  if (!state.unread) return null;
  const badge = document.createElement("span");
  badge.className = "team-unread";
  if (state.count > 0) {
    badge.textContent = String(state.count);
    badge.setAttribute("aria-label", `${state.count} chưa đọc`);
  } else {
    badge.classList.add("is-dot");
    badge.textContent = "";
    badge.setAttribute("aria-label", "Có tin chưa đọc");
  }
  return badge;
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
  const unread = unreadBadge(botUnread(bot.id));
  if (unread) button.append(unread);
  const label = statusLabel(bot.status);
  if (label) {
    const badge = document.createElement("span");
    badge.className = `team-bot-status is-${bot.status}`;
    badge.textContent = label;
    button.append(badge);
  }
  const unreadSuffix = unread
    ? (unread.textContent ? `, ${unread.textContent} chưa đọc` : ", có tin chưa đọc")
    : "";
  button.setAttribute("aria-label", (label ? `${bot.name}, ${label}` : bot.name) + unreadSuffix);
  button.addEventListener("click", () => void openBot(bot.id));
  return button;
}

function sessionRow(session, { main = false } = {}) {
  const row = document.createElement("div");
  row.className = "session-row";
  if (main) row.classList.add("is-main");
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
  const shared = isSharedSession(session);
  time.textContent = main ? `${when} · Cửa sổ chính` : (shared ? `${when} · chung` : when);
  button.append(icon, name, time);
  const unread = unreadBadge(sessionUnread(session));
  if (unread) button.append(unread);
  button.addEventListener("click", () => {
    exitPicking();
    void hub.loadSession(button.dataset.sessionId);
  });
  row.append(button);
  return row;
}

function exitPicking() {
  if (matchMedia("(max-width: 56rem)").matches) {
    document.querySelector(".chat-shell")?.classList.remove("is-picking");
  }
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
    // And when it answers while ignoring {bots}, verifySharedSession keeps
    // us from opening the plain session as a fake shared room.
    const detail = await postJson("/api/sessions", { bots: chosen });
    const check = verifySharedSession(detail, chosen);
    if (!check.ok) {
      sharedError = check.missing.length
        ? `Backend chưa hỗ trợ phiên chung (thiếu ${check.missing.join(", ")} trong phiên mới) — chưa mở phiên chung.`
        : "Backend chưa hỗ trợ phiên chung — chưa mở phiên chung.";
      render();
      return;
    }
    sharedOpen = false;
    sharedPicked = null;
    await hub.afterTeamSession(detail);
  } catch (error) {
    sharedError = messageOf(error, "Chưa tạo được phiên chung — backend đội bot chưa sẵn sàng.");
    render();
  }
}

/* CFE-1: clicking a bot opens its fixed DM/main session directly. Backend
   provides no DM marker on this branch, so the main window resolves
   client-side: remembered solo session → newest solo session → created via
   the existing POST /api/sessions {bots:[bot]} semantics (verified with
   isMainSessionDetail, never a fake main). Level 2 renders immediately with
   known sessions while the DM resolves; the main window pins first. */
async function openBot(botId) {
  level = 2;
  activeBot = botId;
  sharedOpen = false;
  sharedError = "";
  sharedPicked = null;
  mainError = "";
  mainResolving = true;
  render();
  await resolveMain(botId);
}

async function resolveMain(botId) {
  try {
    if (!hub) return;
    const main = findMainSession(hub.state.sessions, botId, getRememberedDm(botId));
    if (main?.id) {
      rememberDm(botId, main.id);
      if (activeBot !== botId || level !== 2) return;
      mainResolving = false;
      render();
      exitPicking();
      await hub.loadSession(String(main.id));
      return;
    }
    // No solo session yet: create the fixed DM. POST answers the full
    // detail including bots, so the guard below keeps a backend that
    // ignores {bots} from becoming a fake main window.
    const detail = await postJson("/api/sessions", { bots: [botId] });
    if (!isMainSessionDetail(detail, botId)) {
      mainError = "Backend chưa trả về cửa sổ riêng cho bot này — chưa mở cửa sổ chính.";
      return;
    }
    rememberDm(botId, detail.id);
    if (activeBot !== botId || level !== 2) return;
    exitPicking();
    await hub.afterTeamSession(detail);
  } catch (error) {
    if (activeBot !== botId || level !== 2) return;
    mainError = messageOf(error, "Chưa mở được cửa sổ chính của bot — backend đội bot chưa sẵn sàng.");
  } finally {
    if (activeBot === botId && level === 2) {
      mainResolving = false;
      render();
    } else {
      mainResolving = false;
    }
  }
}

function openTeam() {
  level = 1;
  activeBot = "";
  sharedOpen = false;
  sharedError = "";
  sharedPicked = null;
  mainResolving = false;
  mainError = "";
  render();
}

export function renderTeamSidebar() {
  render();
}

function render() {
  const section = teamSection();
  if (!section || !bots.length) return;
  showTeam();
  ensureLegacyBackButton();
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
    const main = findMainSession(hub.state.sessions, activeBot, getRememberedDm(activeBot));
    const { main: pinned, others } = partitionBotSessions(
      hub.state.sessions, activeBot, main?.id,
    );
    if (mainError) {
      const error = document.createElement("p");
      error.className = "screen-status is-error";
      error.setAttribute("role", "alert");
      error.textContent = mainError;
      list?.append(error);
    }
    if (pinned) list?.append(sessionRow(pinned, { main: true }));
    else if (mainResolving) {
      const resolving = document.createElement("p");
      resolving.className = "sidebar-state";
      resolving.textContent = "Đang mở cửa sổ chính…";
      list?.append(resolving);
    }
    if (others.length) {
      if (pinned) {
        const sub = document.createElement("p");
        sub.className = "team-secondary-title";
        sub.textContent = "Phiên khác";
        list?.append(sub);
      }
      for (const session of others) list?.append(sessionRow(session));
    } else if (!pinned && !mainResolving) {
      const empty = document.createElement("p");
      empty.className = "sidebar-state";
      empty.textContent = bot
        ? `${bot.name} chưa có phiên nào — nhắn trong phiên hiện tại để bắt đầu.`
        : "Bot này chưa có phiên nào.";
      list?.append(empty);
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
    // The note lives AFTER #session-list — renderSessions() rebuilds that
    // nav on every load (replaceChildren), so anything inside it is wiped.
    const nav = document.querySelector("#session-list");
    if (nav && !nav.parentElement?.querySelector(".team-unavailable")) {
      const note = document.createElement("p");
      note.className = "sidebar-state team-unavailable";
      note.textContent = "Đội bot chưa sẵn sàng — hiện tất cả phiên.";
      nav.after(note);
    }
    return;
  }
  armTeamRefresh();
  render();
}

/* Background bot writes (scheduler DM notes, turns finished in another tab)
   only surface when the session list reloads: refresh on window focus and
   on return to the tab, team mode only. The legacy list path never arms
   this (bots is empty without GET /api/bots). */
let refreshArmed = false;
function armTeamRefresh() {
  if (refreshArmed) return;
  refreshArmed = true;
  const refresh = () => {
    if (document.hidden || !bots.length || teamSection()?.hidden) return;
    void hub?.teamRefresh?.();
  };
  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", refresh);
}

// After a team session is created elsewhere (or sessions reload), the level-2
// list must follow the newest state.
export function syncTeamSessions() {
  if (bots.length && !teamSection()?.hidden) render();
}
