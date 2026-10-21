import { TEAM_BOT_ID, tintVarFor } from "./team-data.js";

// Participant strip (design r9 §4.2): the names of every bot in the room,
// right-aligned, right-to-left with thyca outermost. Each name is plain
// text in that bot's own tint (a borderless text button, so clicking it
// still opens the read-only bot drawer). A working bot renders bolder
// (600) with a light sweep through the text (disabled under
// prefers-reduced-motion); an errored bot renders in danger red and
// still. Type is the r9 spec: 0.8rem var(--font-reading), upright.
// No "Dừng cả nhóm", no floating bar.
let hub = null;
let strip = null;

function participants() {
  const detail = hub?.state?.detail;
  // An explicit empty roster means the room truly has no participants;
  // a missing roster is the legacy single-bot case and defaults to thyca.
  const bots = Array.isArray(detail?.bots)
    ? detail.bots.map(String).filter(Boolean)
    : [TEAM_BOT_ID];
  // Right-to-left with thyca outermost: thyca last in DOM under
  // row-reverse, first otherwise — keep DOM order stable (roster order,
  // thyca first) and let CSS handle the visual order.
  return bots;
}

function runningBots() {
  const detail = hub?.state?.detail;
  if (detail?.running === true) {
    // The room's turn is live; the responding bot is the last @-mentioned
    // participant or the single 1-1 bot. Anything more precise needs the
    // backend's status events — do not guess beyond this.
    const bots = participants();
    if (bots.length === 1) return new Set(bots);
  }
  return new Set();
}

function failedBots() {
  const detail = hub?.state?.detail;
  const failed = detail?.failedBots;
  return new Set(Array.isArray(failed) ? failed.map(String) : []);
}

export function renderTeamStrip() {
  if (!strip) strip = document.querySelector("#team-strip");
  if (!strip) return;
  const names = participants();
  const running = runningBots();
  const failed = failedBots();
  strip.replaceChildren();
  // Always show the room's participants, including a single bot in a
  // DM (supersedes the earlier <2 hide). Hide only when there are
  // truly no participants.
  if (names.length === 0) {
    strip.hidden = true;
    return;
  }
  strip.hidden = false;
  for (const id of names) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "team-strip-name";
    button.dataset.botId = id;
    const bot = hub?.teamRoster?.().find?.((item) => item.id === id);
    button.textContent = bot?.name || id;
    button.style.setProperty("--team-tint", tintVarFor(id));
    // Working state comes from the room turn (detail) or the bot's own
    // roster status ("running" = đang làm, as the sidebar labels it);
    // an errored bot is never animated, so error wins over busy.
    const status = String(bot?.status || "");
    if (failed.has(id) || status === "error") button.classList.add("is-error");
    else if (running.has(id) || status === "running") button.classList.add("is-busy");
    // TURN-CURATE: agent tự quản lý context — chỉ hiển thị % trên card bot
    // (room 1-1) thay vì meter/nudge trong khung chat.
    if (names.length === 1) {
      const ratio = Number(hub?.state?.detail?.context?.ratio);
      if (Number.isFinite(ratio) && ratio > 0) {
        const badge = document.createElement("span");
        badge.className = "team-strip-context";
        badge.textContent = `~${Math.round(ratio * 100)}%`;
        badge.title = `Ngữ cảnh phiên ~${Math.round(ratio * 100)}% — agent tự quản lý`;
        button.append(badge);
      }
    }
    button.setAttribute("aria-label", `Hồ sơ bot ${bot?.name || id}`);
    button.addEventListener("click", () => hub?.openBotDrawer?.(id));
    strip.append(button);
  }
}

export function initTeamStrip(entryHub) {
  hub = entryHub;
}
