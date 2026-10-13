import { getJson, postJson } from "../../shared/js/http.js";
import { cleanText, formatSessionTime } from "../../shared/js/format.js";
import { messageOf } from "../../shared/js/status.js";
import {
  TEAM_BOT_ID,
  findMainSession,
  isDmDetail,
  isMainSessionDetail,
  isSoloSession,
  normalizeBots,
  roomParticipantNames,
  sessionMetaLine,
  sessionsForBot,
  summarizeUnread,
  tintVarFor,
  unifiedSessionsForBot,
  verifySharedSession,
} from "./team-data.js";
import {
  forgetDmSession,
  getRememberedDm,
  getSeen,
  markSeen,
  rememberDm,
} from "./team-unread.js";

// Team sidebar: agents are the primary list, reusing the legacy session-row
// anatomy (sessions-sidebar.js + kit.css .session-item): each agent row is a
// .session-row > button.session-item with the tint dot in the icon cell, the
// name in .session-name and "role · status" in the italic <time> line. The
// active bot keeps the legacy left accent bar via .is-active. Clicking an
// agent opens its main (DM) window directly — there is no second level.
// The sidebar is ONE list, the bot list: the selected bot's sessions (its
// private sessions — its DM excluded, since the bot row itself opens that
// DM — and the shared sessions it takes part in, merged as legacy rows)
// render directly under that bot's row inside
// the same list; other bots stay single rows. A multi-bot session adds a
// participant annotation line. The same shared session appears under
// every participating bot. A bot-row click both selects that bot (its
// sessions expand beneath it) and resolves/opens its DM as main window.
//
// Progressive enhancement over sessions-sidebar.js: when GET /api/bots
// answers, this section replaces the legacy session list; when the backend
// is not there yet, the legacy list stays exactly as today plus one muted
// note.
let hub = null;
let bots = [];
let activeBot = "";
let sharedOpen = false;
let sharedError = "";
// Checkbox picks survive re-renders while the picker stays open.
let sharedPicked = null;
// Main-window (DM) resolve state for CFE-1: creation is async, so openBot
// highlights immediately while the fixed DM resolves. mainToken guards
// rapid bot switching: every openBot mints a token and only the resolve
// still owning it may touch mainError or navigate (see resolveMain).
// Ownership is re-checked after every await, and resolveMain hands its
// `owned` closure into hub.afterTeamSession as a guard, so a stale
// navigation already inside the hub (past resolveMain's last checkpoint,
// mid-refresh) still aborts instead of loading the first bot's DM over the
// second bot's window.
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

function botList() {
  return document.querySelector("#team-bot-list");
}

function actionsBox() {
  return document.querySelector("#team-nav-actions");
}

function legacySection() {
  // #team-nav carries the legacy .sessions class for its layout, so the
  // legacy list must exclude it explicitly.
  return document.querySelector("#chat-sidebar .sessions:not(#team-nav)");
}

function showTeam() {
  teamSection()?.removeAttribute("hidden");
  const legacy = legacySection();
  if (legacy) legacy.hidden = true;
}

function statusLabel(status) {
  return STATUS_LABEL[String(status || "")] || "";
}

/* The bot whose unified list is on screen: the open session names it when
   it is a solo room, otherwise the last bot opened this tab, otherwise a
   participant of the open shared room, otherwise Thyca (the default bot
   whose pre-team sessions are simply its sessions in the one list).
   Drives the .is-active accent bar and the unified session list. */
