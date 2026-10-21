import { getJson, postJson } from "../../shared/js/http.js";
import { completeDays, rollingRange } from "../../shared/js/analytics-data.js";
import { drawBarChart } from "../../shared/js/bar-chart.js";
import { formatDate, formatDateTime, formatInteger } from "../../shared/js/format.js";
import { memoryScopeMatches, selectMemories } from "../../shared/js/memory-data.js";
import { botMark, botLookFor, ensureBotAvatarDefs } from "../../shared/js/bot-avatar.js";
import { createBotEnvelope, setBotEnvelopeOpen } from "../../shared/js/bot-envelope.js";

const el = {
  workspace: document.querySelector("#memory-workspace"),
  list: document.querySelector("#memory-sections"),
  empty: document.querySelector("#memory-empty"),
  title: document.querySelector("#memory-scope-title"),
  description: document.querySelector("#memory-scope-description"),
  viewSections: [...document.querySelectorAll(".memory-view")],
  search: document.querySelector("#memory-search"),
  status: document.querySelector("#memory-status"),
  globalNav: document.querySelector("#memory-global-nav"),
  botNav: document.querySelector("#memory-bot-list"),
};
const compact = typeof matchMedia === "function"
  ? matchMedia("(max-width: 56rem)")
  : { matches: false, addEventListener: () => {} };

function placeControls() {
  const cluster = el.search?.closest(".memory-search-cluster");
  const bar = document.querySelector(".memory-search-bar");
  const sessions = document.querySelector("#memory-nav");
  if (!cluster) return;
  if (compact.matches) {
    if (bar && cluster.parentNode !== bar) bar.append(cluster);
  } else if (sessions && (cluster.parentNode !== sessions
    || cluster.nextElementSibling !== el.globalNav)) {
    sessions.insertBefore(cluster, el.globalNav);
  }
}

const state = {
  stats: { leaves: [] },
  statsError: true,
  scopeId: null,
  scopeName: "",
  scopeInvalid: false,
  bots: [],
  // Drafts stay in memory while changing scope and reappear when that scope
  // is selected again; a save/forget/reinforce request cannot change scope.
  editing: null, // { id, sessionId, topic, summary }
  pending: false,
  scopeToken: 0,
  statsRequest: 0,
  statsController: null,
};

function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function setStatus(message = "", kind = "") {
  if (!el.status) return;
  el.status.textContent = message;
  el.status.className = `screen-note memory-status screen-status${kind ? ` is-${kind}` : ""}`;
}

function setBusy(busy) {
  el.list?.setAttribute("aria-busy", String(busy));
  for (const form of document.querySelectorAll(".memory-edit")) {
    for (const control of form.querySelectorAll("button, input, textarea")) control.disabled = busy;
  }
  for (const button of document.querySelectorAll("[data-memory-scope]")) button.disabled = busy;
}

function botRow(bot) {
  const row = document.createElement("div");
  row.className = "session-row team-envelope-header";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-item team-bot-item";
  button.dataset.memoryScope = String(bot.id || "");
  button.setAttribute("aria-label", `Xem nhật ký của ${bot.name || bot.id}`);
  const avatar = document.createElement("span");
  avatar.className = "team-avatar";
  avatar.style.setProperty("--team-tint", `var(--color-${botLookFor(bot).tint})`);
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = botMark(bot);
  const name = document.createElement("span");
  name.className = "session-name";
  name.textContent = bot.name || bot.id;
  const role = document.createElement("time");
  role.textContent = bot.role || bot.id;
  const caret = document.createElement("span");
  caret.className = "team-bot-caret";
  caret.setAttribute("aria-hidden", "true");
  button.append(avatar, name, role, caret);
  row.append(button);
  button.addEventListener("click", () => selectScope(bot.id, bot.name));
  return row;
}

