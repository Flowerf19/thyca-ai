/* Team v1 frontend — pure helpers (no DOM, no fetch). DOM wiring lives in
   team-sidebar.js / team-mention.js / team-cards.js / team-drawer.js; data
   fetching stays at the call sites so these stay unit-testable under node.

   Contract (docs/team-plan §6, frozen): room transcript events carry
   meta {bot_id, kind, trace_id, parent_span_id}; GET /api/bots lists public
   cards {id/name/role/description/tint/status}; sessions carry bots: string[];
   memory candidates at GET /api/memory/candidates. The backend on main does
   not serve these yet — every helper treats missing fields as "unavailable",
   never as fake data. */

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
   anything else (backend missing, error page) → []. Entries keep only the
   public card fields; status defaults to "unknown" (rendered muted, never
   as a fake running/error badge). */
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
      status: typeof bot.status === "string" && bot.status ? bot.status : "unknown",
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

/* True when a session is shared (more than one participant) — rendered with
   a "chung" label in level 2 of the sidebar. */
export function isSharedSession(session) {
  return Array.isArray(session?.bots) && session.bots.length > 1;
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
   bot messages stay as-is; consecutive inter-bot bus events
   (kind delegate|step|status) collapse into one {type:"exchange"} run
   rendered behind "Xem các bot đã làm gì". Messages without meta keep the
   legacy single-thread rendering. */
const EXCHANGE_KINDS = new Set(["delegate", "step", "status"]);

export function isExchangeMessage(message) {
  return EXCHANGE_KINDS.has(String(message?.meta?.kind || ""));
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
  return roots;
}

/* Cost per bot over trace/cost rows carrying bot cost meta. Rows in the
   current backend have no bot_id — they land in the "" bucket, which the UI
   renders as an honest "chưa gắn bot" line (recorded-known semantics like
   overviewMetrics: sums are recorded, never claimed complete). */
export function aggregateBotCost(rows) {
  const groups = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row !== "object") continue;
    const botId = typeof row.bot_id === "string" ? row.bot_id : "";
    let group = groups.get(botId);
    if (!group) {
      group = { botId, turns: 0, requests: 0, costUsd: null, pricedTurns: 0 };
      groups.set(group.botId, group);
    }
    group.turns += 1;
    group.requests += Number(row.requests) || 0;
    // null/blank = never priced (not $0) — same rule as turnCost().
    const raw = row.cost_usd;
    const cost = raw == null || raw === "" ? null : Number(raw);
    if (cost != null && Number.isFinite(cost)) {
      group.costUsd = (group.costUsd ?? 0) + cost;
      group.pricedTurns += 1;
    }
  }
  return [...groups.values()].sort((a, b) => (b.costUsd ?? -1) - (a.costUsd ?? -1)
    || a.botId.localeCompare(b.botId, "vi"));
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
      text: typeof item.text === "string" ? item.text : (typeof item.summary === "string" ? item.summary : ""),
      status: typeof item.status === "string" ? item.status : "pending",
      decidedBy: typeof item.decided_by === "string" ? item.decided_by : "",
    }));
}

export function pendingCandidates(candidates) {
  return normalizeCandidates(candidates).filter((item) => item.status === "pending");
}