export function currentTeamBot() {
  const detail = hub?.state?.detail;
  const participants = Array.isArray(detail?.bots) ? detail.bots.map(String) : [];
  const id = (participants.length === 1 ? participants[0] : "")
    || activeBot || participants[0] || TEAM_BOT_ID;
  return bots.find((bot) => bot.id === id)
    || bots.find((bot) => bot.id === TEAM_BOT_ID) || null;
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

/* Agent aggregate stays a dot for the same reason: per-session counts
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

function unreadSuffix(state) {
  if (!state?.unread) return "";
  return state.count > 0 ? `, ${state.count} chưa đọc` : ", có tin chưa đọc";
}

/* Agent row: legacy .session-row shape. The tint dot sits in the icon cell
   (grid column 1, both rows — see .team-bot-dot); the name and the
   "role · status" line reuse .session-name and <time> verbatim, so the row
   reads exactly like an old session row. */
function botRow(bot) {
  const row = document.createElement("div");
  row.className = "session-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-item";
  button.dataset.botId = bot.id;
  const current = currentTeamBot();
  const selected = !!current && current.id === bot.id;
  button.classList.toggle("is-active", selected);
  if (selected) button.setAttribute("aria-current", "page");
  const dot = document.createElement("span");
  dot.className = "team-bot-dot";
  dot.style.setProperty("--team-tint", tintVarFor(bot.id, bot.tint));
  dot.setAttribute("aria-hidden", "true");
  const name = document.createElement("span");
  name.className = "session-name";
  name.textContent = bot.name;
  button.append(dot, name);
  const sub = [bot.role, statusLabel(bot.status)].filter(Boolean).join(" · ");
  if (sub) {
    const line = document.createElement("time");
    line.textContent = sub;
    button.append(line);
  }
  const seen = botUnread(bot.id);
  const unread = unreadBadge(seen);
  if (unread) button.append(unread);
  const label = statusLabel(bot.status);
  button.setAttribute("aria-label",
    (bot.role ? `${bot.name}, ${bot.role}` : bot.name)
    + (label ? `, ${label}` : "")
    + unreadSuffix(seen));
  button.addEventListener("click", () => void openBot(bot.id));
  row.append(button);
  return row;
}

/* Unified-list rows: the old session-row format untouched (quill icon,
   bold title, italic "time · lượt" line) for private and shared sessions
   alike — no category label on the row. Opening one works like the legacy
   list: solo rooms confirm the remembered DM, shared rooms just open. A
   session with multiple participating bots adds one annotation line with
   their names; a solo session has no annotation line. */
function unifiedRow(session, botId = "") {
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
  time.textContent = sessionMetaLine(formatSessionTime(session.updated_at), session.turns);
  button.append(icon, name, time);
  const names = roomParticipantNames(session, bots);
  if (names.length > 1) {
    const annotation = document.createElement("span");
    annotation.className = "stack";
    annotation.textContent = names.join(" · ");
    button.append(annotation);
  }
  const seen = sessionUnread(session);
  const unread = unreadBadge(seen);
  if (unread) button.append(unread);
  if (seen.unread) {
    button.setAttribute("aria-label",
      `${cleanText(session.title, "Phiên trống")}, có tin chưa đọc`);
  }
  button.addEventListener("click", () => {
    exitPicking();
    const sid = button.dataset.sessionId;
    const opened = (hub?.state?.sessions || [])
      .find((item) => String(item?.id) === sid);
    if (opened && botId && isSoloSession(opened, botId)) rememberDm(botId, sid);
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

/* Shared-room picker: rendered only while actively picking (the actions box
   carries [hidden] otherwise, so no empty pill ever shows). */
function sharedPicker() {
  const wrap = document.createElement("div");
  wrap.className = "team-shared-picker";
  const title = document.createElement("p");
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
   (pre-CBE-5 backend). The list highlights at once; the DM lands when it
   resolves. */
async function openBot(botId) {
  activeBot = botId;
  sharedOpen = false;
  sharedError = "";
  sharedPicked = null;
  mainError = "";
  const token = ++mainToken;
  render();
  await resolveMain(botId, token);
}

async function resolveMain(botId, token) {
  // Only the resolve that still owns the token may navigate or record the
  // error: rapid bot switching mints a new token per openBot, so a stale
  // resolve bails after EVERY await instead of landing the user in the
  // wrong bot's window. The two afterTeamSession handoffs below carry the
  // same ownership (guard arg) because that hub call awaits internally.
  const owned = () => token === mainToken && activeBot === botId;
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
    // A stale resolve must not touch the newer bot's state: only the owner
    // re-renders the error/note if any.
    if (owned()) render();
  }
}

export function renderTeamSidebar() {
  render();
}

function render() {
  const section = teamSection();
  if (!section || !bots.length) return;
  showTeam();
  const head = section.querySelector("#team-nav-title");
  if (head) head.textContent = "Đội";
  const list = botList();
  if (!list) return;
  // Drop a remembered DM that no longer exists (deleted session): the
  // list is authoritative here, so a missing id is stale, not loading.
  const me = currentTeamBot();
  // ONE list: every bot is a row; the selected bot's sessions render
  // directly under its row, inside this same list.
  const rows = [];
  for (const bot of bots) {
    rows.push(botRow(bot));
    if (!me || bot.id !== me.id) continue;
    const rememberedId = getRememberedDm(me.id);
    if (rememberedId
      && !(hub.state.sessions || []).some((item) => String(item?.id) === rememberedId)) {
      forgetDmSession(rememberedId);
    }
    if (mainError) {
      const error = document.createElement("p");
      error.className = "screen-status is-error";
      error.setAttribute("role", "alert");
      error.textContent = mainError;
      rows.push(error);
    }
    // One merged list: this bot's private sessions together with every
    // shared session it participates in. Thyca's pre-team sessions are
    // simply Thyca's sessions here. The bot's DM is NOT a row here: the
    // bot row above already opens it (/api/bots/<id>/dm), so a DM row
    // duplicated the bot row. unifiedSessionsForBot drops the dm-marked
    // session; findMainSession additionally resolves the pre-marker DM
    // (remembered/newest solo) so that fallback window is dropped too.
    const main = findMainSession(hub.state.sessions, me.id, rememberedId);
    const sessions = unifiedSessionsForBot(hub.state.sessions, me.id)
      .filter((session) => !main || String(session.id) !== String(main.id));
    if (!sessions.length) {
      // A bot with only its DM expands to nothing (the bot row is the DM
      // entry) — no empty text. The note stays only for a bot with no
      // sessions at all.
      if (!sessionsForBot(hub.state.sessions, me.id).length) {
        const empty = document.createElement("p");
        empty.className = "sidebar-state";
        empty.textContent = "Chưa có phiên.";
        rows.push(empty);
      }
    } else {
      rows.push(...sessions.map((session) => unifiedRow(session, me.id)));
    }
  }
  list.replaceChildren(...rows);
  const actions = actionsBox();
  if (actions) {
    actions.replaceChildren();
    actions.hidden = !sharedOpen;
    if (sharedOpen) actions.append(sharedPicker());
  }
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
  document.querySelector("#team-shared-create")?.addEventListener("click", () => {
    sharedOpen = !sharedOpen;
    sharedError = "";
    render();
  });
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
    if (nav && !nav.parentElement?.querySelector("[data-team-note]")) {
      const note = document.createElement("p");
      note.className = "sidebar-state";
      note.dataset.teamNote = "1";
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

// After a team session is created elsewhere (or sessions reload), the lists
// must follow the newest state.
export function syncTeamSessions() {
  if (bots.length && !teamSection()?.hidden) render();
}