function renderScopes() {
  if (!el.botNav) return;
  ensureBotAvatarDefs();
  const active = state.scopeId || "";
  const global = el.globalNav?.querySelector("[data-memory-scope='']");
  if (global) {
    global.classList.toggle("is-active", !active && !state.scopeInvalid);
    global.setAttribute("aria-pressed", String(!active && !state.scopeInvalid));
    if (!active && !state.scopeInvalid) global.setAttribute("aria-current", "page");
    else global.removeAttribute("aria-current");
    global.disabled = state.pending;
  }
  const old = new Map([...el.botNav.children].map((card) => [card.dataset.botId, card]));
  const desired = state.bots.map((bot) => {
    let card = old.get(bot.id);
    if (!card) {
      const envelope = createBotEnvelope(bot, botRow(bot));
      card = envelope.node;
      card.dataset.botId = bot.id;
      card._envelope = envelope;
    } else {
      const button = card._envelope.button;
      const row = botRow(bot);
      const freshButton = row.children[0];
      const avatar = freshButton.children[0];
      const existingAvatar = button.children[0];
      if (existingAvatar) {
        existingAvatar.innerHTML = avatar.innerHTML;
        existingAvatar.style.setProperty("--team-tint", `var(--color-${botLookFor(bot).tint})`);
      }
      button.children[1].textContent = bot.name || bot.id;
      button.children[2].textContent = bot.role || bot.id;
      button.setAttribute("aria-label", `Xem nhật ký của ${bot.name || bot.id}`);
    }
    card._envelope ||= {
      node: card,
      header: card.children[0],
      button: card.children[0].children[0],
      viewport: card.children[1],
      paper: card.children[1].children[0],
    };
    const description = bot.description?.trim();
    let note = card._envelope.paper.querySelector(".team-bot-description");
    if (description) {
      if (!note) {
        note = document.createElement("p");
        note.className = "team-bot-description";
      }
      note.textContent = description;
      card._envelope.paper.replaceChildren(note);
    } else if (note) card._envelope.paper.replaceChildren();
    card._envelope.button.classList.toggle("is-active", bot.id === active);
    if (bot.id === active) card._envelope.button.setAttribute("aria-current", "page");
    else card._envelope.button.removeAttribute("aria-current");
    setBotEnvelopeOpen(card._envelope, bot.id === active);
    card._envelope.button.disabled = state.pending;
    return card;
  });
  el.botNav.replaceChildren(...desired);
}

function scopeUrl(path, botId = state.scopeId) {
  return botId ? `${path}?bot_id=${encodeURIComponent(botId)}` : path;
}

