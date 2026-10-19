import { getJson, postJson } from "../../shared/js/http.js";
import { botMark } from "../../shared/js/bot-avatar.js";
import { pendingCandidates } from "../chat/team-data.js";

// Nhật ký › "Chờ cho cả đội nhớ" (FE-5, r9 §4.8): the tab exists in the DOM
// but stays hidden unless the candidate queue is non-empty (contract §6:
// GET /api/memory/candidates, POST …/<id>/approve|reject). This module is
// separate from memories.js (whose node harness replays that file alone),
// so it owns its own nav button + section and deactivates itself whenever
// one of the built-in views is chosen.
const TAB_ID = "pending-tab";
const VIEW_ID = "pending-view";

let tab = null;
let view = null;
let chips = null;
let list = null;
let empty = null;
let status = null;
let items = [];
let filter = "";
let busy = false;

function setStatus(text = "", kind = "") {
  if (!status) return;
  status.textContent = text;
  status.className = `screen-note memory-status screen-status${kind ? ` is-${kind}` : ""}`;
}

function botLabel(item) {
  return item.botId || "không rõ bot";
}

// Bot registry snapshot for display names + card avatars (pattern shared
// with cost-bots/request): fetched once, ids render as-is on failure.
let roster = null;
let rosterPromise = null;

async function loadRoster() {
  if (roster) return roster;
  if (!rosterPromise) {
    rosterPromise = (async () => {
      const map = new Map();
      try {
        const payload = await getJson("/api/bots");
        const list = Array.isArray(payload) ? payload : payload?.bots;
        for (const bot of Array.isArray(list) ? list : []) {
          if (bot && typeof bot.id === "string" && bot.id) map.set(bot.id, bot);
        }
      } catch {
        // Registry unavailable: ids render as-is.
      }
      roster = map;
      return map;
    })();
  }
  return rosterPromise;
}

function botDisplayName(item, known = roster) {
  const id = item?.botId || (typeof item === "string" ? item : "");
  if (!id) return "không rõ bot";
  const bot = known?.get(id);
  const name = bot && typeof bot.name === "string" && bot.name ? bot.name : "";
  return name || id;
}

/* Avatar slot for one bot id: empty (aligned) when unknown, else the idle
   seal — from the roster card when known so tint/avatar match the card. */
export function avatarSlotFor(botId, known = roster) {
  const slot = document.createElement("span");
  slot.className = "bot-avatar-slot pending-avatar";
  slot.setAttribute("aria-hidden", "true");
  const id = typeof botId === "string" ? botId : "";
  if (!id) return slot;
  slot.dataset.botId = id;
  const bot = known?.get(id);
  slot.innerHTML = botMark(bot || { id }, { state: "idle" });
  return slot;
}

function filters() {
  const bots = [...new Set(items.map((item) => item.botId || ""))].sort((a, b) => a.localeCompare(b, "vi"));
  return ["", ...bots];
}

function renderChips() {
  if (!chips) return;
  chips.replaceChildren();
  for (const bot of filters()) {
    chips.append(filterChip(bot, filter === bot));
  }
}

/* One filter chip: seal + name for a bot, plain "Tất cả bot" for "".
   Exported for tests; the click handler is injected so node harnesses
   can observe selection without the module render loop. */
export function filterChip(bot, active, onSelect) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "screen-button pending-chip";
  button.setAttribute("aria-pressed", String(active));
  button.classList.toggle("is-active", active);
  if (!bot) {
    button.textContent = "Tất cả bot";
  } else {
    button.append(avatarSlotFor(bot), document.createTextNode(botDisplayName({ botId: bot })));
  }
  button.addEventListener("click", () => {
    if (typeof onSelect === "function") onSelect(bot);
    else {
      filter = bot;
      render();
    }
  });
  return button;
}

