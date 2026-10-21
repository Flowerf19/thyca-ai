import { getJson } from "../../shared/js/http.js";
import { botMark } from "../../shared/js/bot-avatar.js";
import { tintVarFor } from "../chat/team-data.js";
import { aggregateUsage, completeDays, rollingRange, splitPromptTokens, traceRangeUrl } from "../../shared/js/analytics-data.js";
import { knownTokenTotal, modelTokens, shareLabel, shareRatio } from "./cost-data.js";
import { drawBarChart } from "../../shared/js/bar-chart.js";
import { fetchAllTraces } from "../../shared/js/dashboard-today.js";
import { makeSetStatus, messageOf } from "../../shared/js/status.js";
import { cleanText, formatCompact, formatDate, formatInteger } from "../../shared/js/format.js";

const el = {
  period: document.querySelector("#usage-month"),
  chart: document.querySelector("#usage-chart"),
  summaries: document.querySelector("#usage-summaries"),
  total: document.querySelector("#usage-total"),
  turns: document.querySelector("#usage-turns"),
  range: document.querySelector("#usage-period"),
  status: document.querySelector("#usage-status"),
  models: document.querySelector("#usage-models"),
  bots: document.querySelector("#usage-bots"),
  legend: [...document.querySelectorAll(".usage-legend span")],
  units: [...document.querySelectorAll(".usage-toggle button")],
};
const compact = typeof matchMedia === "function"
  ? matchMedia("(max-width: 56rem)")
  : { matches: false, addEventListener: () => {} };
const leafIcon = '<svg class="usage-leaf" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 18c4.2-7.5 7.5-10.8 12-12-1.2 4.8-4.7 8.1-12 12Z"/><path d="m7 17 6-6"/></svg>';
let unit = "token";
let payload = null;
let usage = null;
let series = [];
let generation = 0; // rapid period changes: only the newest load may render

const setStatus = makeSetStatus(el.status);

function draw() {
  if (!series.length) {
    el.chart.replaceChildren();
    return;
  }
  const values = series.map((day) => unit === "token" ? day.input + day.cache + day.output : day.turns);
  const rows = series.map((day) => ({
    day: day.day,
    segments: unit === "token"
      ? [
          { class: "usage-bar-input", value: day.input },
          { class: "usage-bar-cache", value: day.cache },
          { class: "usage-bar-output", value: day.output },
        ]
      : [{ class: "usage-bar-input", value: day.turns }],
  }));
  drawBarChart(el.chart, rows, {
    ariaLabel: `Biểu đồ ${unit === "token" ? "token" : "lượt"} theo ngày; mức cao nhất ${formatCompact(Math.max(...values, 0))}.`,
  });
}

function summaryCard(title, amount, total, turns) {
  const article = document.createElement("article");
  article.className = "screen-card usage-summary";
  const header = document.createElement("header");
  const heading = document.createElement("h3");
  heading.textContent = title;
  const icon = document.createElement("span");
  icon.innerHTML = leafIcon;
  header.append(heading, icon.firstElementChild);
  const value = document.createElement("strong");
  value.append(document.createTextNode(`${formatCompact(amount)} `));
  const unitText = document.createElement("small");
  unitText.textContent = "token";
  value.append(unitText);
  const footer = document.createElement("footer");
  const share = document.createElement("span");
  share.textContent = total > 0 ? `${(amount / total * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}% tổng sử dụng` : "0% tổng sử dụng";
  const turnCount = document.createElement("span");
  turnCount.textContent = `${formatInteger(turns)} lượt`;
  footer.append(share, turnCount);
  article.append(header, value, footer);
  return article;
}

/* Token by model, aggregated from the same loaded trace snapshot as the
   chart above — no extra request. Sorted by tokens, never by cost. */
function aggregateModelTokens(traces) {
  const byModel = new Map();
  for (const row of Array.isArray(traces) ? traces : []) {
    const name = cleanText(row?.model) || "unknown";
    const current = byModel.get(name)
      || { model: name, requests: 0, prompt_tokens: 0, cached_tokens: 0, completion_tokens: 0 };
    current.requests += Number(row?.requests) || 0;
    current.prompt_tokens += Number(row?.prompt_tokens) || 0;
    current.cached_tokens += Number(row?.cached_tokens) || 0;
    current.completion_tokens += Number(row?.completion_tokens) || 0;
    byModel.set(name, current);
  }
  return [...byModel.values()].sort((a, b) => modelTokens(b) - modelTokens(a));
}

/* Token-by-model chart row: same row markup as Request theo mô hình
   (shared .request-model-* rules in dashboard.css). The token breakdown
   rides below the bar as one muted meta line. */