const BOT_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function replaceScopeUrl(botId) {
  const url = new URL(location.href);
  if (botId) url.searchParams.set("bot_id", botId);
  else url.searchParams.delete("bot_id");
  history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

function selectScope(botId = null, botName = "") {
  const nextId = botId || null;
  if (state.pending || (nextId && (nextId.length > 64 || !BOT_ID_PATTERN.test(nextId)))) return false;
  if (nextId && state.bots.length && !state.bots.some((bot) => bot.id === nextId)) return false;
  if (nextId === state.scopeId && !state.scopeInvalid) return true;
  const changing = new CustomEvent("memory-scope-before-change", {
    cancelable: true,
    detail: { botId: nextId },
  });
  document.dispatchEvent(changing);
  if (changing.defaultPrevented) return false;
  state.scopeId = nextId;
  state.scopeInvalid = false;
  state.scopeName = botName || state.bots.find((bot) => bot.id === nextId)?.name || nextId || "";
  state.scopeToken += 1;
  state.stats = { leaves: [] };
  state.statsError = true;
  replaceScopeUrl(nextId);
  el.empty.hidden = true;
  el.title.textContent = nextId ? `Nhật ký của ${state.scopeName}` : "Nhật ký của bạn";
  el.description.textContent = nextId
    ? `Những trang bộ nhớ thuộc bot ${state.scopeName}.`
    : "Những trang Thyca ghi lại từ các cuộc trò chuyện.";
  render();
  document.dispatchEvent(new CustomEvent("memory-scope-changed", {
    detail: { botId: nextId },
  }));
  void loadStats();
  return true;
}

function memoryCard(memory) {
  const article = document.createElement("article");
  article.className = "memory-card";
  article.dataset.memoryId = memory.id;
  const time = document.createElement("time");
  time.textContent = memory.date === "Không rõ ngày" ? memory.date : formatDate(memory.date);
  article.append(time);
  if (state.editing?.id === memory.id) {
    article.classList.add("is-editing");
    article.append(editForm(memory));
    return article;
  }
  const copy = document.createElement("div");
  copy.className = "memory-copy";
  const heading = document.createElement("h3");
  heading.textContent = memory.title;
  const description = document.createElement("p");
  description.textContent = memory.description;
  copy.append(heading, description);
  const meta = document.createElement("div");
  meta.className = "memory-meta";
  for (const label of [`${memory.uses} lần dùng`, `${memory.searches} lần tìm`]) {
    const tag = document.createElement("span");
    tag.textContent = label;
    meta.append(tag);
  }
  if (memory.expiresAt) {
    const tag = document.createElement("span");
    tag.textContent = `hết hạn ${formatDateTime(memory.expiresAt)}`;
    meta.append(tag);
  }
  const more = document.createElement("button");
  more.className = "memory-more row-action";
  more.type = "button";
  more.disabled = !memory.sessionId;
  more.setAttribute("aria-label", `Sửa trang ${memory.title}`);
  more.textContent = "⋮";
  more.addEventListener("click", (event) => startEdit(memory, event.currentTarget));
  meta.append(more);
  article.append(copy, meta);
  return article;
}

function editForm(memory) {
  const form = document.createElement("form");
  form.className = "memory-edit";
  const line = document.createElement("div");
  line.className = "memory-edit-line";
  const title = document.createElement("input");
  title.className = "memory-edit-title";
  title.required = true;
  title.maxLength = 120;
  title.autocomplete = "off";
  title.placeholder = "Tên trang nhật ký";
  title.setAttribute("aria-label", "Tiêu đề trang");
  title.value = state.editing.topic;
  title.addEventListener("input", () => { state.editing.topic = title.value; });
  line.append(title);
  const text = document.createElement("textarea");
  text.className = "memory-edit-text";
  text.required = true;
  text.maxLength = 400;
  text.placeholder = "Nội dung ghi lại từ phiên trò chuyện";
  text.setAttribute("aria-label", "Mô tả trang");
  text.value = state.editing.summary;
  text.addEventListener("input", () => { state.editing.summary = text.value; });
  const actions = document.createElement("div");
  actions.className = "memory-edit-actions";
  const save = document.createElement("button");
  save.className = "screen-button is-primary";
  save.type = "submit";
  save.textContent = "Lưu trang";
  const cancel = document.createElement("button");
  cancel.className = "screen-button";
  cancel.type = "button";
  cancel.textContent = "Hủy";
  cancel.addEventListener("click", () => cancelEdit(cancel));
  const reinforce = document.createElement("button");
  reinforce.className = "screen-button";
  reinforce.type = "button";
  reinforce.textContent = "Gia hạn";
  reinforce.addEventListener("click", () => {
    void mutateMemory("/api/memory/reinforce", { session_id: state.editing.sessionId }, "Đã gia hạn trang.");
  });
  const forget = document.createElement("button");
  forget.className = "screen-button is-danger";
  forget.type = "button";
  forget.textContent = "Quên";
  forget.addEventListener("click", () => {
    if (!confirm(`Quên “${state.editing.topic || "trang này"}” khỏi bộ nhớ?`)) return;
    void mutateMemory("/api/memory/forget", { session_id: state.editing.sessionId }, "Đã quên trang.");
  });
  actions.append(cancel, reinforce, save, forget);
  form.append(line, text, actions);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const topic = state.editing.topic.trim();
    const summary = state.editing.summary.trim();
    if (topic && summary) {
      void mutateMemory("/api/memory/update", {
        session_id: state.editing.sessionId, topic, summary,
      }, "Đã cập nhật trang.");
    }
  });
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing) {
      event.stopPropagation();
      cancelEdit(form);
    }
  });
  return form;
}

function firstVisible(nodes) {
  return nodes.find((node) => node.offsetParent !== null) ?? null;
}

function startEdit(memory, origin) {
  if (state.pending) return;
  const section = origin?.closest(".memory-view") ?? null;
  state.editing = { id: memory.id, sessionId: memory.sessionId, topic: memory.title, summary: memory.description };
  render();
  const titles = [...(section || document).querySelectorAll(".memory-edit-title")];
  (firstVisible(titles) ?? titles[0])?.focus();
}

