/* Bot avatar system (design "Bot Avatar System" v1.1, ported).

   Geometry is ported from the approved design file; colours are NOT:
   every fill/stroke resolves through the bot's own --team-tint (its
   Agent Card tint) and the shared theme tokens, so an avatar follows
   the card, not a fixed palette. Inputs are whitelist-only (variant,
   state, numeric size) — bot names/ids never enter the SVG markup, so
   the returned string is safe to inject as innerHTML. */

export const AVATAR_VARIANTS = [
  "star", "hollow", "single-flag", "double-flag", "dotted", "fermata", "plain",
];
const CHILD_VARIANTS = AVATAR_VARIANTS.slice(1);
export const AVATAR_STATES = [
  "offline", "idle", "thinking", "running", "waiting", "done", "error",
];

const VARIANT_BY_ID = {
  designer: "hollow",
  "thyca-designer": "hollow",
  coder: "single-flag",
  "thyca-coder": "single-flag",
  researcher: "double-flag",
  "thyca-researcher": "double-flag",
  "bot-a": "dotted",
  "thyca-bot-a": "dotted",
  "bot-b": "fermata",
  "thyca-bot-b": "fermata",
  generic: "plain",
  "thyca-generic": "plain",
};

/* CRC32 over the UTF-8 bytes — identical to the backend's
   zlib.crc32 fallback in effective_avatar, so a legacy card renders
   the same shape whichever side computes it. */
function crc32Of(text) {
  let crc = 0xffffffff;
  for (const byte of new TextEncoder().encode(String(text || ""))) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/* The variant a bot renders with: explicit card avatar wins, Thyca is
   always the star, design ids keep their shapes, anything else gets a
   stable per-id child variant (mirrors the backend's effective_avatar
   for cards served by an older backend without the field). */
export function avatarVariantFor(bot) {
  if (!bot || typeof bot !== "object") return "plain";
  if (bot.system || bot.id === "thyca") return "star";
  if (AVATAR_VARIANTS.includes(bot.avatar)) return bot.avatar;
  const known = VARIANT_BY_ID[String(bot.id || "")];
  if (known) return known;
  return CHILD_VARIANTS[crc32Of(bot.id) % CHILD_VARIANTS.length];
}

/* Display state for one bot: the backend agent_status wins; the legacy
   lifecycle status only knows paused/active. */
export function avatarStateFor(bot) {
  if (!bot || typeof bot !== "object") return "idle";
  if (String(bot.status || "") === "paused") return "offline";
  const state = String(bot.agentStatus?.state || bot.agent_status?.state || "");
  if (AVATAR_STATES.includes(state)) return state;
  if (String(bot.status || "") === "running") return "running";
  if (String(bot.status || "") === "error") return "error";
  return "idle";
}

/* Wax-seal bot marks (chosen design, thyca-bot-design.html): a wobbly seal
   blob per bot id + gold note glyph; state/selection drive CSS animation.
   Pure CSS/SVG — no canvas. */

let waxUid = 0;

function waxPath(seed, r = 27, amp = 2.6, n = 13) {
  let s = 0;
  for (const ch of String(seed)) s = (s * 31 + ch.charCodeAt(0)) >>> 0;
  const rand = () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296;
  const pts = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const rr = r + (rand() * 2 - 1) * amp;
    pts.push([32 + Math.cos(a) * rr, 32 + Math.sin(a) * rr]);
  }
  let d = `M${pts[0][0].toFixed(2)} ${pts[0][1].toFixed(2)}`;
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
    d += `C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(2)} ${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(2)} `
      + `${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(2)} ${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(2)} `
      + `${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
  }
  return d + "Z";
}

const WAX_STATE = { thinking: "thinking", running: "writing", done: "done", error: "error" };

/* Per-bot look from the chosen design (thyca-bot-design.html): each seal
   carries its own pigment + glyph. Unknown ids fall back to the variant
   tint and the note glyph. */
const MARK_LOOK = {
  thyca: ["#A65A42", "quill"],
  coder: ["#5C7448", "code"],
  writer: ["#B8863C", "nib"],
  "nhac-viec": ["#6B4A33", "note"],
  research: ["#3E5A5C", "lens"],
};

function markLook(bot) {
  const id = String(bot?.id || "");
  for (const key of Object.keys(MARK_LOOK)) {
    if (id === key || id.endsWith(`-${key}`) || id.startsWith(`${key}-`)) return MARK_LOOK[key];
  }
  return [null, "note"];
}

function glyphSvg(name) {
  return name === "quill"
    ? '<svg class="fill" viewBox="0 0 640 427"><use href="#g-quill"/></svg>'
    : `<svg viewBox="0 0 24 24"><use href="#g-${name}"/></svg>`;
}

export function botMark(bot, { selected = false, state = null, cut = false } = {}) {
  const mkState = state || WAX_STATE[avatarStateFor(bot)] || "idle";
  const [color, glyph] = markLook(bot);
  const tint = color || "var(--avatar-tint, var(--team-tint, var(--color-accent)))";
  const d = waxPath(bot?.id || "thyca");
  const id = `wx${waxUid++}`;
  return `<span class="mk mk-wax" data-state="${mkState}" data-selected="${selected}" `
    + `style="--bot:${tint}" aria-hidden="true">`
    + `<svg class="base" viewBox="0 0 64 64"><clipPath id="${id}"><path d="${d}"/></clipPath>`
    + (cut ? `<path class="cut" d="${d}"/>` : "")
    + `<path class="wax-body" d="${d}"/><path d="${d}" fill="url(#waxShade)"/><path d="${d}" fill="url(#waxHl)"/>`
    + `<g clip-path="url(#${id})"><rect class="wax-sheen" x="0" y="0" width="64" height="64" fill="url(#waxSheen)"/></g>`
    + `<circle class="wax-ring-hi" cx="32" cy="32" r="19.5"/><circle class="wax-ring" cx="32" cy="32" r="19.5"/>`
    + `<circle class="wax-notch" cx="32" cy="32" r="16.4"/>`
    + `<path class="wax-crack" d="M40 7.5l-2.6 6.4 3 3.2-3.4 5.6"/></svg>`
    + `<span class="glyph">${glyphSvg(glyph)}</span></span>`;
}

/* Group mark — "chồng dấu" (stack) from thyca-bot-group-avatar.html:
   overlapping seals, the first/speaking bot on top, +N coin past 3. */
const STACK_POS = {
  1: [[0, 0, 100]],
  2: [[32, 32, 68], [0, 0, 64]],
  3: [[34, 34, 64], [0, 22, 54], [24, 0, 50]],
};
const STACK_OVER = [[34, 34, 64], [0, 22, 54]];

export function botMarkGroup(members, { selected = false, state = null } = {}) {
  const list = (Array.isArray(members) ? members : []).filter(Boolean);
  if (list.length <= 1) return botMark(list[0] || {}, { selected, state });
  const pos = STACK_POS[list.length] || STACK_OVER;
  const shown = list.slice(0, pos.length);
  const marks = shown.map((member, i) => {
    const m = botMark(member, {
      state: i === 0 ? state : "idle",
      cut: true,
    });
    const [l, t, w] = pos[i];
    return `<span class="gm-slot" style="left:${l}%;top:${t}%;width:${w}%;height:${w}%">${m}</span>`;
  }).reverse().join("");
  const extra = list.length - shown.length;
  const coin = extra > 0
    ? `<span class="gm-coin" style="left:50%;top:0;width:42%;height:42%">+${extra}</span>`
    : "";
  return `<span class="gm gm-stack" data-selected="${selected}">${coin}${marks}</span>`;
}