function modelTokenEntry(model, denominator) {
  const item = document.createElement("li");
  item.className = "request-model-row";
  const tokens = modelTokens(model);
  const head = document.createElement("div");
  head.className = "request-model-head";
  const name = document.createElement("span");
  name.className = "request-model-name";
  name.textContent = model.model || "unknown";
  const amount = document.createElement("span");
  amount.className = "request-model-count";
  amount.textContent = `${formatInteger(tokens)} token`;
  const share = document.createElement("span");
  share.className = "request-model-share";
  share.textContent = shareLabel(tokens, denominator);
  const bar = document.createElement("span");
  bar.className = "request-model-bar";
  bar.setAttribute("aria-hidden", "true");
  const fill = document.createElement("span");
  fill.className = "request-model-fill";
  fill.style.width = shareRatio(tokens, denominator) ?? "0%";
  bar.append(fill);
  head.append(name, amount, share);
  item.append(head, bar);
  const meta = document.createElement("p");
  meta.className = "journal-meta";
  const calls = document.createElement("span");
  calls.textContent = `${formatInteger(model.requests)} lần gọi model`;
  meta.append(calls);
  // prompt_tokens already contains cached_tokens (backend semantics), so the
  // uncached input is split out and cache is reported beside it, never added
  // twice.
  const { input, cache } = splitPromptTokens(model.prompt_tokens, model.cached_tokens);
  const output = model.completion_tokens;
  for (const [label, value] of [["Đầu vào", input], ["Cache", cache], ["Đầu ra", output]]) {
    const token = document.createElement("span");
    token.textContent = `${label} ${formatCompact(value)} token`;
    meta.append(token);
  }
  meta.title = `Đầu vào ${formatInteger(input)} token · Cache ${formatInteger(cache)} token · Đầu ra ${formatInteger(output)} token`;
  item.append(meta);
  return item;
}

function renderModels() {
  const rows = aggregateModelTokens(payload?.traces);
  const list = document.createElement("ul");
  list.className = "request-model-chart";
  list.replaceChildren(...rows.map((model) => modelTokenEntry(model, knownTokenTotal(rows))));
  el.models.replaceChildren(list);
  renderBots();
}

/* Token per bot from the same loaded trace snapshot — no extra request.
   Each row prefers its bot_breakdown (per-bot requests/tokens); rows
   without a breakdown fall back to bot_id + the row's own numbers, like
   aggregateBotCost. Turns count participations (one breakdown entry =
   one participating turn). Sorted tokens desc, ties by name/id. */
let botRoster = null;

async function loadBotRoster() {
  if (botRoster) return botRoster;
  botRoster = new Map();
  try {
    const payload = await getJson("/api/bots");
    const list = Array.isArray(payload) ? payload : payload?.bots;
    for (const bot of Array.isArray(list) ? list : []) {
      if (bot && typeof bot.id === "string" && bot.id) botRoster.set(bot.id, bot);
    }
  } catch {
    // Registry unavailable: ids render as-is.
  }
  return botRoster;
}

function isUnattributedBot(botId, roster) {
  return !botId || (botId === "unknown" && !roster?.has(botId));
}

function botTokenName(botId, roster) {
  if (isUnattributedBot(botId, roster)) return "chưa gắn bot";
  const bot = roster?.get(botId);
  const name = bot && typeof bot.name === "string" && bot.name ? bot.name : "";
  return name || botId;
}

function aggregateBotTokens(traces) {
  const byBot = new Map();
  for (const row of Array.isArray(traces) ? traces : []) {
    const breakdown = Array.isArray(row?.bot_breakdown)
      ? row.bot_breakdown.filter((entry) => entry && typeof entry === "object")
      : [];
    const parts = breakdown.length
      ? breakdown.map((entry) => ({
          botId: typeof entry.bot_id === "string" ? entry.bot_id : "",
          requests: entry.requests,
          prompt_tokens: entry.prompt_tokens,
          cached_tokens: entry.cached_tokens,
          completion_tokens: entry.completion_tokens,
        }))
      : [{
          botId: typeof row?.bot_id === "string" ? row.bot_id : "",
          requests: row?.requests,
          prompt_tokens: row?.prompt_tokens,
          cached_tokens: row?.cached_tokens,
          completion_tokens: row?.completion_tokens,
        }];
    for (const part of parts) {
      const key = part.botId;
      const current = byBot.get(key)
        || { bot_id: key, turns: 0, requests: 0, prompt_tokens: 0, cached_tokens: 0, completion_tokens: 0 };
      current.turns += 1;
      current.requests += Number(part.requests) || 0;
      current.prompt_tokens += Number(part.prompt_tokens) || 0;
      current.cached_tokens += Number(part.cached_tokens) || 0;
      current.completion_tokens += Number(part.completion_tokens) || 0;
      byBot.set(key, current);
    }
  }
  return [...byBot.values()].sort((a, b) => modelTokens(b) - modelTokens(a)
    || String(a.bot_id || "").localeCompare(String(b.bot_id || ""), "vi"));
}

export { aggregateBotTokens, botTokenEntry };

