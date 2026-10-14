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

function hashOf(text) {
  let hash = 0;
  const value = String(text || "");
  for (let i = 0; i < value.length; i++) hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  return hash;
}

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

function gazeDelay(bot) {
  return `${-((hashOf(bot?.id) % 50) / 10).toFixed(1)}s`;
}

function eyesSVG(state, scale = 1) {
  if (state === "done") {
    return `<g transform="scale(${scale})" class="avatar-eye-line">`
      + '<path d="M -5.5,-1.5 Q -3.5,-4.5 -1.5,-1.5" />'
      + '<path d="M 1.5,-1.5 Q 3.5,-4.5 5.5,-1.5" /></g>';
  }
  if (state === "error") {
    return `<g transform="scale(${scale})" class="avatar-eye-line">`
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
    + '<ellipse cx="-3.6" cy="-2.2" rx="1.85" ry="2.4" />'
    + '<ellipse cx="3.6" cy="-2.2" rx="1.85" ry="2.4" /></g></g></g>';
}

function badgeSVG(state) {
  if (state === "done") {
    return '<g class="avatar-badge avatar-badge-done"><circle cx="37.5" cy="37.5" r="6.2" />'
      + '<path d="M 34.8,37.5 L 36.7,39.4 L 40.3,35.6" /></g>';
  }
  if (state === "waiting") {
    return '<g class="avatar-badge avatar-badge-waiting"><circle cx="37.5" cy="37.5" r="6.2" />'
      + '<circle class="avatar-badge-dot" cx="34.8" cy="37.5" r="0.95" />'
      + '<circle class="avatar-badge-dot" cx="37.5" cy="37.5" r="0.95" />'
      + '<circle class="avatar-badge-dot" cx="40.2" cy="37.5" r="0.95" /></g>';
  }
  if (state === "error") {
    return '<g class="avatar-badge avatar-badge-error"><circle cx="37.5" cy="37.5" r="6.2" />'
      + '<line x1="37.5" y1="34.6" x2="37.5" y2="38" />'
      + '<circle class="avatar-badge-dot" cx="37.5" cy="40.4" r="1" /></g>';
  }
  return "";
}

function selectedBgSVG() {
  return '<g class="avatar-selected">'
    + '<path class="avatar-selected-paper" d="M 6,4 H 42 A 4,4 0 0 1 46,8 V 36 L 37,45 H 6 A 4,4 0 0 1 2,41 V 8 A 4,4 0 0 1 6,4 Z" />'
    + '<g class="avatar-selected-lines">'
    + '<line x1="7" y1="14" x2="41" y2="14" /><line x1="7" y1="20" x2="41" y2="20" />'
    + '<line x1="7" y1="26" x2="41" y2="26" /><line x1="7" y1="32" x2="41" y2="32" /></g>'
    + '<path class="avatar-selected-fold" d="M 37,45 V 38 A 2,2 0 0 1 39,36 H 46 Z" /></g>';
}

function stemArtSVG(variant) {
  if (variant === "fermata") {
    return '<path class="avatar-stem" d="M 27.2,30.5 V 18.5" />'
      + '<circle class="avatar-main" cx="27.2" cy="12.5" r="1.9" />'
      + '<path class="avatar-stem avatar-fermata" d="M 19.5,11.2 Q 27.2,2.8 34.9,11.2" />';
  }
  let art = '<path class="avatar-stem" d="M 27.2,30.5 V 10.5" />';
  if (variant === "star") {
    art += '<path class="avatar-star" d="M 33.5,9.5 C 33.5,13.2 35.3,15 39,15 C 35.3,15 33.5,16.8 33.5,20.5 C 33.5,16.8 31.7,15 28,15 C 31.7,15 33.5,13.2 33.5,9.5 Z" />';
  } else if (variant === "single-flag") {
    art += '<path class="avatar-main" d="M 27.2,10.5 C 31,12.5 36.5,16.5 35.5,23.5 C 34.5,19 30.5,16.5 27.2,16 Z" />';
  } else if (variant === "double-flag") {
    art += '<path class="avatar-main" d="M 27.2,10.5 C 31,12 36,15.5 35.2,21 C 34.2,17.5 30.5,15.5 27.2,15 Z" />'
      + '<path class="avatar-main" d="M 27.2,17 C 31,18.5 36,22 35.2,27.5 C 34.2,24 30.5,22 27.2,21.5 Z" />';
  } else if (variant === "dotted") {
    art += '<circle class="avatar-main" cx="34.5" cy="22" r="2.8" />';
  }
  return art;
}

function effectSVG(state) {
  if (state === "running") {
    return '<g class="floating-note"><ellipse class="avatar-main" cx="39" cy="10" rx="2.4" ry="1.7" transform="rotate(-20 39 10)" />'
      + '<path class="avatar-stem" d="M 40.8,9.5 V 3 C 40.8,3 44,4 44.5,6.8" /></g>';
  }
  if (state === "thinking") {
    return '<g class="avatar-think">'
      + '<circle class="think-dot-1" cx="10" cy="20" r="1.6" />'
      + '<circle class="think-dot-2" cx="14" cy="15.5" r="1.8" />'
      + '<circle class="think-dot-3" cx="19" cy="12" r="2.0" /></g>';
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
    ? '<ellipse class="avatar-hollow" cx="0" cy="-0.4" rx="7.5" ry="4.8" />'
    : "";
  return `<svg class="bot-svg bot-single" width="${px}" height="${px}" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">`
    + (selected ? selectedBgSVG() : "")
    + `<g class="bot-node is-${state}" style="--gaze-delay: ${gazeDelay(bot)};">`
    + '<g class="node-bounce-target">'
    + stemArtSVG(variant)
    + '<g transform="translate(18.5, 32.5) rotate(-20)">'
    + '<ellipse class="avatar-main" cx="0" cy="0" rx="10.5" ry="7.6" />'
    + hollow
    + eyesSVG(state)
    + "</g></g></g>"
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

const TEAM_SLOTS = [
  { headX: 10.5, headY: 35.5, stemX: 15.4, stemTop: 16, delay: "0s" },
  { headX: 22.5, headY: 33.5, stemX: 27.4, stemTop: 14, delay: "-1.8s" },
  { headX: 34.5, headY: 31.5, stemX: 39.4, stemTop: 12, delay: "-3.4s" },
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
  const allOffline = list.every((bot) => avatarStateFor(bot) === "offline");
  let stems = "";
  let heads = "";
  let effects = "";
  visible.forEach((bot, index) => {
    const slot = TEAM_SLOTS[index];
    const state = avatarStateFor(bot);
    const tint = /^[a-z][a-z0-9]*(-[a-z0-9]+)*$/.test(String(bot.tint || ""))
      ? String(bot.tint)
      : "accent";
    stems += `<line class="avatar-stem" style="--team-tint: var(--color-${tint});" x1="${slot.stemX}" y1="${slot.headY - 1.5}" x2="${slot.stemX}" y2="${slot.stemTop}" />`;
    heads += `<g class="bot-node is-${state}" style="--gaze-delay: ${slot.delay}; --team-tint: var(--color-${tint});">`
      + '<g class="node-bounce-target">'
      + `<g transform="translate(${slot.headX}, ${slot.headY}) rotate(-20)">`
      + '<ellipse class="avatar-main" cx="0" cy="0" rx="6.2" ry="4.6" />'
      + eyesSVG(state, 0.62)
      + "</g></g></g>";
    if (state === "running") {
      const nx = slot.stemX + 1.8;
      const ny = slot.stemTop - 5.2;
      effects += `<g class="floating-note" style="--team-tint: var(--color-${tint}); animation-delay: ${index * 0.35}s">`
        + `<ellipse class="avatar-main" cx="${nx}" cy="${ny}" rx="2.1" ry="1.45" transform="rotate(-20 ${nx} ${ny})" />`
        + `<path class="avatar-stem" d="M ${nx + 1.5},${ny - 0.4} V ${ny - 5.5} C ${nx + 1.5},${ny - 5.5} ${nx + 4.2},${ny - 4.6} ${nx + 4.5},${ny - 2.2}" /></g>`;
    } else if (state === "thinking") {
      effects += '<g class="avatar-think">'
        + `<circle class="think-dot-1" cx="${slot.stemX - 3}" cy="${slot.stemTop - 3}" r="1.2" />`
        + `<circle class="think-dot-2" cx="${slot.stemX}" cy="${slot.stemTop - 5.2}" r="1.35" />`
        + `<circle class="think-dot-3" cx="${slot.stemX + 3}" cy="${slot.stemTop - 7}" r="1.5" /></g>`;
    }
  });
  const last = TEAM_SLOTS[visible.length - 1];
  const beam = `<path class="avatar-beam${allOffline ? " is-offline" : ""}" d="M 14,16.5 L ${last.stemX + 1.4},${last.stemTop - 2}" />`;
  const tag = extra > 0
    ? `<g class="avatar-extra"><rect x="2" y="4" width="15.5" height="11.5" rx="3.8" /><text x="9.8" y="12.3" text-anchor="middle">+${extra}</text></g>`
    : "";
  return `<svg class="bot-svg bot-team" width="${px}" height="${px}" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">`
    + (selected ? selectedBgSVG() : "")
    + `<g class="bot-core">${stems}${beam}${heads}${tag}</g>`
    + effects
    + badgeSVG(teamBadgeState(list))
    + "</svg>";
}
