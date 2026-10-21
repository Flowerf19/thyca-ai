import { avatarStateFor, botMark, botMarkGroup, botMarkStateFor } from "../../shared/js/bot-avatar.js";
import { setChatBotRoster } from "../../shared/js/chat-bot.js";
import { getJson, postJson } from "../../shared/js/http.js";
import { createBotEnvelope, setBotEnvelopeOpen } from "../../shared/js/bot-envelope.js";
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
  getPins,
  getRememberedDm,
  getSeen,
  markSeen,
  rememberDm,
  setPin,
} from "./team-unread.js";

// Team sidebar: agents are the primary list, reusing the legacy session-row
// anatomy (sessions-sidebar.js + kit.css .session-item): each agent row is a
// .session-row > button.session-item with the tint dot in the icon cell, the
// name in .session-name and meaningful non-idle status in the italic <time>
// line. Idle rows omit the duplicate id. The active bot keeps the legacy left
// accent bar via .is-active. Clicking an agent opens its main (DM) window
// directly — there is no second level.
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
/* One explicit entry owns the open paper at a time. Null follows the current
   selection until the user opens or folds an entry; an empty string is an
   intentional all-closed state that survives status rerenders. */
let expandedEntry = null;
const nodeByKey = new Map();
/* Session sheet paging: newest PAGE_SIZE rows per page, remembered per
   bot across re-renders. */
const PAGE_SIZE = 5;
const pageByBot = new Map();
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

/* Live activity beats the static role line, per the design's META map:
   a thinking/running bot reads "đang nghĩ…/đang viết…" in the sidebar. */
const LIVE_LABEL = {
  thinking: "đang nghĩ…", running: "đang viết…",
  done: "vừa xong", error: "lỗi · thử lại",
};

function botSubLabel(bot) {
  const status = bot.agentStatus || bot.agent_status || {};
  if (status.waiting_on?.kind === "private") return "Đang chờ thông tin riêng";
  const state = avatarStateFor(bot);
  const live = LIVE_LABEL[state];
  if (live) return live;
  return state === "idle" ? "" : statusLabel(bot.status);
}

function sheetPager(botId, page, pageCount, existing = null) {
  const pager = existing || document.createElement("div");
  pager.className = "team-sheet-pager";
  const prev = pager.children[0] || document.createElement("button");
  prev.type = "button";
  prev.className = "team-sheet-page";
  prev.textContent = "‹ Trước";
  prev.disabled = page <= 0;
  prev.setAttribute("aria-label", "Trang phiên trước");
  if (!prev._pagerBound) {
    prev.addEventListener("click", () => {
      pageByBot.set(botId, Math.max(0, (pageByBot.get(botId) || 0) - 1));
      render();
    });
    prev._pagerBound = true;
  }
  const label = pager.children[1] || document.createElement("span");
  label.className = "team-sheet-page-label";
  label.textContent = `${page + 1} / ${pageCount}`;
  const next = pager.children[2] || document.createElement("button");
  next.type = "button";
  next.className = "team-sheet-page";
  next.textContent = "Sau ›";
  next.disabled = page >= pageCount - 1;
  next.setAttribute("aria-label", "Trang phiên sau");
  if (!next._pagerBound) {
    next.addEventListener("click", () => {
      pageByBot.set(botId, (pageByBot.get(botId) || 0) + 1);
      render();
    });
    next._pagerBound = true;
  }
  reconcileChildren(pager, [prev, label, next]);
  return pager;
}

/* Expanded = this entry owns the shared open-paper slot. Until a user
   toggles an entry, derive the default from the current selected session. */
function defaultExpandedEntry() {
  const detail = hub?.state?.detail;
  if (isSharedSession(detail) && detail?.id) return `group:${String(detail.id)}`;
  const participants = Array.isArray(detail?.bots) ? detail.bots.map(String) : [];
  const id = (participants.length === 1 ? participants[0] : "") || activeBot;
  return id ? `bot:${id}` : "";
}

function entryExpanded(key) {
  return (expandedEntry === null ? defaultExpandedEntry() : expandedEntry) === key;
}

