import { getJson } from "../../shared/js/http.js";
import { cleanText, formatSessionTime } from "../../shared/js/format.js";

// Sidebar session list: pager and rows. Receives the
// entry-owned bindings (state/el plus entry functions) via init — never
// imports app.js back.
let sidebarDeps = null;
let sessionPager = null;

export function initSessionsSidebar(hub) {
  sidebarDeps = hub;
  const { state } = hub;
  sessionPager = buildPager((delta) => {
    state.sessionPage = clampPage(
      state.sessionPage + delta,
      sessionsPageCount(state.sessions.length),
    );
    renderSessions();
  });
  sidebarDeps.el.sessionList.after(sessionPager.nav);
}

// >>> journal-pager (pure math; extracted by unit tests, no DOM here)
const SESSIONS_PAGE_SIZE = 12;
function sessionsPageCount(totalItems, perPage = SESSIONS_PAGE_SIZE) {
  return Math.max(1, Math.ceil(totalItems / perPage));
}
function clampPage(page, pages) {
  return Math.min(Math.max(page, 1), pages);
}
// <<< journal-pager

function buildPager(onStep) {
  const nav = document.createElement("nav");
  nav.className = "session-pager";
  nav.hidden = true;
  const prev = document.createElement("button");
  prev.type = "button";
  prev.className = "screen-button session-pager-step";
  prev.textContent = "‹ Trước";
  prev.setAttribute("aria-label", "Trang trước");
  const label = document.createElement("span");
  label.className = "session-pager-label";
  label.setAttribute("aria-live", "polite");
  const next = document.createElement("button");
  next.type = "button";
  next.className = "screen-button session-pager-step";
  next.textContent = "Sau ›";
  next.setAttribute("aria-label", "Trang sau");
  prev.addEventListener("click", () => onStep(-1));
  next.addEventListener("click", () => onStep(1));
  nav.append(prev, label, next);
  return { nav, prev, next, label };
}

function syncPager(pager, page, pages) {
  pager.nav.hidden = pages <= 1;
  pager.prev.disabled = page <= 1;
  pager.next.disabled = page >= pages;
  pager.label.textContent = `${page} / ${pages}`;
}

// After create/open: flip to the page that holds this session.
export function revealSession(id) {
  if (!id) return;
  const index = sidebarDeps.state.sessions.findIndex((session) => String(session.id) === String(id));
  if (index !== -1) sidebarDeps.state.sessionPage = Math.floor(index / SESSIONS_PAGE_SIZE) + 1;
}

export function sessionKey() {
  return sidebarDeps.state.activeId || "";
}

export function rememberActiveSession(id) {
  try {
    if (id) sessionStorage.setItem("thyca.activeSessionId", id);
    else sessionStorage.removeItem("thyca.activeSessionId");
  } catch {
    // Storage may be blocked; the current tab still works.
  }
}

// Mobile pick pattern: a tapped row reveals its conversation (boot-time and
// retry loads must NOT exit picking — only real taps do).
function exitPickingOnTap() {
  if (matchMedia("(max-width: 56rem)").matches) {
    document.querySelector(".chat-shell")?.classList.remove("is-picking");
  }
}

function sessionButton(session) {
  // The row is a single navigation button.
  const row = document.createElement("div");
  row.className = "session-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-item";
  button.dataset.sessionId = String(session.id || "");
  const selected = button.dataset.sessionId === sidebarDeps.state.activeId;
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
  time.textContent = sessionMeta(session);
  // Same shape as Hồ sơ / Nhật ký: icon and name on the item. The time line
  // is the shared second row of .session-item, flush right under the name.
  button.append(icon, name, time);
  button.addEventListener("click", () => {
    exitPickingOnTap();
    void sidebarDeps.loadSession(button.dataset.sessionId);
  });

  row.append(button);
  return row;
}

// Second line of a row: when it happened, then how many turns it holds. The
// turn count reads the same way Trace counts them (one per user message).
function sessionMeta(session) {
  const when = formatSessionTime(session.updated_at);
  const turns = Number(session.turns);
  if (!Number.isFinite(turns) || turns <= 0) return when;
  return `${when} · ${turns} lượt`;
}

export function renderSessions() {
  const { state, el } = sidebarDeps;
  sessionPager.nav.hidden = !state.sessions.length;
  if (!state.sessions.length) {
    const empty = document.createElement("p");
    empty.className = "sidebar-state";
    empty.textContent = "Chưa có phiên đã lưu.";
    el.sessionList.replaceChildren(empty);
    return;
  }
  const pages = sessionsPageCount(state.sessions.length);
  state.sessionPage = clampPage(state.sessionPage, pages); // clamp after list changes
  const start = (state.sessionPage - 1) * SESSIONS_PAGE_SIZE;
  el.sessionList.replaceChildren(
    ...state.sessions.slice(start, start + SESSIONS_PAGE_SIZE).map(sessionButton),
  );
  // Selection is keyed by session id, so the active row stays marked on any
  // page it lands on.
  syncPager(sessionPager, state.sessionPage, pages);
}

export async function refreshSessions(revealId = "") {
  const payload = await getJson("/api/sessions");
  sidebarDeps.state.sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
  if (revealId) revealSession(revealId);
  renderSessions();
  return payload;
}
