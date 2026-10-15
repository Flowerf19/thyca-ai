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

const GAZE_DELAY_BY_VARIANT = {
  star: "0s",
  hollow: "-1.3s",
  "single-flag": "-2.7s",
  "double-flag": "-4.1s",
  dotted: "-0.8s",
  fermata: "-3.3s",
  plain: "-2.0s",
};

function gazeDelay(bot) {
  return GAZE_DELAY_BY_VARIANT[avatarVariantFor(bot)] || "0s";
}

function eyesSVG(state, scale = 1) {
  if (state === "done") {
    return `<g transform="scale(${scale})" class="avatar-eye-line">`
      + '<path d="M -5.5,-1.5 Q -3.5,-4.5 -1.5,-1.5" />'
      + '<path d="M 1.5,-1.5 Q 3.5,-4.5 5.5,-1.5" /></g>';
  }
  if (state === "error") {
    return `<g transform="scale(${scale})" class="avatar-eye-line avatar-eye-error">`
      + '<line x1="-5.2" y1="-3.8" x2="-1.8" y2="-0.4" />'
      + '<line x1="-1.8" y1="-3.8" x2="-5.2" y2="-0.4" />'
      + '<line x1="1.8" y1="-3.8" x2="5.2" y2="-0.4" />'
      + '<line x1="5.2" y1="-3.8" x2="1.8" y2="-0.4" /></g>';
  }
  if (state === "offline") {
    return `<g transform="scale(${scale})" class="avatar-eye-line">`
      + '<line x1="-5.5" y1="-1.8" x2="-1.5" y2="-1.8" />'
      + '<line x1="1.5" y1="-1.8" x2="5.5" y2="-1.8" /></g>';
  }
  return `<g transform="scale(${scale})"><g class="eye-gaze"><g class="eyes-open avatar-eye-fill">`
    + '<ellipse cx="-3.6" cy="-2.2" rx="1.8" ry="2.35" />'
    + '<ellipse cx="3.6" cy="-2.2" rx="1.8" ry="2.35" /></g></g></g>';
}

function badgeSVG(state) {
  if (state === "done") {
    return '<g class="avatar-badge avatar-badge-done"><circle cx="37.5" cy="37.5" r="5.8" />'
      + '<path d="M 35,37.5 L 36.8,39.2 L 40.2,35.8" /></g>';
  }
  if (state === "waiting") {
    return '<g class="avatar-badge avatar-badge-waiting"><circle cx="37.5" cy="37.5" r="5.8" />'
      + '<circle class="avatar-badge-dot" cx="35" cy="37.5" r="0.9" />'
      + '<circle class="avatar-badge-dot" cx="37.5" cy="37.5" r="0.9" />'
      + '<circle class="avatar-badge-dot" cx="40" cy="37.5" r="0.9" /></g>';
  }
  if (state === "error") {
    return '<g class="avatar-badge avatar-badge-error"><circle cx="37.5" cy="37.5" r="5.8" />'
      + '<line x1="37.5" y1="34.8" x2="37.5" y2="38" />'
      + '<circle class="avatar-badge-dot" cx="37.5" cy="40.3" r="0.95" /></g>';
  }
  return "";
}

function selectedBgSVG() {
  return '<g class="avatar-selected">'
    + '<ellipse class="avatar-halo" cx="22" cy="27" rx="19" ry="17" transform="rotate(-12 22 27)" />'
    + '<circle class="avatar-halo-ring" cx="22" cy="27" r="18.5" />'
    + '<path class="avatar-halo-star" d="M 7,10 C 7,12 8,13 10,13 C 8,13 7,14 7,16 C 7,14 6,13 4,13 C 6,13 7,12 7,10 Z" /></g>';
}