function candidateDate(item) {
  const raw = item.createdAt || String(item.sessionId || "").split("#")[0];
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getDate()}/${date.getMonth() + 1}`;
}

export function candidateCard(item, known = roster) {
  const article = document.createElement("article");
  article.className = "memory-card pending-card";
  const time = document.createElement("time");
  if (item.createdAt) time.dateTime = item.createdAt;
  time.textContent = candidateDate(item);
  article.append(time);
  const copy = document.createElement("div");
  copy.className = "memory-copy";
  const heading = document.createElement("h3");
  heading.textContent = item.title || `Đề xuất từ ${botDisplayName(item, known)}`;
  const description = document.createElement("p");
  description.textContent = item.text || "(trống)";
  copy.append(heading, description);
  article.append(copy);
  const actions = document.createElement("div");
  actions.className = "pending-actions";
  const source = document.createElement("span");
  source.className = "pending-source";
  const label = document.createElement("span");
  label.textContent = `từ ${botDisplayName(item, known)}`;
  label.dataset.botId = item.botId || "";
  source.append(avatarSlotFor(item.botId, known), label);
  const approve = document.createElement("button");
  approve.className = "screen-button is-primary";
  approve.type = "button";
  approve.textContent = "Cho cả đội nhớ";
  approve.disabled = busy;
  approve.addEventListener("click", () => void decide(item.id, "approve"));
  const reject = document.createElement("button");
  reject.className = "screen-button";
  reject.type = "button";
  reject.textContent = "Bỏ qua";
  reject.disabled = busy;
  reject.addEventListener("click", () => void decide(item.id, "reject"));
  actions.append(source, reject, approve);
  article.append(actions);
  return article;
}

function render() {
  if (!view || !list) return;
  renderChips();
  const shown = filter ? items.filter((item) => (item.botId || "") === filter) : items;
  list.replaceChildren(...shown.map((item) => candidateCard(item)));
  if (empty) {
    empty.hidden = shown.length > 0;
    empty.textContent = items.length
      ? "Không có đề xuất nào của bot này."
      : "Không có đề xuất nào đang chờ.";
  }
  if (busy) {
    for (const button of list.querySelectorAll("button")) button.disabled = true;
  }
  void refreshRoster();
}

/* Refresh display names + seals once the roster arrives. Skipped while a
   decision is in flight so the busy state is never disturbed; the next
   render picks the cached roster up. Paint-once: the second pass renders
   with names and stops. */
let rosterPainted = false;

async function refreshRoster() {
  await loadRoster();
  if (busy || rosterPainted) return;
  if (!list?.isConnected) return;
  rosterPainted = true;
  render();
}

function showPending() {
  if (!tab || !view) return;
  document.querySelectorAll("#memory-list").forEach((node) => { node.hidden = true; });
  document.querySelectorAll(".memory-view").forEach((node) => { node.hidden = true; });
  const emptyNote = document.querySelector("#memory-empty");
  if (emptyNote) emptyNote.hidden = true;
  document.querySelectorAll(".session-item[data-view]").forEach((button) => {
    button.classList.remove("is-active");
    button.setAttribute("aria-pressed", "false");
  });
  tab.classList.add("is-active");
  tab.setAttribute("aria-pressed", "true");
  view.hidden = false;
  render();
}

function hidePending({ restore = false } = {}) {
  if (!tab || !view) return;
  const wasActive = !view.hidden;
  tab.classList.remove("is-active");
  tab.setAttribute("aria-pressed", "false");
  view.hidden = true;
  if (restore && wasActive) {
    // The queue emptied while the pending view was on screen. showPending()
    // hid #memory-list and every .memory-view, so without this the
    // workspace stays blank. Re-activate the built-in overview through its
    // own button so memories.js renders itself; fall back to unhiding the
    // list when that button is absent.
    const overview = document.querySelector('.session-item[data-view="overview"]');
    if (overview) overview.click();
    else {
      const listNode = document.querySelector("#memory-list");
      if (listNode) listNode.hidden = false;
    }
  }
}

async function decide(id, action) {
  if (busy || !id) return;
  busy = true;
  render();
  setStatus(action === "approve" ? "Đang cho cả đội nhớ…" : "Đang bỏ qua…");
  try {
    await postJson(`/api/memory/candidates/${encodeURIComponent(id)}/${action}`, {});
    setStatus(action === "approve" ? "Đã cho cả đội nhớ." : "Đã bỏ qua.", "success");
    await load({ quiet: true });
  } catch (error) {
    setStatus(error instanceof Error && error.message ? error.message : "Không lưu được.", "error");
  } finally {
    busy = false;
    render();
  }
}

async function load({ quiet = false } = {}) {
  if (!tab || !view) return;
  if (!quiet) setStatus("Đang đọc đề xuất…");
  let next = [];
  try {
    next = pendingCandidates(await getJson("/api/memory/candidates"));
    if (!quiet) setStatus();
  } catch {
    // Backend has no candidate queue yet: the tab stays hidden, quietly.
    next = [];
    if (!quiet) setStatus();
  }
  items = next;
  if (!items.some((item) => (item.botId || "") === filter)) filter = "";
  tab.hidden = items.length === 0;
  if (!items.length) hidePending({ restore: true });
  else if (!view.hidden) render();
}

function bind() {
  tab = document.querySelector(`#${TAB_ID}`);
  view = document.querySelector(`#${VIEW_ID}`);
  chips = document.querySelector("#pending-filters");
  list = document.querySelector("#pending-list");
  empty = document.querySelector("#pending-empty");
  status = document.querySelector("#pending-status");
  if (!tab || !view || !list) return; // not on memories.html (node harness)
  tab.addEventListener("click", showPending);
  document.querySelectorAll(".session-item[data-view]").forEach((button) => {
    button.addEventListener("click", hidePending);
  });
  // A viewport crossing rebuilds the built-in views; the pending section
  // would otherwise linger next to the rebuilt list.
  if (typeof matchMedia === "function") {
    matchMedia("(max-width: 56rem)").addEventListener?.("change", hidePending);
  }
  void load();
}

// Guarded for node harnesses: pure-helper tests import this module
// without a DOM; bind() itself already no-ops off memories.html.
if (typeof document !== "undefined") bind();
