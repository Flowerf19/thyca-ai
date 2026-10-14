/* Team v1 frontend — pure helpers (no DOM, no fetch). DOM wiring lives in
   team-sidebar.js / team-mention.js / team-cards.js / team-drawer.js; data
   fetching stays at the call sites so these stay unit-testable under node.

   Contract (docs/team-plan §6, frozen, plus team-context CBE-5): room
   transcript events carry meta {bot_id, kind, trace_id, parent_span_id};
   GET /api/bots lists public cards {id/name/role/description/tint/status};
   sessions carry bots: string[] plus dm (the bot id whose fixed main
   window this session is, else null); GET /api/bots/<botId>/dm resolves
   or creates that fixed DM. Every helper treats missing fields as
   "unavailable" (pre-CBE-5 payloads carry no dm), never as fake data. */

export const TEAM_BOT_ID = "thyca";

/* Tint tokens the team design allows (team-frontend.md §5). Card `tint` wins
   when it names one of these; otherwise a stable hash fallback. `thyca` is
   always accent. No hex here — tints resolve to var(--color-…) at render. */
export const BOT_TINTS = ["accent", "botanical", "success-ink", "ink", "accent-deep"];

export function tintForBot(botId, tint) {
  if (String(botId || "") === TEAM_BOT_ID) return "accent";
  if (BOT_TINTS.includes(String(tint || ""))) return String(tint);
  const id = String(botId || "");
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return BOT_TINTS[hash % BOT_TINTS.length];
}

export function tintVar(tint) {
  return `var(--color-${tintForBot("", tint)})`;
}

export function tintVarFor(botId, tint) {
  return `var(--color-${tintForBot(botId, tint)})`;
}

/* Normalize GET /api/bots payloads. Tolerant: {bots:[…]} or a bare array;
   anything else (backend missing, error page) → []. Entries keep the public
   card fields plus the optional detail fields the drawer renders
   (abilities/limits/model/bash/skills/schedule) — passed through verbatim
   when the backend supplies them, empty otherwise. The drawer renders an
   honest "chưa có dữ liệu" for empties; nothing here is ever invented. */
function stringList(value) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => String(item ?? "").trim())
    .filter((text) => text.length > 0);
}

function textField(value) {
  if (typeof value === "string") return value;
  if (value == null) return "";
  if (typeof value === "object") {
    // Card model is {id, thinking}: keep the model id as the readable line.
    if (typeof value.id === "string") return value.id;
    return "";
  }
  return String(value);
}

export function normalizeBots(payload) {
  const list = Array.isArray(payload) ? payload : payload?.bots;
  if (!Array.isArray(list)) return [];
  return list
    .filter((bot) => bot && typeof bot === "object" && typeof bot.id === "string" && bot.id)
    .map((bot) => ({
      id: String(bot.id),
      name: typeof bot.name === "string" && bot.name ? bot.name : String(bot.id),
      role: typeof bot.role === "string" ? bot.role : "",
      description: typeof bot.description === "string" ? bot.description : "",
      tint: tintForBot(bot.id, bot.tint),
      avatar: typeof bot.avatar === "string" ? bot.avatar : "",
      system: bot.system === true,
      agentStatus: bot.agent_status && typeof bot.agent_status === "object"
        ? bot.agent_status
        : (bot.agentStatus && typeof bot.agentStatus === "object" ? bot.agentStatus : null),
      status: typeof bot.status === "string" && bot.status ? bot.status : "unknown",
      abilities: stringList(bot.abilities),
      limits: stringList(bot.limits),
      model: textField(bot.model),
      bash: textField(bot.bash),
      skills: stringList(bot.skills),
      schedule: textField(bot.schedule),
    }));
}

/* Sessions of one bot. Contract: session.bots is the participants list;
   sessions without it predate teams → they belong to thyca alone. */
export function sessionsForBot(sessions, botId) {
  const id = String(botId || "");
  return (Array.isArray(sessions) ? sessions : []).filter((session) => {
    if (!session || typeof session !== "object") return false;
    if (!Array.isArray(session.bots)) return id === TEAM_BOT_ID;
    return session.bots.map(String).includes(id);
  });
}

/* True when a session is shared (more than one participant) — such a
   session carries a participant annotation in the unified bot list. */
export function isSharedSession(session) {
  return Array.isArray(session?.bots) && session.bots.length > 1;
}