function stemArtSVG(variant) {
  if (variant === "fermata") {
    return '<path class="avatar-stem" d="M 27.2,30.5 V 18.5" />'
      + '<circle class="avatar-main" cx="27.2" cy="12.5" r="1.85" />'
      + '<path class="avatar-stem avatar-fermata" d="M 19.5,11.2 Q 27.2,2.8 34.9,11.2" />';
  }
  let art = '<path class="avatar-stem" d="M 27.2,30.5 V 10.5" />';
  if (variant === "star") {
    art += '<path class="avatar-star" d="M 33.5,9.5 C 33.5,13.2 35.3,15 39,15 C 35.3,15 33.5,16.8 33.5,20.5 C 33.5,16.8 31.7,15 28,15 C 31.7,15 33.5,13.2 33.5,9.5 Z" />';
  } else if (variant === "single-flag") {
    art += '<path class="avatar-flag" d="M 27.2,10.5 C 31,12.5 36.5,16.5 35.5,23.5 C 34.5,19 30.5,16.5 27.2,16 Z" />';
  } else if (variant === "double-flag") {
    art += '<path class="avatar-flag avatar-flag-double" d="M 27.2,10.5 C 31,12 36,15.5 35.2,21 C 34.2,17.5 30.5,15.5 27.2,15 Z" />'
      + '<path class="avatar-flag avatar-flag-double" d="M 27.2,17 C 31,18.5 36,22 35.2,27.5 C 34.2,24 30.5,22 27.2,21.5 Z" />';
  } else if (variant === "dotted") {
    art += '<circle class="avatar-main" cx="34.5" cy="22" r="2.6" />';
  }
  return art;
}

function effectSVG(state) {
  if (state === "running") {
    return '<g class="floating-note"><ellipse class="avatar-main" cx="39" cy="10" rx="2.3" ry="1.6" transform="rotate(-20 39 10)" />'
      + '<path class="floating-note-stem" d="M 40.7,9.5 V 3.2 C 40.7,3.2 43.8,4.2 44.2,6.8" /></g>';
  }
  if (state === "thinking") {
    return '<g class="avatar-think">'
      + '<circle class="think-dot-1" cx="10" cy="20" r="1.55" />'
      + '<circle class="think-dot-2" cx="14" cy="15.5" r="1.75" />'
      + '<circle class="think-dot-3" cx="19" cy="12" r="1.95" /></g>';
  }
  return "";
}

function sizeAttr(size) {
  const value = Number(size);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : 48;
}

/* One bot's avatar as an SVG string (see the module header for why the
   string form is safe). The parent element owns --team-tint. */
export function botAvatarSVG(bot, { size = 48, selected = false } = {}) {
  const variant = avatarVariantFor(bot);
  const state = avatarStateFor(bot);
  const px = sizeAttr(size);
  const hollow = variant === "hollow"
    ? '<ellipse class="avatar-hollow" cx="0" cy="-0.3" rx="7.2" ry="4.6" />'
    : "";
  return `<svg class="bot-svg bot-single avatar-variant-${variant}" width="${px}" height="${px}" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">`
    + `<g class="bot-node is-${state} bot-ink-layer" style="--gaze-delay: ${gazeDelay(bot)};">`
    + '<g transform="translate(24, 24) scale(1.25) translate(-24, -24)">'
    + '<g class="node-bounce-target">'
    + stemArtSVG(variant)
    + '<g transform="translate(18.5, 32.5) rotate(-20)">'
    + '<ellipse class="avatar-main" cx="0" cy="0" rx="10.4" ry="7.5" />'
    + '<path class="avatar-crescent" d="M -9.2,2.2 A 10.4,7.5 0 0 0 9.6,1.5 A 10.2,6.2 0 0 1 -9.2,2.2 Z" />'
    + hollow
    + eyesSVG(state)
    + "</g></g></g></g>"
    + effectSVG(state)
    + badgeSVG(state)
    + "</svg>";
}

/* Aggregate badge for a team: error beats waiting beats done, and done
   only when every non-offline member is done. */
export function teamBadgeState(members) {
  const states = (Array.isArray(members) ? members : []).map(avatarStateFor);
  if (states.includes("error")) return "error";
  const busy = states.some((state) => state === "running" || state === "thinking");
  if (!busy && states.includes("waiting")) return "waiting";
  const active = states.filter((state) => state !== "offline");
  if (active.length && active.every((state) => state === "done")) return "done";
  return "";
}

function tenth(value) {
  return Math.round(value * 10) / 10;
}