function cancelEdit(origin) {
  if (state.pending) return;
  const section = origin?.closest(".memory-view") ?? null;
  const id = state.editing?.id;
  state.editing = null;
  render();
  if (!id) return;
  const actions = [...(section || document).querySelectorAll(".row-action")]
    .filter((node) => node.closest(".memory-card")?.dataset.memoryId === String(id));
  (firstVisible(actions) ?? actions[0])?.focus();
}

const SVG_NS = "http://www.w3.org/2000/svg";
let overviewSvg = null;
const chartObserver = typeof ResizeObserver === "function"
  ? new ResizeObserver(() => drawOverviewChart())
  : null;

function drawOverviewChart() {
  if (!overviewSvg) return;
  const counts = new Map();
  for (const leaf of state.stats.leaves) {
    const day = String(leaf?.timeline_day || "");
    if (/^\d{4}-\d{2}-\d{2}$/.test(day)) counts.set(day, (counts.get(day) || 0) + 1);
  }
  const rows = completeDays([...counts].map(([day, value]) => ({ day, value })), rollingRange(30))
    .map(({ day, value }) => ({ day, value: value || 0 }));
  drawBarChart(overviewSvg, rows.map((row) => ({
    day: row.day,
    segments: [{ class: "memory-bar", value: row.value }],
  })), {
    valueLabels: true,
    ariaLabel: `Biểu đồ mẩu nhật ký theo ngày; mức cao nhất ${formatInteger(Math.max(...rows.map((row) => row.value), 0))}.`,
  });
}

function overviewChartCard() {
  const article = document.createElement("article");
  article.className = "screen-card memory-chart-card";
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("role", "img");
  article.append(svg);
  overviewSvg = svg;
  chartObserver?.disconnect();
  chartObserver?.observe(svg);
  drawOverviewChart();
  return article;
}

function overviewStats() {
  const leaves = state.stats.leaves;
  const values = [
    ["Mẩu nhật ký", state.stats.total ?? leaves.length],
    ["Lần dùng", leaves.reduce((sum, leaf) => sum + Math.max(0, Number(leaf.get_count) || 0), 0)],
    ["Lần tìm", leaves.reduce((sum, leaf) => sum + Math.max(0, Number(leaf.search_count) || 0), 0)],
  ];
  const wrap = document.createElement("div");
  wrap.className = "memory-overview";
  for (const [title, count] of values) {
    const article = document.createElement("article");
    article.className = "screen-card";
    const heading = document.createElement("h3");
    heading.textContent = title;
    const value = document.createElement("strong");
    value.textContent = formatInteger(count);
    article.append(heading, value);
    wrap.append(article);
  }
  return wrap;
}

function renderRows(view) {
  const rows = selectMemories(state.stats.leaves, { view, query: el.search.value });
  return rows.map((memory) => memoryCard(memory));
}

function render() {
  if (!el.list) return;
  placeControls();
  for (const section of el.viewSections) {
    const body = section.querySelector(".memory-view-body");
    if (section.dataset.view === "overview") {
      if (state.statsError) {
        chartObserver?.disconnect();
        overviewSvg = null;
        body.replaceChildren();
      } else body.replaceChildren(overviewChartCard(), overviewStats());
      continue;
    }
    const nodes = renderRows(section.dataset.view);
    body.replaceChildren(...nodes);
    if (!nodes.length) {
      const note = document.createElement("p");
      note.className = "screen-note memory-section-empty";
      note.textContent = el.search.value
        ? "Không tìm thấy mẩu nhật ký phù hợp."
        : "Chưa có mẩu nhật ký trong mục này.";
      body.append(note);
    }
  }
  if (state.pending) setBusy(true);
  renderScopes();
}