/* Group chats: shared sessions are first-class channels in their own
   right (not a bot's sub-sessions), newest first. */
export function groupSessions(sessions) {
  return (Array.isArray(sessions) ? sessions : [])
    .filter((session) => isSharedSession(session))
    .slice()
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
}

/* @ mention candidates: id/name substring, case-insensitive; empty query
   returns the full roster in order. */
export function mentionCandidates(bots, query) {
  const needle = String(query || "").trim().toLowerCase();
  const roster = Array.isArray(bots) ? bots : [];
  if (!needle) return roster.slice();
  return roster.filter((bot) => String(bot.id || "").toLowerCase().includes(needle)
    || String(bot.name || "").toLowerCase().includes(needle));
}

/* Split a room transcript into display runs: user turns and thyca-or-named
   bot messages stay as-is; consecutive inter-bot bus events collapse into
   one {type:"exchange"} run rendered behind "Xem các bot đã làm gì".
   Envelope rows of kind message|help_request count as exchange only when
   they carry a trace (recorded bus traffic); a trace-less message row is
   a bot speaking in the room (e.g. a message_user DM post) and stays
   visible. Messages without meta keep the legacy single-thread rendering. */
const EXCHANGE_KINDS = new Set(["delegate", "step", "status"]);
const TRACED_EXCHANGE_KINDS = new Set(["message", "help_request"]);

export function isExchangeMessage(message) {
  const meta = message?.meta || {};
  const kind = String(meta.kind || "");
  if (EXCHANGE_KINDS.has(kind)) return true;
  return TRACED_EXCHANGE_KINDS.has(kind) && !!meta.trace_id;
}

export function splitExchange(messages) {
  const runs = [];
  let open = null;
  for (const message of Array.isArray(messages) ? messages : []) {
    if (message && message.role === "assistant" && isExchangeMessage(message)) {
      if (!open) {
        open = { type: "exchange", messages: [] };
        runs.push(open);
      }
      open.messages.push(message);
      continue;
    }
    open = null;
    runs.push({ type: "message", message });
  }
  return runs;
}

/* Delegation tree over transcript messages carrying trace meta (contract §6:
   bot_id, trace_id, parent_span_id). Roots are messages whose parent span is
   absent/unknown; children attach by parent_span_id matching a seen span.
   Messages carry their span id in meta.span_id when the backend provides it;
   without any trace meta the message stays a root-level note (never dropped).
   Pure — shared by the chat collapse and the Trace delegation view. */