const TEAM_SLOTS = [
  { headX: 11.5, headY: 36.5, stemX: 16.4, stemTop: 17.5, delay: "0s" },
  { headX: 23.5, headY: 34.5, stemX: 28.4, stemTop: 15.5, delay: "-1.8s" },
  { headX: 35.5, headY: 32.5, stemX: 40.4, stemTop: 13.5, delay: "-3.4s" },
];

const TEAM_PAIR_SLOTS = [
  { headX: 14, headY: 36, stemX: 19.8, stemTop: 16, delay: "0s" },
  { headX: 30, headY: 34, stemX: 35.8, stemTop: 13, delay: "-1.8s" },
];

/* A group of bots as one composite avatar: up to three note heads on a
   shared beam, the rest folded into a +N tag. One member renders as a
   plain single avatar. Member tints ride each node's own --team-tint,
   taken from the member's normalized tint token. */
export function teamAvatarSVG(members, { size = 48, selected = false } = {}) {
  const list = (Array.isArray(members) ? members : []).filter(
    (bot) => bot && typeof bot === "object",
  );
  if (!list.length) return botAvatarSVG(null, { size, selected });
  if (list.length === 1) return botAvatarSVG(list[0], { size, selected });
  const px = sizeAttr(size);
  const visible = list.slice(0, 3);
  const extra = list.length - visible.length;
  const pair = visible.length === 2;
  const slots = pair ? TEAM_PAIR_SLOTS : TEAM_SLOTS;
  const headRx = pair ? 7.2 : 6;
  const headRy = pair ? 5.3 : 4.5;
  const eyeScale = pair ? 0.72 : 0.60;
  const crescent = pair
    ? '<g transform="scale(1.2)"><path class="avatar-crescent" d="M -5.2,1.5 A 6.0,4.5 0 0 0 5.5,1.0 A 6.0,3.8 0 0 1 -5.2,1.5 Z" /></g>'
    : '<path class="avatar-crescent" d="M -5.2,1.5 A 6.0,4.5 0 0 0 5.5,1.0 A 6.0,3.8 0 0 1 -5.2,1.5 Z" />';
  const allOffline = list.every((bot) => avatarStateFor(bot) === "offline");
  let stems = "";
  let heads = "";
  let effects = "";
  visible.forEach((bot, index) => {
    const slot = slots[index];
    const state = avatarStateFor(bot);
    const variant = avatarVariantFor(bot);
    const tint = /^[a-z][a-z0-9]*(-[a-z0-9]+)*$/.test(String(bot.tint || ""))
      ? String(bot.tint)
      : "accent";
    stems += `<line class="avatar-team-stem avatar-variant-${variant}${state === "offline" ? " is-offline" : ""}" style="--team-tint: var(--color-${tint});" x1="${slot.stemX}" y1="${slot.headY - 1.5}" x2="${slot.stemX}" y2="${slot.stemTop}" />`;
    heads += `<g class="bot-node is-${state} avatar-variant-${variant}" style="--gaze-delay: ${slot.delay}; --team-tint: var(--color-${tint});">`
      + '<g class="node-bounce-target">'
      + `<g transform="translate(${slot.headX}, ${slot.headY}) rotate(-20)">`
      + `<ellipse class="avatar-main" cx="0" cy="0" rx="${headRx}" ry="${headRy}" />`
      + crescent
      + eyesSVG(state, eyeScale)
      + "</g></g></g>";
    if (state === "running") {
      const nx = index === 0 && extra > 0 ? 22.5 : tenth(slot.stemX + 1.5);
      const ny = index === 0 && extra > 0 ? 9 : tenth(slot.stemTop - 5.2);
      effects += `<g class="floating-note avatar-variant-${variant}" style="--team-tint: var(--color-${tint}); animation-delay: ${index * 0.35}s">`
        + `<ellipse class="avatar-main" cx="${nx}" cy="${ny}" rx="2" ry="1.4" transform="rotate(-20 ${nx} ${ny})" />`
        + `<path class="team-floating-note-stem" d="M ${tenth(nx + 1.4)},${tenth(ny - 0.4)} V ${tenth(ny - 5.2)} C ${tenth(nx + 1.4)},${tenth(ny - 5.2)} ${tenth(nx + 4.0)},${tenth(ny - 4.3)} ${tenth(nx + 4.2)},${tenth(ny - 2.0)}" /></g>`;
    } else if (state === "thinking") {
      const dots = index === 0 && extra > 0
        ? [[19.5, 9], [22.5, 6.8], [25.5, 4.6]]
        : [[slot.stemX - 3, slot.stemTop - 3], [slot.stemX, slot.stemTop - 5.2], [slot.stemX + 3, slot.stemTop - 7]];
      effects += '<g class="avatar-think">'
        + `<circle class="think-dot-1" cx="${tenth(dots[0][0])}" cy="${tenth(dots[0][1])}" r="1.2" />`
        + `<circle class="think-dot-2" cx="${tenth(dots[1][0])}" cy="${tenth(dots[1][1])}" r="1.35" />`
        + `<circle class="think-dot-3" cx="${tenth(dots[2][0])}" cy="${tenth(dots[2][1])}" r="1.5" /></g>`;
    }
  });
  const first = slots[0];
  const last = slots[visible.length - 1];
  const beamD = pair
    ? `M ${tenth(first.stemX - 1.4)},${tenth(first.stemTop + 0.3)} L ${tenth(last.stemX + 1.2)},${tenth(last.stemTop - 0.2)}`
    : `M 15,17.8 L ${tenth(last.stemX + 1.2)},${tenth(last.stemTop - 0.2)}`;
  const beam = `<path class="avatar-beam${allOffline ? " is-offline" : ""}" d="${beamD}" />`;
  const extras = list.slice(visible.length);
  const allExtraOffline = extras.every((bot) => avatarStateFor(bot) === "offline");
  const anyExtraRunning = extras.some((bot) => avatarStateFor(bot) === "running");
  const extraState = allExtraOffline ? "is-offline" : anyExtraRunning ? "is-running" : "";
  const tag = extra > 0
    ? `<g class="avatar-extra${extraState ? ` ${extraState}` : ""}"><circle cx="8.5" cy="9.5" r="6.2" /><text x="8.5" y="12.2" text-anchor="middle">+${extra}</text></g>`
    : "";
  return `<svg class="bot-svg bot-team ${pair ? "bot-team-pair" : "bot-team-trio"}" width="${px}" height="${px}" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">`
    + `<g class="bot-core bot-ink-layer">${stems}${beam}${heads}</g>`
    + tag
    + effects
    + badgeSVG(teamBadgeState(list))
    + "</svg>";
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

export function botMark(bot, { selected = false } = {}) {
  const state = WAX_STATE[avatarStateFor(bot)] || "idle";
  const d = waxPath(bot?.id || "thyca");
  const id = `wx${waxUid++}`;
  return `<span class="mk mk-wax" data-state="${state}" data-selected="${selected}" `
    + `style="--bot:var(--avatar-tint, var(--team-tint, var(--color-accent)))" aria-hidden="true">`
    + `<svg class="base" viewBox="0 0 64 64"><clipPath id="${id}"><path d="${d}"/></clipPath>`
    + `<path class="wax-body" d="${d}"/><path d="${d}" fill="url(#waxShade)"/><path d="${d}" fill="url(#waxHl)"/>`
    + `<g clip-path="url(#${id})"><rect class="wax-sheen" x="0" y="0" width="64" height="64" fill="url(#waxSheen)"/></g>`
    + `<circle class="wax-ring-hi" cx="32" cy="32" r="19.5"/><circle class="wax-ring" cx="32" cy="32" r="19.5"/>`
    + `<circle class="wax-notch" cx="32" cy="32" r="16.4"/>`
    + `<path class="wax-crack" d="M40 7.5l-2.6 6.4 3 3.2-3.4 5.6"/></svg>`
    + `<span class="glyph"><svg viewBox="0 0 24 24"><use href="#g-note"/></svg></span></span>`;
}

export function botMarkForTeam(members, { selected = false } = {}) {
  const list = Array.isArray(members) ? members : [];
  return botMark(list[0] || {}, { selected });
}
