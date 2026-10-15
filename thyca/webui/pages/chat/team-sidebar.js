import { avatarStateFor, botMark, botMarkGroup } from "../../shared/js/bot-avatar.js";
import { setChatBotRoster } from "../../shared/js/chat-bot.js";
import { getJson, postJson } from "../../shared/js/http.js";
import { cleanText, formatSessionTime } from "../../shared/js/format.js";
import { messageOf } from "../../shared/js/status.js";
import {
  TEAM_BOT_ID,
  findMainSession,
  groupSessions,
  isDmDetail,
  isMainSessionDetail,
  isSharedSession,
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
/* Sealed-letter rows: a bot bar is a collapsible header for its session
   list. The current bot starts expanded (same shape as before); user
   toggles are remembered across live-state re-renders. */
const openIds = new Set();
const closedIds = new Set();
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
let newSessionBot = "";
let newSessionError = "";
let newSessionErrorBot = "";

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

/* Live activity beats the static role line, per the design's META map:
   a thinking/running bot reads "đang nghĩ…/đang viết…" in the sidebar. */
const LIVE_LABEL = {
  thinking: "đang nghĩ…", running: "đang viết…",
  done: "vừa xong", error: "lỗi · thử lại",
};

function botSubLabel(bot) {
  const live = LIVE_LABEL[avatarStateFor(bot)];
  if (live) return live;
  return [bot.role, statusLabel(bot.status)].filter(Boolean).join(" · ");
}

/* Expanded = shows its session list below the envelope row. The current
   bot is expanded by default until the user folds it; other bots expand
   only by user click. */
function botExpanded(bot) {
  const me = currentTeamBot();
  const id = String(bot?.id || "");
  if (!id) return false;
  return me && id === me.id ? !closedIds.has(id) : openIds.has(id);
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

/* Avatar slot: the design v1.1 note avatar (SVG port in shared/js/
   bot-avatar.js) in the legacy icon column. Shape comes from the
   bot's Agent Card avatar, colour from its tint, state from the
   backend agent_status. Single participant → one avatar; a shared
   session → the composite team avatar (3 heads + +N). */
function avatarSlot(bot, mini = false, selected = false) {
  const slot = document.createElement("span");
  slot.className = mini ? "team-avatar is-mini" : "team-avatar";
  slot.style.setProperty("--team-tint", tintVarFor(bot?.id, bot?.tint));
  slot.setAttribute("aria-hidden", "true");
  slot.innerHTML = botMark(bot, { selected });
  return slot;
}

function sessionIcon(session, selected = false) {
  const ids = Array.isArray(session?.bots) ? session.bots.map(String) : [];
  const members = ids.map((id) => bots.find((bot) => bot.id === id)).filter(Boolean);
  if (members.length > 1) {
    const team = document.createElement("span");
    team.className = "team-avatar is-group";
    const first = members[0];
    team.style.setProperty("--team-tint", tintVarFor(first?.id, first?.tint));
    team.setAttribute("aria-hidden", "true");
    team.innerHTML = botMarkGroup(members, { selected });
    return team;
  }
  if (members.length === 1) return avatarSlot(members[0], true, selected);
  const icon = document.createElement("span");
  icon.className = "session-icon";
  icon.setAttribute("aria-hidden", "true");
  return icon;
}

/* Agent row: legacy .session-row shape. The avatar slot sits in the icon
   cell (grid column 1, both rows); the name and the "role · status"
   line reuse .session-name and <time> verbatim. Selection is a soft
   wash pill in the bot's tint (chat.css, scoped to the team list). */
function botRow(bot) {
  const row = document.createElement("div");
  row.className = "session-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-item team-bot-item";
  button.dataset.botId = bot.id;
  button.style.setProperty("--team-tint", tintVarFor(bot.id, bot.tint));
  button.title = bot.role ? `${bot.name} \u00b7 ${bot.role}` : bot.name;
  button.classList.toggle("is-paused", String(bot.status || "") === "paused");
  const current = currentTeamBot();
  // An open group session owns the single selected row — the bot row that
  // merely happens to be currentTeamBot must not also read as selected.
  const selected = !!current && current.id === bot.id
    && !isSharedSession(hub?.state?.detail);
  button.classList.toggle("is-active", selected);
  if (selected) button.setAttribute("aria-current", "page");
  const name = document.createElement("span");
  name.className = "session-name";
  name.textContent = bot.name;
  button.append(avatarSlot(bot, false, selected), name);
  const sub = botSubLabel(bot);
  if (sub) {
    const line = document.createElement("time");
    line.textContent = sub;
    button.append(line);
  }
  const seen = botUnread(bot.id);
  const unread = unreadBadge(seen);
  if (unread) button.append(unread);
  const expanded = botExpanded(bot);
  button.classList.toggle("is-open", expanded);
  button.setAttribute("aria-expanded", expanded ? "true" : "false");
  const caret = document.createElement("span");
  caret.className = "team-bot-caret";
  caret.setAttribute("aria-hidden", "true");
  button.append(caret);
  const label = statusLabel(bot.status);
  button.setAttribute("aria-label",
    (bot.role ? `${bot.name}, ${bot.role}` : bot.name)
    + (label ? `, ${label}` : "")
    + unreadSuffix(seen));
  button.addEventListener("click", () => {
    const me = currentTeamBot();
    if (botExpanded(bot)) {
      // Fold only — the open chat (if this bot's) is untouched.
      if (me && bot.id === me.id) closedIds.add(bot.id);
      else openIds.delete(bot.id);
      render();
      return;
    }
    closedIds.delete(bot.id);
    void openBot(bot.id);
  });
  row.append(button);
  return row;
}

/* Unified-list rows: a subordinate child of the bot row above (smaller
   title, muted subline, indented cluster — chat.css), so a session can
   never read as another bot. The subline merges time/turns and, for a
   shared session, the participant names onto one line. Opening works
   like the legacy list: solo rooms confirm the remembered DM, shared
   rooms just open. */
function unifiedRow(session, botId = "", group = false) {
  const row = document.createElement("div");
  row.className = "session-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = group
    ? "session-item team-session-item team-group-item"
    : "session-item team-session-item";
  button.dataset.sessionId = String(session.id || "");
  if (group) {
    const first = bots.find((bot) => bot.id === String(session.bots?.[0] || ""));
    button.style.setProperty("--team-tint", tintVarFor(first?.id, first?.tint));
    button.title = `Nhóm chat: ${cleanText(session.title, "Phiên trống")}`;
  }
  const selected = button.dataset.sessionId === hub.state.activeId;
  button.classList.toggle("is-active", selected);
  if (selected) button.setAttribute("aria-current", "page");
  const name = document.createElement("span");
  name.className = "session-name";
  name.textContent = cleanText(session.title, "Phiên trống");
  const names = roomParticipantNames(session, bots);
  const time = document.createElement("time");
  time.dateTime = String(session.updated_at || "");
  time.textContent = [
    sessionMetaLine(formatSessionTime(session.updated_at), session.turns),
    names.length > 1 ? names.join(" \u00b7 ") : "",
  ].filter(Boolean).join(" \u00b7 ");
  if (group) button.append(sessionIcon(session, selected));
  button.append(name, time);
  const seen = sessionUnread(session);
  const unread = unreadBadge(seen);
  if (unread) button.append(unread);
  if (seen.unread) {
    button.setAttribute("aria-label",
      `${cleanText(session.title, "Phiên trống")}, có tin chưa đọc`);
  }
  button.addEventListener("click", () => {
    mainToken += 1;
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
  title.textContent = "Chọn bot cho nhóm chat";
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
  create.textContent = "Tạo nhóm chat";
  create.addEventListener("click", () => void createShared([...picked]));
  wrap.append(create);
  return wrap;
}

async function createShared(botIds) {
  sharedError = "";
  const chosen = botIds.filter(Boolean);
  if (chosen.length < 2) {
    sharedError = "Nhóm chat cần ít nhất hai bot.";
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
        ? `Backend chưa hỗ trợ nhóm chat (thiếu ${check.missing.join(", ")} trong nhóm mới) — chưa mở nhóm chat.`
        : "Backend chưa hỗ trợ nhóm chat — chưa mở nhóm chat.";
      render();
      return;
    }
    sharedOpen = false;
    sharedPicked = null;
    await hub.afterTeamSession(detail);
  } catch (error) {
    sharedError = messageOf(error, "Chưa tạo được nhóm chat — backend đội bot chưa sẵn sàng.");
    render();
  }
}

async function createBotSession(botId) {
  if (!hub || newSessionBot) return;
  activeBot = botId;
  const token = ++mainToken;
  const owned = () => token === mainToken && activeBot === botId;
  newSessionBot = botId;
  newSessionError = "";
  newSessionErrorBot = "";
  render();
  try {
    const detail = await postJson("/api/sessions", { bots: [botId] });
    if (!owned()) return;
    if (!isMainSessionDetail(detail, botId)
        || !String(detail.id || "") || detail.dm) {
      newSessionError = "Backend chưa trả về phiên mới đúng cho bot này — chưa mở phiên.";
      newSessionErrorBot = botId;
      return;
    }
    exitPicking();
    await hub.afterTeamSession(detail, owned);
    if (!owned()) return;
  } catch (error) {
    if (!owned()) return;
    newSessionError = messageOf(error, "Chưa tạo được phiên mới — backend đội bot chưa sẵn sàng.");
    newSessionErrorBot = botId;
  } finally {
    if (newSessionBot === botId) newSessionBot = "";
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

/* Push a live turn state (thinking/running/done/error) onto a bot's row —
   the chat card is the source of truth; /api/bots only refreshes on load. */
export function teamBotLive(botId, state) {
  const bot = bots.find((item) => item.id === String(botId || ""));
  if (!bot) return;
  if (bot.agentStatus?.state === state) return;
  bot.agentStatus = { ...(bot.agentStatus || {}), state };
  render();
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
  // ONE flat channel list ordered by latest activity (user decision
  // 2026-10-14): bot channels and group chats interleave purely by
  // each channel's own newest activity — type never breaks a tie by
  // putting bots first. A bot entry is its cluster (bot row + the
  // selected bot's solo sessions under it); its activity is only its
  // own DM/solo activity, never a group it merely participates in
  // (that group is a separate channel with its own timestamp).
  const entries = [];
  const botActivity = (botId) => sessionsForBot(hub.state.sessions, botId)
    .filter((session) => !isSharedSession(session))
    .reduce((max, s) => (String(s.updated_at || "") > max ? String(s.updated_at || "") : max), "");
  const errorNote = (text) => {
    const error = document.createElement("p");
    error.className = "screen-status is-error";
    error.setAttribute("role", "alert");
    error.textContent = text;
    return error;
  };
  bots.forEach((bot) => {
    const cluster = document.createElement("div");
    cluster.className = "team-cluster";
    cluster.append(botRow(bot));
    entries.push({
      t: botActivity(bot.id),
      k: String(bot.name || bot.id || ""),
      id: String(bot.id || ""),
      node: cluster,
    });
    if (!botExpanded(bot)) return;
    const sub = document.createElement("div");
    sub.className = "team-cluster-sessions";
    cluster.append(sub);
    if (me && bot.id === me.id && mainError) sub.append(errorNote(mainError));
    const rememberedId = getRememberedDm(bot.id);
    if (rememberedId
      && !(hub.state.sessions || []).some((item) => String(item?.id) === rememberedId)) {
      forgetDmSession(rememberedId);
    }
    // This bot's private (solo) sessions only. Shared sessions are not
    // sub-sessions of any bot — they are group chats and render at top
    // level below (2026-10-14). Thyca's pre-team sessions are simply
    // Thyca's sessions here. The bot's DM is NOT a row here: the bot
    // row above already opens it (/api/bots/<id>/dm), so a DM row
    // duplicated the bot row. unifiedSessionsForBot drops the dm-marked
    // session; findMainSession additionally resolves the pre-marker DM
    // (remembered/newest solo) so that fallback window is dropped too.
    const main = findMainSession(hub.state.sessions, bot.id, rememberedId);
    const sessions = unifiedSessionsForBot(hub.state.sessions, bot.id)
      .filter((session) => !main || String(session.id) !== String(main.id))
      .filter((session) => !isSharedSession(session));
    if (!sessions.length) {
      // A bot with only its DM expands to nothing (the bot row is the DM
      // entry) — no empty text. The note stays only for a bot with no
      // sessions at all.
      if (!sessionsForBot(hub.state.sessions, bot.id).length) {
        const empty = document.createElement("p");
        empty.className = "sidebar-state";
        empty.textContent = "Chưa có phiên.";
        sub.append(empty);
      }
    } else {
      sub.append(...sessions.map((session) => unifiedRow(session, bot.id)));
    }
    if (newSessionError && newSessionErrorBot === bot.id) {
      sub.append(errorNote(newSessionError));
    }
    const create = document.createElement("button");
    create.type = "button";
    create.className = "team-new-session";
    create.setAttribute("aria-label", `Tạo phiên mới với ${bot.name}`);
    create.textContent = newSessionBot === bot.id ? "Đang tạo…" : "+ Phiên mới";
    create.disabled = !!newSessionBot;
    create.addEventListener("click", () => void createBotSession(bot.id));
    sub.append(create);
  });
  groupSessions(hub.state.sessions).forEach((session) => {
    entries.push({
      t: String(session.updated_at || ""),
      k: String(session.title || session.id || ""),
      id: String(session.id || ""),
      node: unifiedRow(session, "", true),
    });
  });
  entries.sort((a, b) => (a.t === b.t
    ? (a.k.localeCompare(b.k) || a.id.localeCompare(b.id))
    : (a.t < b.t ? 1 : -1)));
  list.replaceChildren(...entries.map((entry) => entry.node));
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
  setChatBotRoster(bots);
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
