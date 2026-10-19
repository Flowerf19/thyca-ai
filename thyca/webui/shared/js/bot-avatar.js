/* Bot avatar system (design "Bot Avatar System" v1.1, ported).

   Geometry is ported from the approved design file; colours are NOT:
   every fill/stroke resolves through the bot's own --team-tint (its
   Agent Card tint) and the shared theme tokens, so an avatar follows
   the card, not a fixed palette. Inputs are whitelist-only (variant,
   state, numeric size) — bot names/ids never enter the SVG markup, so
   the returned string is safe to inject as innerHTML. */

/* Shared SVG defs (moved verbatim from index.html): wax gradients +
   glyph symbols referenced by url(#…)/use href in every mark. Pages other
   than chat (dashboard, settings) never had them inline. */
const BOT_AVATAR_DEFS = `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<radialGradient id="waxHl" cx="0.34" cy="0.28" r="0.55"><stop offset="0" stop-color="#fff" stop-opacity="0.5"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
<radialGradient id="waxShade" cx="0.62" cy="0.7" r="0.6"><stop offset="0.45" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.28"/></radialGradient>
<linearGradient id="waxSheen" x1="0" x2="1" y1="0" y2="0.3"><stop offset="0.35" stop-color="#fff" stop-opacity="0"/><stop offset="0.5" stop-color="#fff" stop-opacity="0.42"/><stop offset="0.65" stop-color="#fff" stop-opacity="0"/></linearGradient>
<symbol id="g-quill" viewBox="0 0 640 427"><g transform="translate(0.000000,427.000000) scale(0.100000,-0.100000)" fill="currentColor" stroke="none">
  <path d="M4535 3930 c-322 -73 -494 -211 -567 -454 l-18 -61 5 77 c2 43 14
105 25 139 12 34 19 63 17 65 -7 7 -206 -116 -304 -189 -55 -41 -151 -120
-214 -175 -274 -241 -376 -411 -413 -688 l-13 -99 -8 90 c-5 59 -4 123 4 184
6 52 10 96 8 98 -7 7 -139 -140 -203 -226 -76 -102 -134 -211 -163 -303 -22
-72 -41 -225 -42 -342 l-1 -71 -22 115 c-13 63 -27 142 -31 174 l-7 59 -31
-49 c-89 -144 -133 -290 -147 -497 -11 -164 -28 -287 -39 -287 -4 0 -12 19
-19 43 -6 23 -23 56 -37 72 l-26 30 5 -57 c8 -72 -10 -285 -30 -362 -9 -33
-11 -63 -7 -67 5 -5 50 62 103 153 473 817 1034 1478 1746 2058 322 263 318
252 -21 -55 -661 -597 -1122 -1140 -1604 -1888 -67 -103 -136 -213 -153 -244
-30 -54 -31 -56 -13 -69 27 -20 32 -18 86 30 112 100 261 176 438 223 82 21
99 28 84 37 -32 18 -185 20 -244 3 -138 -41 -2 26 183 89 192 66 284 118 403
226 58 52 188 208 181 216 -2 2 -40 -8 -83 -22 -44 -13 -130 -34 -191 -46 -61
-11 -130 -27 -153 -35 -23 -9 -44 -13 -47 -11 -7 8 115 60 259 111 262 93 380
163 529 314 113 115 284 350 268 367 -4 3 -23 -3 -45 -14 -79 -40 -316 -101
-346 -89 -7 3 -4 6 7 6 39 2 232 73 319 118 122 63 227 144 332 256 76 80 211
264 202 274 -2 2 -25 -6 -50 -17 -63 -28 -178 -56 -262 -65 -38 -3 -81 -9 -95
-12 -14 -3 -22 -2 -18 2 4 4 69 23 145 41 183 45 268 86 348 167 59 61 158
227 144 242 -3 3 -26 -6 -50 -20 -45 -26 -158 -65 -188 -65 -9 0 21 18 66 40
105 51 183 123 223 203 32 65 60 176 60 240 l0 37 -102 -1 c-61 0 -136 -8
-183 -19z M2180 1105 c-21 -25 -6 -48 58 -88 47 -30 68 -38 88 -33 20 5 25 12
22 34 -2 20 -17 37 -58 64 -59 40 -90 47 -110 23z M2090 954 c-36 -36 -99 -91
-140 -122 -41 -31 -78 -61 -81 -66 -4 -5 -10 -39 -14 -75 -9 -90 -39 -224 -64
-291 -43 -114 -23 -93 98 102 59 96 91 157 91 175 0 34 16 53 45 53 25 0 45
-18 45 -40 0 -20 -31 -50 -51 -50 -13 0 -35 -33 -178 -253 -24 -37 -41 -71
-37 -74 3 -3 32 20 63 51 49 48 269 206 304 218 6 2 17 44 26 93 8 50 31 132
50 183 19 52 33 95 31 97 -13 11 -107 65 -114 65 -5 0 -39 -30 -74 -66z"/>
</g></symbol>
<symbol id="g-code" viewBox="0 0 24 24"><path d="M8.5 6.5 3.5 12l5 5.5M15.5 6.5l5 5.5-5 5.5M13.2 4.8l-2.4 14.4"/></symbol>
<symbol id="g-nib" viewBox="0 0 24 24"><path d="M12 2.8 6.6 10l1.8 7.4h7.2l1.8-7.4Z"/><path d="M12 2.8v7.1"/><circle cx="12" cy="11.6" r="1.3"/><path d="M8.6 20.6h6.8"/></symbol>
<symbol id="g-note" viewBox="0 0 24 24"><path d="M9 17.6V5.9l10.2-2.2v11.6"/><path d="M9 9.4l10.2-2.2"/><ellipse cx="6.6" cy="17.8" rx="2.5" ry="2.1"/><ellipse cx="16.8" cy="15.6" rx="2.5" ry="2.1"/></symbol>
<symbol id="g-lens" viewBox="0 0 24 24"><circle cx="10.4" cy="10.4" r="6"/><path d="M14.8 14.8 20.4 20.4M7.6 8.8a3.2 3.2 0 0 1 2.4-1.6"/></symbol>
<symbol id="g-brush" viewBox="0 0 24 24"><path d="M14.2 4.6 19.4 9.8 11 18.2 5.8 13Z"/><path d="M5.8 13c-2 1-2.6 3.6-3 6.6 3-.4 5.6-1 7-3.2"/><path d="M16.8 7.2 18.6 5.4"/></symbol>
<symbol id="g-team" viewBox="0 0 24 24"><circle cx="12" cy="7.6" r="3"/><circle cx="7" cy="15.6" r="3"/><circle cx="17" cy="15.6" r="3"/></symbol>
<symbol id="g-pin" viewBox="0 0 24 24"><path d="M12 17v4.5"/><path d="M9 10.7a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2v.8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9a2 2 0 0 1-1.1-1.8V6h1a2 2 0 1 0 0-4H8a2 2 0 1 0 0 4h1z"/></symbol>
</defs></svg>
`;

/* Ensure the shared defs exist once at the top of <body>. Safe to call
   anywhere (including fake-DOM test harnesses): missing document/body
   or crippled DOM APIs are swallowed, never thrown. */
export function ensureBotAvatarDefs(doc = globalThis.document) {
  try {
    if (!doc || typeof doc.querySelector !== "function") return false;
    if (doc.querySelector("#waxShade")) return false;
    const body = doc.body;
    if (!body || typeof body.prepend !== "function"
        || typeof doc.createElement !== "function") return false;
    const holder = doc.createElement("div");
    holder.setAttribute("aria-hidden", "true");
    holder.innerHTML = BOT_AVATAR_DEFS;
    body.prepend(holder);
    return true;
  } catch {
    return false;
  }
}

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
  ensureBotAvatarDefs();
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
  ensureBotAvatarDefs();
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