function botTokenEntry(bot, denominator, roster) {
  const botId = bot.bot_id || "";
  const unattributed = isUnattributedBot(botId, roster);
  const tokens = modelTokens(bot);
  const item = document.createElement("li");
  item.className = "request-model-row";
  const head = document.createElement("div");
  head.className = "request-model-head";
  const avatar = document.createElement("span");
  avatar.className = "bot-avatar-slot request-bot-avatar";
  avatar.setAttribute("aria-hidden", "true");
  if (!unattributed) {
    avatar.dataset.botId = botId;
    avatar.style.setProperty("--team-tint", tintVarFor(botId));
    const card = roster?.get(botId);
    avatar.innerHTML = botMark(card || { id: botId }, { state: "idle" });
  }
  const name = document.createElement("span");
  name.className = "request-model-name";
  name.dataset.botId = unattributed ? "" : botId;
  name.textContent = botTokenName(botId, roster);
  const amount = document.createElement("span");
  amount.className = "request-model-count";
  amount.textContent = `${formatInteger(tokens)} token`;
  const share = document.createElement("span");
  share.className = "request-model-share";
  share.textContent = shareLabel(tokens, denominator);
  const bar = document.createElement("span");
  bar.className = "request-model-bar";
  bar.setAttribute("aria-hidden", "true");
  const fill = document.createElement("span");
  fill.className = "request-model-fill";
  fill.style.width = shareRatio(tokens, denominator) ?? "0%";
  bar.append(fill);
  head.append(avatar, name, amount, share);
  item.append(head, bar);
  const meta = document.createElement("p");
  meta.className = "journal-meta";
  const calls = document.createElement("span");
  calls.textContent = `${formatInteger(bot.requests)} lần gọi model`;
  meta.append(calls);
  const { input, cache } = splitPromptTokens(bot.prompt_tokens, bot.cached_tokens);
  const output = bot.completion_tokens;
  for (const [label, value] of [["Đầu vào", input], ["Cache", cache], ["Đầu ra", output]]) {
    const token = document.createElement("span");
    token.textContent = `${label} ${formatCompact(value)} token`;
    meta.append(token);
  }
  meta.title = `Đầu vào ${formatInteger(input)} token · Cache ${formatInteger(cache)} token · Đầu ra ${formatInteger(output)} token`;
  item.append(meta);
  return item;
}

function renderBots() {
  if (!el.bots) return;
  const paint = (roster) => {
    const rows = aggregateBotTokens(payload?.traces).filter((bot) => modelTokens(bot) > 0);
    const list = document.createElement("ul");
    list.className = "request-model-chart";
    list.replaceChildren(...rows.map((bot) => botTokenEntry(bot, knownTokenTotal(rows), roster)));
    el.bots.replaceChildren(list);
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "screen-note";
      empty.textContent = "Chưa có token nào trong khoảng này.";
      el.bots.append(empty);
    }
  };
  paint(botRoster);
  void loadBotRoster().then((roster) => {
    if (!el.bots?.isConnected) return;
    paint(roster);
  });
}

function setStrong(root, value, unitText) {
  const small = document.createElement("small");
  small.textContent = unitText;
  root.replaceChildren(document.createTextNode(`${value} `), small);
}

function render() {
  if (!usage) return;
  const totals = usage.totals;
  setStrong(el.total, formatCompact(totals.total), "token");
  setStrong(el.turns, formatInteger(totals.turns), "lượt");
  const range = rollingRange(Number(el.period.value) || 30);
  el.range.textContent = `${formatDate(range.from)} – ${formatDate(range.to)}`;
  el.summaries.replaceChildren(
    summaryCard("Tổng Input", totals.input, totals.total, totals.turns),
    summaryCard("Tổng Cache read", totals.cache, totals.total, totals.turns),
    summaryCard("Tổng Output", totals.output, totals.total, totals.turns),
  );
  el.legend.forEach((item, index) => {
    item.hidden = unit === "turn" && index > 0;
    if (index === 0) item.textContent = unit === "turn" ? "Lượt" : "Input";
  });
  renderModels();
  draw();
}

async function loadAllTraces(days) {
  const traces = await fetchAllTraces(
    (offset) => getJson(`${traceRangeUrl("/api/traces", days, 200)}&offset=${offset}`),
  );
  return { traces, total: traces.length };
}

async function load() {
  const current = ++generation;
  const days = Number(el.period.value) || 30;
  setStatus("Đang tổng hợp token từ trace…");
  try {
    const fresh = await loadAllTraces(days);
    if (current !== generation) return; // a newer period load superseded this one
    payload = fresh;
    usage = aggregateUsage(payload.traces);
    series = completeDays(usage.days, rollingRange(days));
    render();
    setStatus();
  } catch (error) {
    if (current !== generation) return;
    payload = null;
    usage = null;
    series = [];
    el.chart.replaceChildren();
    el.summaries.replaceChildren();
    el.models.replaceChildren();
    setStatus(messageOf(error, "Không tải được số liệu token."), "error");
  }
}

el.units.forEach((button) => {
  button.addEventListener("click", () => {
    unit = button.dataset.unit || "token";
    el.units.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    render();
  });
});
el.period?.addEventListener("change", () => void load());
compact.addEventListener("change", draw);
if (el.chart) {
  new ResizeObserver(() => {
    if (series.length) draw();
  }).observe(el.chart);
}
if (el.total) void load();