export function buildDelegationTree(messages) {
  const nodes = [];
  const bySpan = new Map();
  const list = Array.isArray(messages) ? messages : [];
  list.forEach((message, index) => {
    const meta = (message && typeof message === "object" ? message.meta : null) || {};
    const node = {
      key: String(index),
      botId: typeof meta.bot_id === "string" && meta.bot_id ? meta.bot_id : "",
      kind: typeof meta.kind === "string" ? meta.kind : "",
      traceId: typeof meta.trace_id === "string" ? meta.trace_id : "",
      spanId: typeof meta.span_id === "string" ? meta.span_id : "",
      parentSpanId: typeof meta.parent_span_id === "string" ? meta.parent_span_id : "",
      message,
      children: [],
    };
    nodes.push(node);
    if (node.spanId) bySpan.set(node.spanId, node);
  });
  const roots = [];
  for (const node of nodes) {
    const parent = node.parentSpanId ? bySpan.get(node.parentSpanId) : null;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  // Same-trace roots cluster (first-seen trace order, stable within a
  // trace); messages without trace meta stay last in arrival order. Without
  // span_id the backend gives no parent links, so every node stays a root
  // and the caller renders the flat honest note — never an invented tree.
  const tracedOrder = [];
  const tracedGroups = new Map();
  const untraced = [];
  for (const node of roots) {
    if (node.traceId) {
      if (!tracedGroups.has(node.traceId)) {
        tracedGroups.set(node.traceId, []);
        tracedOrder.push(node.traceId);
      }
      tracedGroups.get(node.traceId).push(node);
    } else {
      untraced.push(node);
    }
  }
  return [...tracedOrder.flatMap((id) => tracedGroups.get(id)), ...untraced];
}

/* Cost per bot over trace/cost rows carrying bot cost meta. Rows with a
   backend bot_breakdown credit each bot in the turn; rows without bot
   meta land in the "" bucket, which the UI renders as an honest
   "chưa gắn bot" line (recorded-known semantics like overviewMetrics:
   sums are recorded, never claimed complete). */
export function aggregateBotCost(rows) {
  const groups = new Map();
  const add = (botId, requests, rawCost) => {
    let group = groups.get(botId);
    if (!group) {
      group = { botId, turns: 0, requests: 0, costUsd: null, pricedTurns: 0 };
      groups.set(group.botId, group);
    }
    group.turns += 1;
    group.requests += Number(requests) || 0;
    // null/blank = never priced (not $0) — same rule as turnCost().
    const cost = rawCost == null || rawCost === "" ? null : Number(rawCost);
    if (cost != null && Number.isFinite(cost)) {
      group.costUsd = (group.costUsd ?? 0) + cost;
      group.pricedTurns += 1;
    }
  };
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row !== "object") continue;
    // Per-bot breakdown (backend bot_breakdown) wins when present: a turn
    // that delegated credits each bot's own requests/cost, and each bot
    // counts one participating turn. Absent/empty breakdown falls back
    // to the row's single bot_id + turn totals (legacy rows).
    const breakdown = Array.isArray(row.bot_breakdown)
      ? row.bot_breakdown.filter((entry) => entry && typeof entry === "object")
      : [];
    if (breakdown.length) {
      for (const entry of breakdown) {
        add(typeof entry.bot_id === "string" ? entry.bot_id : "", entry.requests, entry.cost_usd);
      }
    } else {
      add(typeof row.bot_id === "string" ? row.bot_id : "", row.requests, row.cost_usd);
    }
  }
  return [...groups.values()].sort((a, b) => (b.costUsd ?? -1) - (a.costUsd ?? -1)
    || a.botId.localeCompare(b.botId, "vi"));
}

/* POST /api/sessions {bots} guard: the backend may answer while ignoring
   the bots field and return a plain session. Only open the result as a
   shared room when the returned participants cover every requested bot;
   otherwise the caller shows the unavailable note instead of a fake
   shared room. Pure — node-testable. */
export function verifySharedSession(detail, botIds) {
  const wanted = [...new Set(
    (Array.isArray(botIds) ? botIds : []).map(String).filter(Boolean),
  )];
  const got = new Set(
    Array.isArray(detail?.bots) ? detail.bots.map(String) : [],
  );
  const missing = wanted.filter((id) => !got.has(id));
  return { ok: wanted.length > 0 && missing.length === 0, missing };
}

/* Bot main window (team-context Layer 5, CFE-1). The backend marks the fixed
   DM per bot (CBE-5: session payloads carry `dm`, resolved authoritatively
   via GET /api/bots/<botId>/dm which also adopts a legacy unmarked solo
   room). The frontend therefore prefers the dm-marked session below and
   only falls back to client-side solo resolution (bots exactly [botId],
   remembered per bot in localStorage via team-unread.js DM_KEY, else the
   newest solo, else created via POST /api/sessions {bots:[botId]} and
   verified with isMainSessionDetail) when the endpoint/marker is
   unavailable (pre-CBE-5 backend). */
export function isSoloSession(session, botId) {
  const id = String(botId || "");
  const bots = session && typeof session === "object" ? session.bots : null;
  return Array.isArray(bots) && bots.length === 1 && String(bots[0]) === id;
}

/* Strict guard for the DM endpoint answer: only a detail whose marker AND
   participants name exactly this bot's fixed window opens as the main
   window (same honesty rule as verifySharedSession for the shared flow). */
export function isDmDetail(detail, botId) {
  const id = String(botId || "");
  if (!id || !detail || typeof detail !== "object") return false;
  if (String(detail.dm || "") !== id) return false;
  return Array.isArray(detail.bots) && detail.bots.length === 1
    && String(detail.bots[0]) === id;
}

