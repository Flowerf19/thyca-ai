import { botLookFor } from "./bot-avatar.js";

let roster = [];

export function setChatBotRoster(bots) {
  roster = (Array.isArray(bots) ? bots : []).filter(
    (bot) => bot && typeof bot === "object" && typeof bot.id === "string" && bot.id,
  );
}

function rosterBot(id) {
  const wanted = String(id || "");
  return wanted ? roster.find((bot) => bot.id === wanted) || null : null;
}

function synthesizedBot(id) {
  const wanted = String(id || "");
  if (wanted === "thyca") {
    return { id: "thyca", name: "Thyca", system: true };
  }
  return { id: wanted, name: wanted };
}

function resolveBot(id) {
  return rosterBot(id) || synthesizedBot(id);
}

export function chatBotForMessage(message, fallback = null) {
  const meta = (message && typeof message === "object" ? message.meta : null) || {};
  const id = String(meta.bot_id || fallback?.id || "thyca");
  const current = id ? rosterBot(id) : null;
  const base = current || (fallback?.id === id ? fallback : synthesizedBot(id));
  const historicalName = typeof meta.bot_name === "string" && meta.bot_name.trim()
    ? meta.bot_name : "";
  const name = current?.name || historicalName || base.name;
  const tint = botLookFor(id).tint;
  return { ...base, id, name, tint, avatar: botLookFor(id).glyph };
}

const MENTION_RE = /@([a-z0-9]+(?:-[a-z0-9]+)*)/g;

function latestUserText(detail) {
  const messages = Array.isArray(detail?.messages) ? detail.messages : [];
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index];
    if (message && message.role === "user" && typeof message.content === "string") {
      return message.content;
    }
  }
  return "";
}

export function chatBotForTurn(detail, text = null) {
  const participants = (Array.isArray(detail?.bots) ? detail.bots : [])
    .map(String).filter(Boolean);
  const bots = participants.length ? participants : ["thyca"];
  const paused = new Set(roster
    .filter((bot) => String(bot.status || "") === "paused")
    .map((bot) => bot.id));
  const active = bots.filter((id) => !paused.has(id));
  const prompt = typeof text === "string" ? text : latestUserText(detail);
  for (const mention of prompt.matchAll?.(MENTION_RE) || []) {
    if (active.includes(mention[1])) return resolveBot(mention[1]);
  }
  return resolveBot(active[0] || bots[0] || "thyca");
}
