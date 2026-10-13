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
