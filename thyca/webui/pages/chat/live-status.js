import {
  brandState,
  TURN_FAILED_STATUS,
} from "./chat-status.js";
import { chatBrandHeader, setChatBrand } from "./chat-view.js";
import { teamBotLive, teamBotTool } from "./team-sidebar.js";

// The live card is deliberately minimal: the bot's brand header plus the
// streaming reply. Live activity (đang nghĩ…, tool calls) lives on the bot
// card's description in the sidebar — it is routed there, not rendered here.
export function createLiveStatus(root, startedAt, bot = null) {
  const article = document.createElement("article");
  article.className = "live-card live-status";
  article.setAttribute("aria-label",
    `${(bot && bot.name) || "Thyca"} đang trả lời`);
  article.setAttribute("aria-live", "polite");
  const live = {
    article,
    startedAt: startedAt || null,
    brand: null,
    reply: null,
  };
  const header = chatBrandHeader({
    state: "busy",
    status: "",
    bot,
  });
  article.append(header);
  root.append(article);
  live.brand = header;
  return live;
}

// The visible reply streams into its own paragraph below the brand header;
// plain text here — markdown renders in the transcript.
function replySegment(live) {
  if (!live.reply) {
    const p = document.createElement("p");
    p.className = "reply-live";
    live.article.append(p);
    live.reply = p;
  }
  return live.reply;
}

// A re-follow replays the whole turn; rebuild the reply from scratch so the
// replay does not duplicate text an earlier attempt already added.
export function resetLiveStatus(live, startedAt) {
  live.reply?.remove();
  live.reply = null;
  if (startedAt) live.startedAt = startedAt;
}

export function updateLiveStatus(live, event) {
  if (event?.type === "llm.content") {
    const p = replySegment(live);
    p.append(document.createTextNode(event.delta));
  }
  const nextBrand = brandState(event);
  teamBotLive(live.brand?._botProfile?.id, nextBrand === "busy" ? "thinking"
    : nextBrand === "error" ? "error" : "done");
  setChatBrand(live, {
    state: nextBrand,
    status: event?.type === "turn.failed" ? TURN_FAILED_STATUS : undefined,
  });
  const starts = event?.type === "tool.started" || event?.type === "skill.started";
  const finishes = event?.type === "tool.finished" || event?.type === "skill.finished";
  if (starts) {
    teamBotTool(live.brand?._botProfile?.id, event.call_id || `${event.type}:${event.name || "tool"}`, event.name || "tool", true);
  } else if (finishes) {
    teamBotTool(live.brand?._botProfile?.id, event.call_id || `${event.type}:${event.name || "tool"}`, event.name || "tool", false);
  }
}