function botExpanded(bot) {
  const id = String(bot?.id || "");
  return !!id && entryExpanded(`bot:${id}`);
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

/* Row action button, same convention as sessions-sidebar (word, not
   glyph): sits in .session-actions on the row wrapper, hover-revealed. */
function rowAction(className, label, text, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", onClick);
  return button;
}

function isPinnedBot(botId) {
  return botId === TEAM_BOT_ID || getPins().has(botId);
}

/* Pinning sorts the letter to the top of the list. Thyca is pinned by
   design — a permanent mark, not a toggle. The control is a paper pin
   glyph, not a word. */
function pinControl(row, bot) {
  const pinned = isPinnedBot(bot.id);
  row.classList.toggle("is-pinned", pinned);
  let actions = [...row.children].find((child) => child.className === "session-actions");
  if (!actions) {
    actions = document.createElement("span");
    actions.className = "session-actions";
    row.append(actions);
  }
  const pinSvg = '<svg viewBox="0 0 24 24" aria-hidden="true">'
    + '<use href="#g-pin"/></svg>';
  if (bot.id === TEAM_BOT_ID) {
    if (!actions.children.length) {
      const mark = document.createElement("span");
      mark.className = "session-action is-pinned-mark";
      mark.innerHTML = pinSvg;
      mark.title = "Đã ghim";
      actions.append(mark);
    }
    return;
  }
  let button = [...actions.children].find((child) => child.classList.contains("is-pin"));
  if (!button) {
    button = rowAction("session-action is-pin", "", "", () => {
      const id = button.dataset.pinId;
      setPin(id, !isPinnedBot(id));
      render();
    });
    button.innerHTML = pinSvg;
    actions.append(button);
  }
  button.dataset.pinId = bot.id;
  button.setAttribute("aria-label", pinned
    ? `Bỏ ghim ${bot.name}` : `Ghim ${bot.name} lên đầu`);
  button.title = pinned ? "Bỏ ghim" : "Ghim lên đầu";
}

/* Avatar slot: the wax-seal mark in the legacy icon column. Colour and
   glyph follow the stable bot id; state comes from agent_status. */
function avatarSlot(bot, mini = false, selected = false) {
  const slot = document.createElement("span");
  slot.className = mini ? "team-avatar is-mini" : "team-avatar";
  slot.style.setProperty("--team-tint", tintVarFor(bot?.id));
  slot.setAttribute("aria-hidden", "true");
  slot.dataset.markKey = String(bot?.id || "");
  slot.innerHTML = botMark(bot, { selected });
  return slot;
}

function patchWaxMark(slot, bot, selected) {
  const mark = slot.querySelector?.(".mk-wax");
  if (!mark) return;
  mark.dataset.state = botMarkStateFor(bot);
  mark.dataset.selected = String(selected);
}

function patchBotAvatar(slot, bot, selected) {
  if (!slot) return avatarSlot(bot, false, selected);
  const key = String(bot?.id || "");
  slot.className = "team-avatar";
  slot.style.setProperty("--team-tint", tintVarFor(bot?.id));
  if (slot.dataset.markKey !== key) {
    slot.innerHTML = botMark(bot, { selected });
    slot.dataset.markKey = key;
  }
  patchWaxMark(slot, bot, selected);
  return slot;
}

function patchGroupAvatar(button, session, selected) {
  const existing = [...button.children].find((child) =>
    child.classList?.contains("team-avatar") || child.classList?.contains("session-icon"));
  const desired = sessionIcon(session, selected);
  if (!desired) return existing || null;
  const ids = (session.bots || []).map(String);
  const members = ids.map((id) => bots.find((bot) => bot.id === id)).filter(Boolean);
  const key = ids.join(",");
  const icon = existing || desired;
  icon.className = desired.className;
  icon.setAttribute("aria-hidden", "true");
  if (members.length) icon.style.setProperty("--team-tint", tintVarFor(members[0]?.id));
  if (icon.dataset.markKey !== key) {
    icon.innerHTML = desired.innerHTML;
    icon.dataset.markKey = key;
  }
  const stack = icon.querySelector?.(".gm-stack");
  if (stack) {
    stack.dataset.selected = String(selected);
    const lead = icon.querySelector?.('.gm-slot[data-seal-slot="0"] .mk-wax');
    if (lead && members[0]) lead.dataset.state = botMarkStateFor(members[0]);
  } else if (members[0]) patchWaxMark(icon, members[0], selected);
  desired.remove();
  return icon;
}

function sessionIcon(session, selected = false) {
  const ids = Array.isArray(session?.bots) ? session.bots.map(String) : [];
  const members = ids.map((id) => bots.find((bot) => bot.id === id)).filter(Boolean);
  if (members.length > 1) {
    const team = document.createElement("span");
    team.className = "team-avatar is-group";
    const first = members[0];
    team.style.setProperty("--team-tint", tintVarFor(first?.id));
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
   cell (grid column 1, both rows); the name and meaningful active status
   reuse .session-name and <time> verbatim. Idle rows omit the duplicate id.
   Selection is a soft
   wash pill in the bot's tint (chat.css, scoped to the team list). */
function botRow(bot, existingRow = null) {
  const row = existingRow || document.createElement("div");
  row.className = "session-row team-envelope-header";
  const button = [...row.children].find((child) => child.classList?.contains("team-bot-item"))
    || document.createElement("button");
  button.type = "button";
  button.className = "session-item team-bot-item";
  button.dataset.botId = bot.id;
  button.style.setProperty("--team-tint", tintVarFor(bot.id));
  button.title = bot.role ? `${bot.name} \u00b7 ${bot.role}` : bot.name;
  button.classList.toggle("is-paused", String(bot.status || "") === "paused");
  const current = currentTeamBot();
  const selected = !!current && current.id === bot.id
    && !isSharedSession(hub?.state?.detail);
  button.classList.toggle("is-active", selected);
  if (selected) button.setAttribute("aria-current", "page");
  else button.removeAttribute("aria-current");
  const avatar = patchBotAvatar([...button.children].find((child) =>
    child.classList?.contains("team-avatar")), bot, selected);
  let name = [...button.children].find((child) => child.classList?.contains("session-name"));
  if (!name) name = document.createElement("span");
  name.className = "session-name";
  if (name.textContent !== bot.name) name.textContent = bot.name;
  const content = [avatar, name];
  const sub = botSubLabel(bot);
  let line = [...button.children].find((child) => child.tagName === "TIME");
  if (sub) {
    if (!line) line = document.createElement("time");
    if (line.textContent !== sub) line.textContent = sub;
    content.push(line);
  } else line?.remove();
  const seen = botUnread(bot.id);
  let unread = [...button.children].find((child) => child.classList?.contains("team-unread"));
  if (seen.unread) {
    if (!unread) unread = unreadBadge(seen);
    else {
      unread.classList.toggle("is-dot", seen.count === 0);
      unread.textContent = seen.count > 0 ? String(seen.count) : "";
      unread.setAttribute("aria-label", seen.count > 0
        ? `${seen.count} chưa đọc` : "Có tin chưa đọc");
    }
    content.push(unread);
  } else unread?.remove();
  const expanded = botExpanded(bot);
  button.classList.toggle("is-open", expanded);
  button.classList.toggle("has-status", !!sub);
  button.setAttribute("aria-expanded", expanded ? "true" : "false");
  let caret = [...button.children].find((child) => child.classList?.contains("team-bot-caret"));
  if (!caret) {
    caret = document.createElement("span");
    caret.className = "team-bot-caret";
    caret.setAttribute("aria-hidden", "true");
  }
  content.push(caret);
  reconcileChildren(button, content);
  const label = statusLabel(bot.status);
  button.setAttribute("aria-label",
    (bot.role ? `${bot.name}, ${bot.role}` : bot.name)
    + (label ? `, ${label}` : "")
    + unreadSuffix(seen));
  if (!button._teamBotBound) button.addEventListener("click", () => {
    const id = button.dataset.botId;
    const key = `bot:${id}`;
    if (entryExpanded(key)) {
      expandedEntry = "";
      render();
      return;
    }
    expandedEntry = key;
    void openBot(id);
  });
  button._teamBotBound = true;
  if (button.parentNode !== row) row.append(button);
  pinControl(row, bot);
  return row;
}

/* Unified-list rows: a subordinate child of the bot row above (smaller
   title, muted subline, indented cluster — chat.css), so a session can
   never read as another bot. The subline merges time/turns and, for a
   shared session, the participant names onto one line. Opening works
   like the legacy list: solo rooms confirm the remembered DM, shared
   rooms just open. */
function unifiedRow(session, botId = "", group = false, existingRow = null) {
  const row = existingRow || document.createElement("div");
  row.className = group ? "session-row team-group-header" : "session-row";
  const button = [...row.children].find((child) => child.classList?.contains("team-session-item"))
    || document.createElement("button");
  button.type = "button";
  button.className = group
    ? "session-item team-session-item team-group-item"
    : "session-item team-session-item";
  button.dataset.sessionId = String(session.id || "");
  if (group) {
    const first = bots.find((bot) => bot.id === String(session.bots?.[0] || ""));
    button.style.setProperty("--team-tint", tintVarFor(first?.id));
    button.title = `Nhóm chat: ${cleanText(session.title, "Phiên trống")}`;
  }
  const selected = button.dataset.sessionId === hub.state.activeId;
  button.classList.toggle("is-active", selected);
  if (selected) button.setAttribute("aria-current", "page");
  else button.removeAttribute("aria-current");
  const content = [];
  if (group) content.push(patchGroupAvatar(button, session, selected));
  let name = [...button.children].find((child) => child.classList?.contains("session-name"));
  if (!name) name = document.createElement("span");
  name.className = "session-name";
  const title = cleanText(session.title, "Phiên trống");
  if (name.textContent !== title) name.textContent = title;
  const names = roomParticipantNames(session, bots);
  let time = [...button.children].find((child) => child.tagName === "TIME");
  if (!time) time = document.createElement("time");
  time.dateTime = String(session.updated_at || "");
  const meta = [
    sessionMetaLine(formatSessionTime(session.updated_at), session.turns),
    names.length > 1 ? names.join(" \u00b7 ") : "",
  ].filter(Boolean).join(" \u00b7 ");
  if (time.textContent !== meta) time.textContent = meta;
  content.push(name, time);
  const seen = sessionUnread(session);
  let unread = [...button.children].find((child) => child.classList?.contains("team-unread"));
  if (seen.unread) {
    if (!unread) unread = unreadBadge(seen);
    else {
      unread.classList.toggle("is-dot", seen.count === 0);
      unread.textContent = seen.count > 0 ? String(seen.count) : "";
    }
    content.push(unread);
    button.setAttribute("aria-label", `${title}, có tin chưa đọc`);
  } else {
    unread?.remove();
    button.removeAttribute("aria-label");
  }
  if (group) {
    const sid = String(session.id || "");
    const expanded = entryExpanded(`group:${sid}`);
    button.classList.toggle("is-open", expanded);
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
    let caret = [...button.children].find((child) => child.classList?.contains("team-bot-caret"));
    if (!caret) {
      caret = document.createElement("span");
      caret.className = "team-bot-caret";
      caret.setAttribute("aria-hidden", "true");
    }
    content.push(caret);
  }
  reconcileChildren(button, content);
  if (!button._teamSessionBound) button.addEventListener("click", () => {
    mainToken += 1;
    exitPicking();
    const sid = button.dataset.sessionId;
    const key = `group:${sid}`;
    if (group && entryExpanded(key)) {
      expandedEntry = "";
      render();
      return;
    }
    if (group) expandedEntry = key;
    const opened = (hub?.state?.sessions || [])
      .find((item) => String(item?.id) === sid);
    if (opened && botId && isSoloSession(opened, botId)) rememberDm(botId, sid);
    void hub.loadSession(sid);
  });
  button._teamSessionBound = true;
  if (button.parentNode !== row) row.append(button);
  if (group) pinControl(row, {
    id: `g:${button.dataset.sessionId}`,
    name: cleanText(session.title, "Phiên trống"),
  });
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
    dot.style.setProperty("--team-tint", tintVarFor(bot.id));
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

/* FE-SESSION-1: no more solo-session creation. The bot row opens the
   bot's fixed DM through the standard openBot resolve (GET
   /api/bots/<id>/dm); groups are created only from the shared flow. */

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
    // Primary: the backend's fixed DM (find-or-create server-side,
    // verified with isDmDetail — never a fake main).
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
    // Legacy fallback (payloads without kind/dm): remembered/newest solo
    // session from the list, else POST-create one and verify it is exactly
    // that. The phase-2B backend answers POST 1-bot with its fixed DM.
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

function reconcileChildren(parent, desired) {
  desired.forEach((node, index) => {
    const current = parent.children[index] || null;
    if (current !== node) parent.insertBefore(node, current);
  });
  while (parent.children.length > desired.length) {
    parent.children[parent.children.length - 1].remove();
  }
}

function makeBotCard(bot) {
  return { key: `bot:${bot.id}`, ...createBotEnvelope(bot, botRow(bot)) };
}

function makeGroupDossier(session) {
  const sid = String(session.id || "");
  const shell = document.createElement("div");
  shell.className = "team-dossier";
  const first = bots.find((bot) => bot.id === String(session.bots?.[0] || ""));
  shell.style.setProperty("--team-tint", tintVarFor(first?.id));
  const header = unifiedRow(session, "", true);
  const viewport = document.createElement("div");
  viewport.className = "team-dossier-viewport";
  const paper = document.createElement("div");
  paper.className = "team-dossier-paper";
  paper.id = `team-group-pocket-${encodeURIComponent(sid)}`;
  viewport.append(paper);
  shell.append(header, viewport);
  return { key: `group:${sid}`, node: shell, header, button: header.children[0], viewport, paper };
}

function updateGroupPaper(card, session) {
  let heading = [...card.paper.children].find((child) =>
    child.classList?.contains("team-dossier-members-heading"));
  if (!heading) heading = document.createElement("h3");
  heading.className = "team-dossier-members-heading";
  if (heading.textContent !== "Các bot tham gia") heading.textContent = "Các bot tham gia";
  let members = [...card.paper.children].find((child) =>
    child.classList?.contains("team-dossier-members"));
  if (!members) members = document.createElement("ul");
  members.className = "team-dossier-members";
  const existing = new Map([...members.children].map((member) =>
    [member.dataset.memberKey, member]));
  const ids = [...new Set(Array.isArray(session.bots)
    ? session.bots.map(String).filter(Boolean) : [])];
  const desired = ids.map((id) => {
    const key = id;
    let member = existing.get(key);
    if (!member) {
      member = document.createElement("li");
      member.className = "team-dossier-member";
      member.dataset.memberId = id;
      member.dataset.memberKey = key;
    }
    const label = bots.find((item) => item.id === id)?.name || id;
    if (member.textContent !== label) member.textContent = label;
    return member;
  });
  reconcileChildren(members, desired);
  reconcileChildren(card.paper, [heading, members]);
}

function render() {
  const section = teamSection();
  if (!section || !bots.length) return;
  showTeam();
  const head = section.querySelector("#team-nav-title");
  if (head) head.textContent = "Đội";
  const list = botList();
  if (!list) return;
  const sessionsNow = hub.state.sessions || [];
  const entries = [];
  const wantedKeys = new Set();
  const botActivity = (botId) => sessionsForBot(sessionsNow, botId)
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
    const key = `bot:${bot.id}`;
    wantedKeys.add(key);
    let card = nodeByKey.get(key);
    if (!card) {
      card = makeBotCard(bot);
      nodeByKey.set(key, card);
    }
    card.header = botRow(bot, card.header);
    card.button = card.header.children[0];
    card.button.setAttribute("aria-controls", card.paper.id);
    card.node.style.setProperty("--team-tint", tintVarFor(bot.id));
    const expanded = botExpanded(bot);
    setBotEnvelopeOpen(card, expanded);
    const rememberedId = getRememberedDm(bot.id);
    if (rememberedId
      && !sessionsNow.some((item) => String(item?.id) === rememberedId)) {
      forgetDmSession(rememberedId);
    }
    const main = findMainSession(sessionsNow, bot.id, rememberedId);
    const description = typeof bot.description === "string"
      ? bot.description.trim() : "";
    const sessions = unifiedSessionsForBot(sessionsNow, bot.id)
      .filter((session) => !main || String(session.id) !== String(main.id))
      .filter((session) => !isSharedSession(session));
    const pageCount = Math.ceil(sessions.length / PAGE_SIZE);
    const page = Math.min(pageByBot.get(bot.id) || 0, Math.max(0, pageCount - 1));
    const pageSessions = sessions.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
    const bodyFingerprint = JSON.stringify([
      description, mainError, hub.state.activeId, page, pageCount,
      pageSessions.map((session) => [
        session.id, session.title, session.updated_at, session.turns,
        roomParticipantNames(session, bots),
      ]),
    ]);
    if (card.bodyFingerprint !== bodyFingerprint) {
      const oldChildren = [...card.paper.children];
      const oldRows = new Map(oldChildren
        .filter((child) => child.classList?.contains("session-row"))
        .map((child) => [child.children[0]?.dataset.sessionId, child]));
      const oldDescription = oldChildren.find((child) =>
        child.classList?.contains("team-bot-description"));
      const oldError = oldChildren.find((child) =>
        child.classList?.contains("screen-status"));
      const oldPager = oldChildren.find((child) =>
        child.classList?.contains("team-sheet-pager"));
      const content = [];
      if (description) {
        const paragraph = oldDescription || document.createElement("p");
        paragraph.className = "team-bot-description";
        if (paragraph.textContent !== description) paragraph.textContent = description;
        content.push(paragraph);
      }
      if (mainError) {
        const error = oldError || errorNote(mainError);
        if (error.textContent !== mainError) error.textContent = mainError;
        content.push(error);
      }
      content.push(...pageSessions.map((session) => unifiedRow(
        session, bot.id, false, oldRows.get(String(session.id)) || null,
      )));
      if (pageCount > 1) content.push(sheetPager(bot.id, page, pageCount, oldPager));
      reconcileChildren(card.paper, content);
      card.bodyFingerprint = bodyFingerprint;
    }
    card.paper.classList.toggle("has-status", !!botSubLabel(bot));
    entries.push({
      t: botActivity(bot.id), k: String(bot.name || bot.id || ""),
      id: String(bot.id || ""), node: card.node, pin: isPinnedBot(bot.id),
    });
  });
  const groups = groupSessions(sessionsNow);
  const liveGroupIds = new Set(groups.map((session) => String(session.id || "")));
  if (expandedEntry?.startsWith("group:")
      && !liveGroupIds.has(expandedEntry.slice(6))) expandedEntry = "";
  groups.forEach((session) => {
    const sid = String(session.id || "");
    const key = `group:${sid}`;
    wantedKeys.add(key);
    let card = nodeByKey.get(key);
    if (!card) {
      card = makeGroupDossier(session);
      nodeByKey.set(key, card);
    }
    card.header = unifiedRow(session, "", true, card.header);
    card.button = card.header.children[0];
    card.button.setAttribute("aria-controls", card.paper.id);
    const first = bots.find((bot) => bot.id === String(session.bots?.[0] || ""));
    card.node.style.setProperty("--team-tint", tintVarFor(first?.id));
    const expanded = entryExpanded(`group:${sid}`);
    card.node.classList.toggle("is-open", expanded);
    card.button.setAttribute("aria-expanded", expanded ? "true" : "false");
    card.viewport.setAttribute("aria-hidden", expanded ? "false" : "true");
    card.viewport.inert = !expanded;
    card.paper.setAttribute("aria-hidden", expanded ? "false" : "true");
    card.paper.inert = !expanded;
    updateGroupPaper(card, session);
    entries.push({
      t: String(session.updated_at || ""),
      k: String(session.title || session.id || ""),
      id: sid, node: card.node, pin: isPinnedBot(`g:${sid}`),
    });
  });
  for (const [key, card] of nodeByKey) {
    if (!wantedKeys.has(key)) {
      card.node.remove();
      nodeByKey.delete(key);
      if (key.startsWith("bot:")) {
        const id = key.slice(4);
        if (expandedEntry === key) expandedEntry = "";
        pageByBot.delete(id);
      }
    }
  }
  entries.sort((a, b) => (Number(b.pin || false) - Number(a.pin || false))
    || ((a.pin && b.pin && a.id !== b.id)
      ? (a.id === TEAM_BOT_ID ? -1 : b.id === TEAM_BOT_ID ? 1 : 0)
      : 0)
    || (a.t === b.t
      ? (a.k.localeCompare(b.k) || a.id.localeCompare(b.id))
      : (a.t < b.t ? 1 : -1)));
  reconcileChildren(list, entries.map((entry) => entry.node));
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
