// Event catalog for the staff score — the ONLY place that knows event types.
//
// Adding a new operational trace = add 4 YAML lines here, a status line in
// turn-status.js, and a TurnEvent allowlist entry in thyca/agent/events.py.
// The mapper (staff-map.js) never switches on event types; unregistered
// events are silence by design — never add an "unknown event = note" fallback.
//
// Schema per entry (flat, string values only):
//   type      — event type, must match TurnEvent.type on the wire
//   slot      — pulse | rest | terminal  (pulse requires density, terminal kind)
//   density   — anchor | cue | outer | full   (pulse only)
//   kind      — completed | failed            (terminal only)
//   errorWhen — optional guard expression; whitelist-compiled, never eval'd
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
  density: anchor
- type: llm.finished
  slot: pulse
  density: outer
- type: llm.retry
  slot: rest
- type: tool.started
  slot: pulse
  density: cue
- type: tool.finished
  slot: pulse
  density: full
  errorWhen: ok !== true
- type: skill.started
  slot: pulse
  density: cue
- type: skill.finished
  slot: pulse
  density: full
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
// A value outside the whitelist drops the entry (with a console warning) so a
// YAML typo can never turn a string into executable code.
const ERROR_WHEN = new Map([["ok !== true", (event) => event.ok !== true]]);

const SLOTS = new Set(["pulse", "rest", "terminal"]);
const DENSITIES = new Set(["anchor", "cue", "outer", "full"]);
const KINDS = new Set(["completed", "failed"]);

// Minimal YAML subset parser: a flat list of flat maps with string values.
// Not general YAML — deliberately tiny so the catalog stays dependency-free.
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
  console.warn(`staff-catalog: dropped ${JSON.stringify(entry.type ?? entry)}: ${reason}`);
}

const FAMILIES = compile(parseCatalog(CATALOG_YAML));

// Single lookup for the mapper: object with a string type, else null (silence).
export function familyFor(event) {
  if (!event || typeof event !== "object" || typeof event.type !== "string") return null;
  return FAMILIES.get(event.type) ?? null;
}

// Exposed for tests: assert the catalog shape without reaching into YAML text.
export function catalogEntries() {
  return [...FAMILIES.entries()].map(([type, family]) => ({ type, ...family }));
}
