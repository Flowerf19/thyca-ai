import { getJson, postJson } from "../../shared/js/http.js";
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

function filters() {
  const bots = [...new Set(items.map((item) => item.botId || ""))].sort((a, b) => a.localeCompare(b, "vi"));
  return ["", ...bots];
}

function renderChips() {
  if (!chips) return;
  chips.replaceChildren();
  for (const bot of filters()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "screen-button pending-chip";
    button.setAttribute("aria-pressed", String(filter === bot));
    button.classList.toggle("is-active", filter === bot);
    button.textContent = bot === "" ? "Tất cả bot" : bot;
    button.addEventListener("click", () => {
      filter = bot;
      render();
    });
    chips.append(button);
  }
}

function candidateCard(item) {
  const article = document.createElement("article");
  article.className = "memory-card pending-card";
  const time = document.createElement("time");
  time.textContent = botLabel(item);
  article.append(time);
  const copy = document.createElement("div");
  copy.className = "memory-copy";
  const heading = document.createElement("h3");
  heading.textContent = `Đề xuất từ ${botLabel(item)}`;
  const description = document.createElement("p");
  description.textContent = item.text || "(trống)";
  copy.append(heading, description);
  article.append(copy);
  const actions = document.createElement("div");
  actions.className = "pending-actions";
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
  actions.append(approve, reject);
  article.append(actions);
  return article;
}

function render() {
  if (!view || !list) return;
  renderChips();
  const shown = filter ? items.filter((item) => (item.botId || "") === filter) : items;
  list.replaceChildren(...shown.map(candidateCard));
  if (empty) {
    empty.hidden = shown.length > 0;
    empty.textContent = items.length
      ? "Không có đề xuất nào của bot này."
      : "Không có đề xuất nào đang chờ.";
  }
  if (busy) {
    for (const button of list.querySelectorAll("button")) button.disabled = true;
  }
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

function hidePending() {
  if (!tab || !view) return;
  tab.classList.remove("is-active");
  tab.setAttribute("aria-pressed", "false");
  view.hidden = true;
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
  if (!items.length) hidePending();
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

bind();
