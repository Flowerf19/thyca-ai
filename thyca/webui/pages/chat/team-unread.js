/* Team unread + DM remember (team-context Layer 5, CFE-1/CFE-2). localStorage
   only — no DOM, no fetch, so the pure contract stays in team-data.js and
   this file is the thin persistence edge. Every access is try/catch guarded:
   blocked storage must never break the sidebar. */

export const SEEN_KEY = "thyca.team.seen.v1";
export const DM_KEY = "thyca.team.dm.v1";

function readMap(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeMap(key, map) {
  try {
    localStorage.setItem(key, JSON.stringify(map));
  } catch {
    // Blocked storage: the current tab still works, nothing persists.
  }
}

/* Last-seen per session: {ts, count}. ts is the newest message stamp seen
   (detail) or the summary's updated_at (baseline); count is the message
   length at that moment. Shaped to match summarizeUnread's `seen`. */
export function getSeen(sessionId) {
  const entry = readMap(SEEN_KEY)[String(sessionId || "")];
  if (!entry || typeof entry !== "object") return null;
  return {
    ts: typeof entry.ts === "string" ? entry.ts : "",
    count: Number(entry.count) || 0,
  };
}

/* Record what the user has seen. Accepts a session detail (opened session:
   exact max message ts + length) or a summary (silent baseline for sessions
   never opened). Returns the stored entry. */
export function markSeen(sessionId, detailOrSummary) {
  const id = String(sessionId || "");
  if (!id) return null;
  const source = detailOrSummary && typeof detailOrSummary === "object" ? detailOrSummary : {};
  let ts = "";
  let count = 0;
  if (Array.isArray(source.messages)) {
    for (const message of source.messages) {
      const stamp = typeof message?.ts === "string" ? message.ts : "";
      if (stamp && stamp > ts) ts = stamp;
    }
    count = source.messages.length;
  } else {
    ts = typeof source.updated_at === "string" ? source.updated_at : "";
    count = Number(source.message_count) || 0;
  }
  const entry = { ts, count };
  const map = readMap(SEEN_KEY);
  map[id] = entry;
  writeMap(SEEN_KEY, map);
  return entry;
}

/* Remembered main-window session per bot (CFE-1 DM fallback). The id is
   only ever honored when it is still that bot's solo session
   (findMainSession revalidates on every resolve). */
export function getRememberedDm(botId) {
  const id = readMap(DM_KEY)[String(botId || "")];
  return typeof id === "string" ? id : "";
}

export function rememberDm(botId, sessionId) {
  const bot = String(botId || "");
  if (!bot) return;
  const map = readMap(DM_KEY);
  if (sessionId) map[bot] = String(sessionId);
  else delete map[bot];
  writeMap(DM_KEY, map);
}

export function forgetDmSession(sessionId) {
  const id = String(sessionId || "");
  if (!id) return;
  const map = readMap(DM_KEY);
  let changed = false;
  for (const bot of Object.keys(map)) {
    if (map[bot] === id) {
      delete map[bot];
      changed = true;
    }
  }
  if (changed) writeMap(DM_KEY, map);
}
