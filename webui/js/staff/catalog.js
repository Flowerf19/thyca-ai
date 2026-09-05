// Event catalog for the staff score — the ONLY place that knows event types.
//
// Adding a new operational trace = add 4 YAML lines here, a status line in
// staff/status.js, and a TurnEvent allowlist entry in thyca/agent/events.py.
// The mapper (staff/map.js) never switches on event types; unregistered
// events are silence by design — never add an "unknown event = note" fallback.
//
// Schema per entry (flat, string values only):
//   type      — event type, must match TurnEvent.type on the wire
//   slot      — pulse | rest | terminal  (pulse requires density, terminal kind)
//   density   — anchor | cue | outer | full   (pulse only)
//   kind      — completed | failed            (terminal only)
//   errorWhen — optional guard expression; whitelist-compiled, never eval'd
//   when + thenDensity — optional paired override (both required)
//
// Rules that keep the 4/4 grammar intact (see .agents/plans/staff-event-catalog.md):
//   - one operational action = at most one pulse (classification lives in Act)
//   - no time-based slotting, no hashing name/path/token into pitch
//   - durations only 4/8/16, no dotted/beam/tie
//   - no fake terminals — only transport completed/failed
//   - no sensitive fields needed by the catalog (path, content)
//   - high-frequency events (token deltas, log lines) stay unregistered

const CATALOG_YAML = `
- type: turn.accepted
  slot: pulse
  density: anchor
- type: llm.started
  slot: pulse
  density: cue
- type: llm.finished
  slot: pulse
  density: outer
  when: tool_count === 0
  thenDensity: full
- type: llm.retry
  slot: pulse
  density: outer
- type: tool.started
  slot: pulse
  density: cue
- type: tool.finished
  slot: pulse
  density: full
  errorWhen: ok !== true
- type: skill.started
  slot: pulse
  density: outer
- type: skill.finished
  slot: pulse
  density: outer
  errorWhen: ok !== true
- type: session.naming.started
  slot: rest
- type: session.naming.finished
  slot: pulse
  density: anchor
- type: turn.completed
  slot: terminal
  kind: completed
- type: turn.failed
  slot: terminal
  kind: failed
`;

// Guard expressions are hand-compiled, not evaluated: only known shapes pass.
const ERROR_WHEN = new Map([["ok !== true", (event) => event.ok !== true]]);
const WHEN = new Map([["tool_count === 0", (event) => event.tool_count === 0]]);

const SLOTS = new Set(["pulse", "rest", "terminal"]);
const DENSITIES = new Set(["anchor", "cue", "outer", "full"]);
const KINDS = new Set(["completed", "failed"]);
const BASH_TYPES = new Set(["tool.started", "tool.finished"]);

function parseCatalog(text) {
  const entries = [];
  let current = null;
  for (const raw of String(text ?? "").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("- ")) {
      current = {};
      entries.push(current);
      assign(current, line.slice(2));
      continue;
    }
    if (current) assign(current, line);
  }
  return entries.filter(Boolean);
}

function assign(target, pair) {
  const index = pair.indexOf(":");
  if (index <= 0) return;
  const key = pair.slice(0, index).trim();
  const value = pair.slice(index + 1).trim();
  if (key && value && !(key in target)) target[key] = value;
}

function compile(entries) {
  const families = new Map();
  for (const entry of entries) {
    const { type, slot } = entry;
    if (!type || !SLOTS.has(slot)) {
      warn("slot must be pulse|rest|terminal", entry);
      continue;
    }
    if (families.has(type)) {
      warn("duplicate type", entry);
      continue;
    }
    const family = { slot };
    if (slot === "pulse") {
      if (!DENSITIES.has(entry.density)) {
        warn("pulse needs density anchor|cue|outer|full", entry);
        continue;
      }
      family.density = entry.density;
      const guard = ERROR_WHEN.get(entry.errorWhen);
      if (entry.errorWhen && !guard) {
        warn("errorWhen not in whitelist", entry);
        continue;
      }
      if (guard) family.errorWhen = guard;
      const hasWhen = Boolean(entry.when);
      const hasThen = Boolean(entry.thenDensity);
      if (hasWhen !== hasThen) {
        warn("when/thenDensity must be paired", entry);
        continue;
      }
      if (hasWhen) {
        const pred = WHEN.get(entry.when);
        if (!pred || !DENSITIES.has(entry.thenDensity)) {
          warn("when/thenDensity invalid", entry);
          continue;
        }
        family.when = pred;
        family.thenDensity = entry.thenDensity;
      }
    }
    if (slot === "terminal") {
      if (!KINDS.has(entry.kind)) {
        warn("terminal needs kind completed|failed", entry);
        continue;
      }
      family.kind = entry.kind;
    }
    families.set(type, family);
  }
  return families;
}

function warn(reason, entry) {
  console.warn(`staff/catalog: dropped ${JSON.stringify(entry.type ?? entry)}: ${reason}`);
}

const FAMILIES = compile(parseCatalog(CATALOG_YAML));

function cloneFamily(family) {
  const out = { slot: family.slot };
  if (family.density) out.density = family.density;
  if (family.kind) out.kind = family.kind;
  if (family.errorWhen) out.errorWhen = family.errorWhen;
  if (family.when) out.when = family.when;
  if (family.thenDensity) out.thenDensity = family.thenDensity;
  return out;
}

// Single lookup for the mapper: resolved clone, else null (silence).
export function familyFor(event) {
  if (!event || typeof event !== "object" || typeof event.type !== "string") return null;
  const base = FAMILIES.get(event.type);
  if (!base) return null;
  const family = cloneFamily(base);
  if (family.slot === "pulse") {
    if (family.when?.(event) && family.thenDensity) family.density = family.thenDensity;
    if (BASH_TYPES.has(event.type) && event.name === "bash") family.density = "full";
  }
  return family;
}

// Static catalog shape for tests — no predicates, no resolved overrides.
export function catalogEntries() {
  return [...FAMILIES.entries()].map(([type, family]) => {
    const out = { type, slot: family.slot };
    if (family.density) out.density = family.density;
    if (family.kind) out.kind = family.kind;
    return out;
  });
}
