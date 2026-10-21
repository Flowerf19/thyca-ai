import { getJson, postJson } from "../../shared/js/http.js";
import { botMark } from "../../shared/js/bot-avatar.js";
import { pendingCandidates } from "../chat/team-data.js";
import { memoryScopeMatches } from "../../shared/js/memory-data.js";

let view = null;
let chips = null;
let list = null;
let empty = null;
let status = null;
let items = [];
let filter = "";
let scopeId = null;
let invalidInitialScope = false;
let busy = false;
let loadToken = 0;
let loadController = null;
let roster = null;
let rosterPromise = null;

function setStatus(text = "", kind = "") {
  if (!status) return;
  status.textContent = text;
  status.className = `screen-note memory-status screen-status${kind ? ` is-${kind}` : ""}`;
}

function botLabel(item) {
  return item.botId || "không rõ bot";
}

async function loadRoster() {
  if (roster) return roster;
  if (!rosterPromise) {
    rosterPromise = (async () => {
      const map = new Map();
      try {
        const payload = await getJson("/api/bots");
        const bots = Array.isArray(payload) ? payload : payload?.bots;
        for (const bot of Array.isArray(bots) ? bots : []) {
          if (bot && typeof bot.id === "string" && bot.id) map.set(bot.id, bot);
        }
      } catch {
        // Registry unavailable: display owner ids without falling back in data scope.
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
  return bot && typeof bot.name === "string" && bot.name ? bot.name : id;
}

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
  if (scopeId) return [scopeId];
  const bots = [...new Set(items.map((item) => item.botId || ""))]
    .sort((a, b) => a.localeCompare(b, "vi"));
  return ["", ...bots];
}

function renderChips() {
  if (!chips) return;
  chips.hidden = Boolean(scopeId);
  chips.replaceChildren();
  if (scopeId) return;
  for (const bot of filters()) chips.append(filterChip(bot, filter === bot));
}

export function filterChip(bot, active, onSelect) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "screen-button pending-chip";
  button.setAttribute("aria-pressed", String(active));
  button.classList.toggle("is-active", active);
  if (!bot) button.textContent = "Tất cả bot";
  else button.append(avatarSlotFor(bot), document.createTextNode(botDisplayName({ botId: bot })));
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
  const shown = filter && !scopeId
    ? items.filter((item) => (item.botId || "") === filter)
    : items;
  list.replaceChildren(...shown.map((item) => candidateCard(item)));
  if (empty) {
    empty.hidden = shown.length > 0;
    if (shown.length) empty.textContent = "";
    else if (status?.classList.contains("is-error")) {
      empty.hidden = false;
      empty.textContent = status.textContent;
    } else empty.textContent = items.length
      ? "Không có đề xuất nào của bot này."
      : "Không có đề xuất nào đang chờ.";
  }
  if (busy) {
    for (const button of list.querySelectorAll("button")) button.disabled = true;
  }
  view.hidden = shown.length === 0 && !status?.classList.contains("is-error");
  void refreshRoster();
}

let rosterPainted = false;

async function refreshRoster() {
  await loadRoster();
  if (busy || rosterPainted || !list?.isConnected) return;
  rosterPainted = true;
  render();
}

export function candidateRequestUrl(botId) {
  return botId
    ? `/api/memory/candidates?bot_id=${encodeURIComponent(botId)}`
    : "/api/memory/candidates";
}

function requestUrl() {
  return candidateRequestUrl(scopeId);
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
  if (!view || !list) return;
  if (invalidInitialScope) {
    items = [];
    list.replaceChildren();
    setStatus("Liên kết phạm vi bot không hợp lệ. Hãy chọn Tổng quan hoặc làm mới trang.", "error");
    view.hidden = false;
    render();
    return;
  }
  const token = ++loadToken;
  loadController?.abort();
  const controller = new AbortController();
  loadController = controller;
  const requestedScope = scopeId;
  items = [];
  filter = "";
  list.replaceChildren();
  if (!quiet) setStatus("Đang đọc đề xuất…");
  view.hidden = !requestedScope;
  try {
    const payload = await getJson(requestUrl(), { signal: controller.signal });
    if (token !== loadToken || requestedScope !== scopeId) return;
    if (!memoryScopeMatches(payload, requestedScope)) {
      throw new Error("Phản hồi đề xuất không khớp phạm vi đã chọn. Hãy làm mới trang để đồng bộ máy chủ.");
    }
    items = pendingCandidates(payload);
    if (!items.some((item) => (item.botId || "") === filter)) filter = "";
    setStatus();
    view.hidden = items.length === 0;
  } catch (error) {
    if (token !== loadToken || requestedScope !== scopeId) return;
    const scopeMismatch = error instanceof Error && error.message.includes("không khớp phạm vi");
    const showError = Boolean(requestedScope || scopeMismatch);
    setStatus(showError
      ? (error instanceof Error && error.message ? error.message : "Không tải được đề xuất.")
      : "", showError ? "error" : "");
    view.hidden = !showError;
  } finally {
    if (token === loadToken && requestedScope === scopeId) {
      if (loadController === controller) loadController = null;
      render();
    }
  }
}

function bind() {
  view = document.querySelector("#pending-view");
  chips = document.querySelector("#pending-filters");
  list = document.querySelector("#pending-list");
  empty = document.querySelector("#pending-empty");
  status = document.querySelector("#pending-status");
  if (!view || !list) return;
  document.addEventListener("memory-scope-before-change", (event) => {
    if (busy) event.preventDefault();
  });
  document.addEventListener("memory-scope-changed", (event) => {
    scopeId = event.detail?.botId || null;
    invalidInitialScope = false;
    void load();
  });
  const requestedIds = new URLSearchParams(location.search).getAll("bot_id");
  if (requestedIds.length) {
    invalidInitialScope = requestedIds.length !== 1
      || requestedIds[0].length > 64
      || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(requestedIds[0]);
    if (!invalidInitialScope) scopeId = requestedIds[0];
  }
  void load();
}

if (typeof document !== "undefined") bind();
