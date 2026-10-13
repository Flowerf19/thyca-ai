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
  const total = groups.reduce((sum, group) => sum + (group.costUsd || 0), 0);
  const list = document.createElement("ul");
  list.className = "journal cost-bots";
  for (const group of groups) {
    const row = document.createElement("li");
    row.className = "journal-entry cost-bot-entry";
    const gutter = document.createElement("span");
    gutter.className = "journal-date";
    gutter.textContent = `${group.turns} lượt`;
    const body = document.createElement("div");
    body.className = "journal-body";
    const title = document.createElement("div");
    title.className = "journal-row-title";
    const heading = document.createElement("h3");
    const name = document.createElement("span");
    name.className = "cost-bot-name";
    name.dataset.botId = group.botId;
    name.textContent = group.botId || "chưa gắn bot";
    heading.append(name);
    const amount = document.createElement("strong");
    amount.className = "journal-amount";
    amount.textContent = group.costUsd == null ? "—" : formatCost(group.costUsd, 4);
    title.append(heading, amount);
    const meter = document.createElement("div");
    meter.className = "journal-meter";
    meter.setAttribute("aria-hidden", "true");
    const fill = document.createElement("span");
    const share = group.costUsd != null && total > 0 ? (group.costUsd / total) * 100 : 0;
    fill.style.setProperty("--share", `${share}%`);
    fill.style.background = group.botId ? tintVarFor(group.botId, "") : "var(--color-muted)";
    meter.append(fill);
    const meta = document.createElement("p");
    meta.className = "journal-meta";
    for (const text of [
      group.costUsd == null ? "chưa định giá" : `${Math.round(share)}%`,
      group.requests ? `${group.requests} lần gọi model` : "",
      group.costUsd != null && group.pricedTurns < group.turns ? "số đã ghi nhận" : "",
    ].filter(Boolean)) {
      const span = document.createElement("span");
      span.textContent = text;
      meta.append(span);
    }
    body.append(title, meter, meta);
    row.append(gutter, body);
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
