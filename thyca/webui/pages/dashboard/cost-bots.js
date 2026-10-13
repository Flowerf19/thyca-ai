import { getJson } from "../../shared/js/http.js";
import { formatCost } from "../../shared/js/format.js";
import { aggregateBotCost } from "./cost-data.js";
import { tintVarFor } from "../chat/team-data.js";

// Cost per bot (FE-5): one block inside the Chi phí view, fed by the same
// snapshot rows the journal pages. renderCostBots is called from the journal
// render() so it never shows another period's numbers. Bot display names come
// from GET /api/bots when the registry exists; otherwise the raw bot id
// shows. Rows without bot meta render as one honest "chưa gắn bot" line —
// the backend on main does not emit bot_id yet, so today that line is the
// whole block (recorded-known, never claimed complete).
let names = null;

async function botNames() {
  if (names) return names;
  names = new Map();
  try {
    const payload = await getJson("/api/bots");
    const list = Array.isArray(payload) ? payload : payload?.bots;
    for (const bot of Array.isArray(list) ? list : []) {
      if (bot && typeof bot.id === "string" && bot.id) {
        names.set(bot.id, typeof bot.name === "string" && bot.name ? bot.name : bot.id);
      }
    }
  } catch {
    // Registry unavailable: ids render as-is.
  }
  return names;
}

export function renderCostBots(rows) {
  const mount = document.querySelector("#cost-bots");
  if (!mount) return;
  const groups = aggregateBotCost(rows);
  mount.replaceChildren();
  if (!groups.length) {
    const note = document.createElement("p");
    note.className = "screen-note";
    note.textContent = "Chưa có lượt nào được định giá trong kỳ này.";
    mount.append(note);
    return;
  }
  const priced = groups.some((group) => group.costUsd != null);
  const list = document.createElement("ul");
  list.className = "journal cost-bots";
  for (const group of groups) {
    const row = document.createElement("li");
    row.className = "journal-row cost-bot-row";
    const dot = document.createElement("span");
    dot.className = "team-bot-dot";
    dot.style.setProperty("--team-tint", tintVarFor(group.botId, ""));
    dot.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "cost-bot-name";
    name.dataset.botId = group.botId;
    name.textContent = group.botId || "chưa gắn bot";
    const meta = document.createElement("span");
    meta.className = "journal-meta";
    const turns = `${group.turns} lượt`;
    meta.textContent = group.costUsd == null ? `${turns} · chưa định giá` : `${turns} · ${formatCost(group.costUsd, 4)} (số đã ghi nhận)`;
    row.append(dot, name, meta);
    list.append(row);
  }
  mount.append(list);
  if (!priced) {
    const note = document.createElement("p");
    note.className = "screen-note";
    note.textContent = "Chưa có lượt nào được định giá trong kỳ này.";
    mount.append(note);
  } else if (groups.some((group) => group.costUsd == null)) {
    const note = document.createElement("p");
    note.className = "screen-note";
    note.textContent = "Một số lượt chưa định giá nên tổng theo bot là số đã ghi nhận.";
    mount.append(note);
  }
  void botNames().then((known) => {
    if (!mount.isConnected) return;
    for (const name of mount.querySelectorAll(".cost-bot-name")) {
      const id = name.dataset.botId || "";
      if (id && known.has(id)) name.textContent = known.get(id);
    }
  });
}