export function findMainSession(sessions, botId, rememberedId) {
  const list = Array.isArray(sessions) ? sessions : [];
  const id = String(botId || "");
  const remembered = String(rememberedId || "");
  // The CBE-5 marker wins whenever the backend supplies it: the server
  // keeps exactly one DM per bot, so the first marked hit is the window.
  if (id) {
    const marked = list.filter((session) => session && typeof session === "object"
      && String(session.dm || "") === id);
    if (marked.length) {
      if (remembered) {
        const kept = marked.find((session) => String(session.id) === remembered);
        if (kept) return kept;
      }
      return marked[0];
    }
  }
  // Legacy fallback (pre-CBE-5 payloads without dm): remembered solo
  // session still wins when valid, else the newest solo, else null
  // (the caller then POST-creates one).
  const solo = list.filter((session) => isSoloSession(session, botId));
  if (!solo.length) return null;
  if (remembered) {
    const kept = solo.find((session) => String(session.id) === remembered);
    if (kept) return kept;
  }
  // Newest solo session wins: updated_at is the backend's last-message
  // stamp (wire.updated_at), so string order is chronological order.
  return solo.slice().sort((a, b) => String(b.updated_at || "").localeCompare(
    String(a.updated_at || ""),
  ))[0] || null;
}

/* Split a bot's sessions into the pinned main window + the secondary list.
   Unknown mainId (or a mainId that is no longer this bot's) → main null
   and every session stays in others (the caller shows the resolving note). */
export function partitionBotSessions(sessions, botId, mainId) {
  const mine = sessionsForBot(sessions, botId);
  const wanted = String(mainId || "");
  const main = wanted ? (mine.find((session) => String(session.id) === wanted) || null) : null;
  return { main, others: main ? mine.filter((session) => session !== main) : mine };
}

/* Created-session guard for the DM fallback: only treat a POST /api/sessions
   {bots:[botId]} answer as the bot's main window when the returned
   participants are exactly that solo room (same honesty rule as
   verifySharedSession for the shared flow). */
export function isMainSessionDetail(detail, botId) {
  const id = String(botId || "");
  if (!id || !detail || typeof detail !== "object") return false;
  return Array.isArray(detail.bots) && detail.bots.length === 1 && String(detail.bots[0]) === id;
}

/* Unread attribution (CFE-2). A row counts as bot activity when it carries a
   bot stamp and is user-visible: user rows never carry bot_id, and sidecar
   kinds (naming/compaction) never render (transcript.js skips them). */
const SIDECAR_KINDS = new Set(["naming", "compaction"]);

export function isBotAttributed(message) {
  if (!message || typeof message !== "object") return false;
  if (message.role === "user") return false;
  const meta = message.meta && typeof message.meta === "object" ? message.meta : {};
  if (typeof meta.bot_id !== "string" || !meta.bot_id) return false;
  return !SIDECAR_KINDS.has(String(meta.kind || ""));
}

export function botUnreadCount(messages, sinceTs) {
  const floor = String(sinceTs || "");
  let count = 0;
  for (const message of Array.isArray(messages) ? messages : []) {
    if (!isBotAttributed(message)) continue;
    const ts = typeof message.ts === "string" ? message.ts : "";
    if (ts && (!floor || ts > floor)) count += 1;
  }
  return count;
}

/* Unread summary for one session. seen = {ts, count} from localStorage
   (team-unread.js). No seen → fresh:true: the caller stores a silent
   baseline, never a mass unread. With messages → exact attributed count;
   list-only → staleness dot, and dot-only by design: the sidebar never
   fetches per-session messages, so no count ever reaches the badge (the
   count branch of the badge stays dormant until messages are in hand).
   The list-only signal is conservative on purpose: only a strictly newer
   last-message stamp means something arrived after the baseline. A bare
   message_count drift is NOT a bot-attributed signal — sidecar rows
   (naming/compaction, hidden from the transcript) and compaction rewrites
   can shift the count without any user-visible bot activity — so count
   drift without a newer stamp never dots. */
export function summarizeUnread(seen, summary, messages) {
  if (!seen || typeof seen !== "object") return { unread: false, count: 0, fresh: true };
  if (messages !== undefined) {
    const count = botUnreadCount(messages, seen.ts);
    return { unread: count > 0, count, fresh: false };
  }
  const record = summary && typeof summary === "object" ? summary : {};
  const stale = String(record.updated_at || "") > String(seen.ts || "");
  return { unread: stale, count: 0, fresh: false };
}

/* Memory candidates (GET /api/memory/candidates contract): pending items for
   the "Chờ cho cả đội nhớ" tab. Unknown shape → [] (tab stays hidden). */
