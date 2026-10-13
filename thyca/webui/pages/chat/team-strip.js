import { TEAM_BOT_ID, tintVarFor } from "./team-data.js";

// Participant strip (design r9 §4.2): the names of every bot in the room,
// right-aligned, right-to-left with thyca outermost. A running bot renders
// bolder with a light shimmer (disabled under prefers-reduced-motion); an
// errored bot renders in danger red and still. Clicking a name opens the
// read-only bot drawer. No "Dừng cả nhóm", no floating bar.
let hub = null;
let strip = null;

function participants() {
  const detail = hub?.state?.detail;
  const bots = Array.isArray(detail?.bots) && detail.bots.length
    ? detail.bots.map(String)
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
  if (names.length <= 1 && names[0] === TEAM_BOT_ID && !hub?.teamRoster?.().length) {
    // Solo Thyca with no team registry: the strip stays out of the way.
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
    button.style.setProperty("--team-tint", tintVarFor(id, bot?.tint));
    if (running.has(id)) button.classList.add("is-running");
    if (failed.has(id)) button.classList.add("is-error");
    button.setAttribute("aria-label", `Hồ sơ bot ${bot?.name || id}`);
    button.addEventListener("click", () => hub?.openBotDrawer?.(id));
    strip.append(button);
  }
}

export function initTeamStrip(entryHub) {
  hub = entryHub;
}
