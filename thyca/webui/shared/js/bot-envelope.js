import { botLookFor } from "./bot-avatar.js";

/* Presentational envelope shell shared by chat and memories. The caller owns
   its header button, selection behavior, and paper contents. */
export function createBotEnvelope(bot, header) {
  const node = document.createElement("div");
  node.className = "team-cluster team-envelope";
  const look = botLookFor(bot);
  node.style.setProperty("--team-tint", `var(--color-${look.tint})`);
  const viewport = document.createElement("div");
  viewport.className = "team-pocket-viewport";
  const paper = document.createElement("div");
  paper.className = "team-cluster-sessions";
  paper.id = `team-bot-pocket-${encodeURIComponent(String(bot?.id || ""))}`;
  viewport.append(paper);
  node.append(header, viewport);
  const button = header.children[0] || null;
  button?.setAttribute("aria-controls", paper.id);
  return { node, header, button, viewport, paper };
}

export function setBotEnvelopeOpen(card, open) {
  const expanded = Boolean(open);
  card.node.classList.toggle("is-open", expanded);
  card.button?.classList.toggle("is-open", expanded);
  card.button?.setAttribute("aria-expanded", String(expanded));
  card.viewport.setAttribute("aria-hidden", String(!expanded));
  card.viewport.inert = !expanded;
  card.paper.setAttribute("aria-hidden", String(!expanded));
  card.paper.inert = !expanded;
}