export function normalizeCandidates(payload) {
  const list = Array.isArray(payload) ? payload : payload?.candidates;
  if (!Array.isArray(list)) return [];
  return list
    .filter((item) => item && typeof item === "object" && item.id != null)
    .map((item) => ({
      id: String(item.id),
      botId: typeof item.bot_id === "string" ? item.bot_id : "",
      sessionId: typeof item.session_id === "string" ? item.session_id : "",
      title: typeof item.title === "string" ? item.title : "",
      createdAt: typeof item.created_at === "string" ? item.created_at : "",
      text: typeof item.text === "string" ? item.text : (typeof item.summary === "string" ? item.summary : ""),
      status: typeof item.status === "string" ? item.status : "pending",
      decidedBy: typeof item.decided_by === "string" ? item.decided_by : "",
    }));
}

export function pendingCandidates(candidates) {
  return normalizeCandidates(candidates).filter((item) => item.status === "pending");
}

/* Ownership-guarded sequencing for multi-await navigations (rapid-switch
   race fix). Runs each async stage in order, re-checking `guard()` before
   the first stage and after every await; returns false on the first failed
   check (later stages never run) and true when every stage completed. The
   caller aborts silently on false without touching navigation state.
   hub.afterTeamSession (app.js) runs its stages through here — adopt id,
   refreshSessions, loadSession — so a stale bot switch landing mid-refresh
   cannot clobber state.activeId or load the wrong DM. */
export async function runGuardedStages(guard, stages) {
  const owned = typeof guard === "function" ? guard : () => true;
  const steps = Array.isArray(stages) ? stages : [];
  for (const step of steps) {
    if (!owned()) return false;
    await step();
  }
  return true;
}

/* Team sidebar second pass (old-UI anatomy): agents and shared rooms reuse
   the legacy session-row shape, so the row text needs the same second line
   the old sidebar renders — "Hôm qua · 10 lượt" via sessionMetaLine — plus
   a participant annotation for multi-agent rooms and the collapsed-history
   slice. All pure (node-tested); the row DOM stays in team-sidebar.js. */

/* Second line of a room/history row, mirroring sessions-sidebar.js
   sessionMeta: when it happened, then how many turns it holds. The caller
   supplies the already-formatted `when` (formatSessionTime) so this module
   stays dependency-free. */
export function sessionMetaLine(when, turns) {
  const stamp = String(when || "");
  const count = Number(turns);
  if (!Number.isFinite(count) || count <= 0) return stamp;
  return stamp ? `${stamp} · ${count} lượt` : `${count} lượt`;
}

/* Display names of a room's participants in roster order (session.bots
   order), falling back to the raw id for bots the roster does not know.
   Rendered as the annotation third line under a shared-room row title. */
export function roomParticipantNames(session, roster) {
  const ids = Array.isArray(session?.bots) ? session.bots.map(String) : [];
  const known = new Map(
    (Array.isArray(roster) ? roster : [])
      .filter((bot) => bot && typeof bot.id === "string" && bot.id)
      .map((bot) => [bot.id, typeof bot.name === "string" && bot.name ? bot.name : bot.id]),
  );
  return ids.map((id) => known.get(id) || id);
}

/* One bot's unified session list: its private sessions and the shared
   sessions it participates in, merged in a single list, newest first —
   EXCLUDING its fixed DM (session.dm === botId): the bot row itself is
   the DM entry (clicking it opens /api/bots/<id>/dm), so listing the DM
   under the bot duplicated the bot row ("two Thyca entries"). Ownership
   mirrors sessionsForBot: legacy rooms without a bots list predate teams
   and belong to thyca alone, so Thyca's pre-team sessions appear in
   Thyca's list like any other bot's sessions. The same shared session
   therefore appears in every participating bot's list. */
export function unifiedSessionsForBot(sessions, botId) {
  const id = String(botId || "");
  if (!id) return [];
  return sessionsForBot(sessions, id)
    .filter((session) => String(session.dm || "") !== id)
    .slice()
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
}

/* Bot message status line: the backend's bot_state when it says something,
   otherwise the legacy settled default ("đã viết") — the same words the
   old Thyca header shows, so a bot without state reads exactly like one. */
export function teamStatusLabel(meta) {
  const state = typeof meta?.bot_state === "string" ? meta.bot_state.trim() : "";
  return state || "đã viết";
}
