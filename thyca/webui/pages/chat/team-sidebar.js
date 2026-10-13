import { getJson, postJson } from "../../shared/js/http.js";
import { cleanText, formatSessionTime } from "../../shared/js/format.js";
import { messageOf } from "../../shared/js/status.js";
import {
  TEAM_BOT_ID,
  findMainSession,
  isDmDetail,
  isMainSessionDetail,
  isSharedSession,
  isSoloSession,
  normalizeBots,
  partitionBotSessions,
  sessionsForBot,
  summarizeUnread,
  tintVarFor,
  verifySharedSession,
} from "./team-data.js";
import {
  forgetDmSession,
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
// mainToken guards rapid bot switching: every openBot mints a token and
// only the resolve still owning it may touch mainResolving/mainError or
// navigate (see resolveMain). Ownership is re-checked after every await,
// and resolveMain hands its `owned` closure into hub.afterTeamSession as a
// guard, so a stale navigation already inside the hub (past resolveMain's
// last checkpoint, mid-refresh) still aborts instead of loading the first
// bot's DM over the second bot's window.
let mainResolving = false;
let mainToken = 0;
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
   Rendering is dot-only by design: the sidebar never fetches per-session
   messages, so summarizeUnread's list-only path always yields count 0 and
   the badge shows a dot (exact counts exist only in the pure helpers for
   callers that already hold messages). Opening a session clears it (app.js
   marks seen in renderDetail). */
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

/* Level-1 aggregate stays a dot for the same reason: per-session counts
   are always 0 list-side, so Σ counts never materialize — any unread
   session dots its bot. */
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
  const seen = sessionUnread(session);
  const unread = unreadBadge(seen);
  if (unread) button.append(unread);
  // Level-1 bot rows announce unread state in their aria-label; mirror the
  // same cue here so level-2 session buttons are equally announced.
  if (seen.unread) {
    button.setAttribute("aria-label",
      `${cleanText(session.title, "Phiên trống")}, có tin chưa đọc`);
  }
  button.addEventListener("click", () => {
    exitPicking();
    const sid = button.dataset.sessionId;
    // Lane plan: opening a solo session confirms it as the remembered DM,
    // so the next openBot lands straight back here without re-resolving.
    // Shared rooms never qualify (isSoloSession is exactly [activeBot]).
    const opened = (hub?.state?.sessions || [])
      .find((item) => String(item?.id) === sid);
    if (opened && isSoloSession(opened, activeBot)) rememberDm(activeBot, sid);
    void hub.loadSession(sid);
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

/* CFE-1: clicking a bot opens its fixed DM/main session directly. Primary
   path is the backend's authoritative window: GET /api/bots/<botId>/dm
   (find-or-create server-side, verified with isDmDetail — never a fake
   main). Solo + localStorage resolution (findMainSession) and POST create
   stay only as the legacy fallback when the endpoint/marker is unavailable
   (pre-CBE-5 backend). Level 2 renders immediately with known sessions
   while the DM resolves; the main window pins first. */
async function openBot(botId) {
  level = 2;
  activeBot = botId;
  sharedOpen = false;
  sharedError = "";
  sharedPicked = null;
  mainError = "";
  mainResolving = true;
  const token = ++mainToken;
  render();
  await resolveMain(botId, token);
}

async function resolveMain(botId, token) {
  // Only the resolve that still owns the token may navigate or clear the
  // resolving flag: rapid bot switching mints a new token per openBot, so
  // a stale resolve bails after EVERY await instead of landing the user
  // in the wrong bot's window or clearing the new bot's spinner. The two
  // afterTeamSession handoffs below carry the same ownership (guard arg)
  // because that hub call awaits internally (see the mainToken note above).
  const owned = () => token === mainToken && activeBot === botId && level === 2;
  try {
    if (!hub) return;
    // Primary: the backend's fixed DM. It adopts a legacy unmarked solo
    // room server-side, so no client-side guessing is needed when it works.
    let detail = null;
    try {
      const dm = await getJson(`/api/bots/${encodeURIComponent(botId)}/dm`);
      if (!owned()) return;
      // Verified dm + bots: a backend answering without the marker (or
      // for the wrong bot) falls through to the legacy path, never opens.
      if (isDmDetail(dm, botId)) detail = dm;
    } catch {
      if (!owned()) return;
      detail = null; // endpoint unavailable → legacy fallback below
    }
    if (detail?.id) {
      rememberDm(botId, detail.id);
      if (!owned()) return;
      mainResolving = false;
      render();
      exitPicking();
      await hub.afterTeamSession(detail, owned);
      if (!owned()) return;
      return;
    }
    // Legacy fallback (pre-CBE-5 backend): remembered/newest solo session
    // from the list, else POST-create one and verify it is exactly that.
    const main = findMainSession(hub.state.sessions, botId, getRememberedDm(botId));
    if (main?.id) {
      rememberDm(botId, main.id);
      if (!owned()) return;
      mainResolving = false;
      render();
      exitPicking();
      await hub.loadSession(String(main.id));
      if (!owned()) return;
      return;
    }
    // No solo session yet: create the fixed DM. POST answers the full
    // detail including bots, so the guard below keeps a backend that
    // ignores {bots} from becoming a fake main window.
    const created = await postJson("/api/sessions", { bots: [botId] });
    if (!owned()) return;
    if (!isMainSessionDetail(created, botId)) {
      mainError = "Backend chưa trả về cửa sổ riêng cho bot này — chưa mở cửa sổ chính.";
      return;
    }
    rememberDm(botId, created.id);
    if (!owned()) return;
    exitPicking();
    await hub.afterTeamSession(created, owned);
    if (!owned()) return;
  } catch (error) {
    if (!owned()) return;
    mainError = messageOf(error, "Chưa mở được cửa sổ chính của bot — backend đội bot chưa sẵn sàng.");
  } finally {
    // A stale resolve must not clear the newer bot's spinner: only the
    // owner clears the flag (and re-renders the error/note if any).
    if (owned()) {
      mainResolving = false;
      render();
    }
  }
}

function openTeam() {
  level = 1;
  activeBot = "";
  sharedOpen = false;
  sharedError = "";
  sharedPicked = null;
  mainToken++; // invalidate any in-flight resolve (owned() also checks level)
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
    // Drop a remembered DM that no longer exists (deleted session): the
    // list is authoritative here, so a missing id is stale, not loading.
    // A blank-but-existing DM pruned from the list re-resolves via GET
    // and is simply re-remembered — never a fake window.
    const rememberedId = getRememberedDm(activeBot);
    if (rememberedId
      && !(hub.state.sessions || []).some((item) => String(item?.id) === rememberedId)) {
      forgetDmSession(rememberedId);
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