async function loadStats({ quiet = false } = {}) {
  const scopeToken = state.scopeToken;
  const request = ++state.statsRequest;
  state.statsController?.abort();
  const controller = new AbortController();
  state.statsController = controller;
  if (!quiet) setStatus("Đang đọc bộ nhớ…");
  el.workspace.setAttribute("aria-busy", "true");
  const requestedScope = state.scopeId;
  try {
    const stats = await getJson(scopeUrl("/api/memory/stats", requestedScope), { signal: controller.signal });
    if (scopeToken !== state.scopeToken || request !== state.statsRequest) return false;
    if (!memoryScopeMatches(stats, requestedScope)) {
      throw new Error("Phản hồi bộ nhớ không khớp phạm vi đã chọn. Hãy làm mới trang để đồng bộ máy chủ.");
    }
    state.stats = { ...stats, leaves: Array.isArray(stats.leaves) ? stats.leaves : [] };
    state.statsError = false;
    el.empty.hidden = true;
    render();
    setStatus();
    return true;
  } catch (error) {
    if (scopeToken !== state.scopeToken || request !== state.statsRequest) return false;
    state.stats = { leaves: [] };
    state.statsError = true;
    render();
    el.empty.hidden = false;
    el.empty.textContent = messageOf(error, "Không tải được bộ nhớ.");
    setStatus(messageOf(error, "Không tải được bộ nhớ."), "error");
    return false;
  } finally {
    if (scopeToken === state.scopeToken && request === state.statsRequest) {
      el.workspace.setAttribute("aria-busy", "false");
      if (state.statsController === controller) state.statsController = null;
    }
  }
}

async function loadRoster() {
  try {
    const payload = await getJson("/api/bots");
    const roster = Array.isArray(payload) ? payload : payload?.bots;
    state.bots = (Array.isArray(roster) ? roster : [])
      .filter((bot) => bot && typeof bot.id === "string" && bot.id);
    renderScopes();
    return true;
  } catch {
    state.bots = [];
    renderScopes();
    return false;
  }
}

function initialBotScope() {
  const ids = new URLSearchParams(location.search).getAll("bot_id");
  if (!ids.length) return { present: false };
  const id = ids.length === 1 ? ids[0] : "";
  return {
    present: true,
    id,
    valid: Boolean(id && id.length <= 64 && BOT_ID_PATTERN.test(id)),
  };
}

function rejectInitialScope(message) {
  state.scopeInvalid = true;
  state.stats = { leaves: [] };
  state.statsError = true;
  state.scopeToken += 1;
  state.statsRequest += 1;
  state.statsController?.abort();
  el.title.textContent = "Không thể mở nhật ký bot";
  el.description.textContent = "Không thể xác thực phạm vi bot trong liên kết này.";
  el.empty.hidden = false;
  el.empty.textContent = message;
  render();
  setStatus(message, "error");
  el.workspace?.setAttribute("aria-busy", "false");
}

async function mutateMemory(path, body, success) {
  if (state.pending) return;
  const draft = state.editing;
  state.pending = true;
  setBusy(true);
  setStatus("Đang lưu…");
  try {
    await postJson(path, body);
    if (state.editing === draft) state.editing = null;
    const refreshed = await loadStats({ quiet: true });
    if (refreshed) setStatus(success, "success");
  } catch (error) {
    setStatus(messageOf(error, "Không lưu được."), "error");
  } finally {
    state.pending = false;
    setBusy(false);
    renderScopes();
  }
}

function bind() {
  placeControls();
  el.search?.addEventListener("input", render);
  el.globalNav?.querySelector("[data-memory-scope='']")?.addEventListener("click", () => selectScope(null));
  document.addEventListener("thyca:memory-scope", (event) => {
    const detail = event.detail || {};
    detail.accepted = selectScope(detail.botId ?? null, detail.botName || "");
  });
  compact.addEventListener("change", () => {
    placeControls();
    render();
  });
}

async function initialize() {
  bind();
  const initial = initialBotScope();
  if (initial.present) {
    if (!initial.valid) {
      rejectInitialScope("Mã bot trong liên kết không hợp lệ. Chọn Tổng quan hoặc làm mới trang.");
      return;
    }
    const rosterAvailable = await loadRoster();
    const bot = state.bots.find((item) => item.id === initial.id);
    if (!rosterAvailable || !bot) {
      rejectInitialScope("Không thể xác thực bot trong liên kết. Hãy làm mới trang hoặc chọn Tổng quan.");
      return;
    }
    selectScope(bot.id, bot.name);
    return;
  }
  if (!compact.matches) void loadRoster();
  void loadStats();
}

if (el.list) void initialize();
