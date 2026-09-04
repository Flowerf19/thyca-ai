import { el } from "./dom.js";
import { modes } from "./data.js";
import { formatMarkdown } from "./markdown.js";
import { escapeHtml, formatUpdated, getJson, postJson } from "./util.js";
import { createNdjsonDecoder } from "./ndjson.js";
import { scoreFromEvents } from "./staff-map.js";
import { statusTextForEvent } from "./turn-status.js";
import { clearStaffs, dropStaff, mountStaff } from "./staff.js";
import { state } from "./state.js";

// Per-session in-flight turn. Survives renderPage / fillChatAt innerHTML
// replacement: restoreLiveTurn remounts .entry-status from this Map.
const liveTurns = new Map();
const STATUS_MIN_DWELL_MS = 500;
let pendingStatus = null;
let lastStatusSwapAt = 0;

const EMPTY_BODY =
  '<div class="new-page-empty"><span aria-hidden="true">+</span><p>Chưa có tin nào.</p><small>Nói điều đầu tiên để mở phiên.</small></div>';
const LOAD_ERROR_BODY =
  '<div class="new-page-empty"><span aria-hidden="true">!</span><p>Không tải được phiên này.</p><small>Kiểm tra mạng rồi bấm lại tab.</small></div>';

// Guard chống race khi bấm tab liên tiếp: lượt fetch cũ về sau phải bỏ,
// không được render đè lên tab mới.
let chatFillGen = 0;
// Guard generation cho hydrateChat: renderMode(chat) bấm liên tiếp không
// để lượt hydrate cũ đè activeSessionId của lượt mới.
let hydrateChatGen = 0;

function liveKey(sessionId) {
  return String(sessionId || "");
}

function staffOpts(sessionId) {
  return { sessionId: liveKey(sessionId), index: "live" };
}

function makeLive(text) {
  return {
    events: [],
    score: scoreFromEvents([]),
    statusText: "Đang chờ Thyca…",
    running: true,
    dirty: false,
    failed: false,
    waiting: false,
    text: text || "",
    activeOps: new Map(),
    batchNames: [],
    lastOperationalText: "",
  };
}

function startLiveTurn(sessionId, text) {
  const rec = makeLive(text);
  liveTurns.set(liveKey(sessionId), rec);
  return rec;
}

function ensureLive(sessionId) {
  const key = liveKey(sessionId);
  let rec = liveTurns.get(key);
  if (!rec) {
    rec = makeLive("");
    liveTurns.set(key, rec);
  }
  return rec;
}

export function getLiveTurn(sessionId) {
  if (sessionId == null) return null;
  return liveTurns.get(liveKey(sessionId)) || null;
}

export function isViewingSession(sessionId) {
  if (state.activeMode !== "chat") return false;
  const page = modes.chat.pages[state.activePageIndex];
  if (!page) return false;
  if (!sessionId) return !page.sessionId;
  if (!page.sessionId) return true;
  return String(page.sessionId) === String(sessionId);
}

function rekeyLiveTurn(fromId, toId) {
  const from = liveKey(fromId);
  const to = liveKey(toId);
  if (from === to) return;
  const rec = liveTurns.get(from);
  if (rec) {
    liveTurns.delete(from);
    liveTurns.set(to, rec);
  }
  dropStaff(`session:${from}:live`);
}

function ensureEntryList(root) {
  let list = root.querySelector(".entry-list");
  if (list) return list;
  root.innerHTML = '<div class="entry-list"></div>';
  return root.querySelector(".entry-list");
}

function ensureOutgoingUser(list, text) {
  if (!text) return;
  const nodes = [...list.children].filter((node) => !node.classList.contains("entry-status"));
  const last = nodes.at(-1);
  if (last && last.classList.contains("entry-user")) return;
  list.insertAdjacentHTML("beforeend", entryHtml("user", text));
}

export function restoreLiveTurn(root, page) {
  if (!root || !page) return false;
  const rec = getLiveTurn(page.sessionId);
  if (!rec) return false;
  if (rec.running || rec.failed) {
    const list = ensureEntryList(root);
    ensureOutgoingUser(list, rec.text);
    let status = list.querySelector(".entry-status");
    if (!status) {
      list.insertAdjacentHTML("beforeend", statusHtml(rec.statusText));
      status = list.querySelector(".entry-status");
    } else {
      const line = status.querySelector(".status-line");
      if (line) line.textContent = rec.statusText;
    }
    if (!status) return false;
    status.classList.toggle("is-error", rec.failed);
    status.classList.toggle("is-waiting", rec.waiting);
    mountStaff(status, rec.score, staffOpts(page.sessionId));
    return true;
  }
  const list = root.querySelector(".entry-list");
  if (!list || !rec.score) return false;
  const born = [...list.querySelectorAll(".entry-thyca")].filter(
    (node) => !node.classList.contains("entry-status"),
  ).at(-1);
  if (born) mountStaff(born, rec.score, staffOpts(page.sessionId));
  rec.dirty = false;
  return Boolean(born);
}

export function discardRunningLiveTurns() {
  for (const [key, rec] of [...liveTurns.entries()]) {
    if (!rec.running) continue;
    liveTurns.delete(key);
    dropStaff(`session:${key}:live`);
  }
}

export async function hydrateChat() {
  const gen = ++hydrateChatGen;
  const pages = await refreshChatList();
  if (gen !== hydrateChatGen) return false;
  if (!pages) return false;
  state.chatLive = true;
  // Reload page: state mất trắng (activeSessionId=null) → khôi phục tab
  // đang xem từ sessionStorage, không mặc định về mới nhất.
  if (!state.activeSessionId) {
    try {
      state.activeSessionId = window.sessionStorage.getItem("thyca.activeSessionId") || null;
    } catch { /* storage bị chặn, giữ null */ }
  }
  let index = pages.findIndex((page) => page.sessionId && page.sessionId === state.activeSessionId);
  if (index < 0) index = 0;
  state.activePageIndex = index;
  await fillChatPage(pages[index]);
  if (gen !== hydrateChatGen) return false;
  return true;
}

export function invalidateChatHydrate() {
  hydrateChatGen++;
  chatFillGen++;
}

export async function createChatSession() {
  state.activeSessionId = null;
  if (state.chatLive) {
    await refreshChatList();
  }
  resetToNewChatPage();
}

// đưa trang "phiên mới" lên đầu — dùng khi bấm vào mode Chat
export function resetToNewChatPage() {
  const rest = (modes.chat.pages || []).filter((page) => page.sessionId);
  modes.chat.pages = [emptyPage(""), ...rest];
  state.activePageIndex = 0;
  state.activeSessionId = null;
}

export function beginOutgoingTurn(text) {
  removeStatus();
  clearStaffs(el.pageBody);
  flushPendingStatus();
  lastStatusSwapAt = 0;
  const page = modes.chat.pages[state.activePageIndex];
  const sessionId = (page && page.sessionId) || state.activeSessionId || "";
  const rec = startLiveTurn(sessionId, text);
  let list = el.pageBody.querySelector(".entry-list");
  if (!list) {
    el.pageBody.innerHTML = '<div class="entry-list"></div>';
    list = el.pageBody.querySelector(".entry-list");
  }
  list.insertAdjacentHTML("beforeend", entryHtml("user", text));
  list.lastElementChild.classList.add("is-enter");
  list.insertAdjacentHTML("beforeend", statusHtml(rec.statusText));
  mountStaff(list.lastElementChild, rec.score, staffOpts(sessionId));
  scrollThread();
}

export function removeStatus() {
  const node = el.pageBody.querySelector(".entry-status");
  if (!node) return;
  clearStaffs(node);
  node.remove();
}

export function settleIncoming() {
  const page = modes.chat.pages[state.activePageIndex];
  const sessionId = page?.sessionId || "";
  const rec = getLiveTurn(sessionId);
  const liveList = el.pageBody.querySelector(".entry-list");
  const wrap = document.createElement("div");
  wrap.innerHTML = page?.body || "";
  const fresh = wrap.querySelector(".entry-list");
  if (!fresh || !liveList) return false;
  clearStaffs(el.pageBody);
  const kept = [...liveList.children].filter((node) => !node.classList.contains("entry-status")).length;
  liveList.replaceWith(fresh);
  [...fresh.children].slice(kept).forEach((node, index) => {
    node.classList.add("is-enter");
    node.style.animationDelay = `${index * 80}ms`;
  });
  const born = [...fresh.children].slice(kept).find((node) => node.classList.contains("entry-thyca"));
  if (born && rec?.score) mountStaff(born, rec.score, staffOpts(sessionId));
  if (rec) {
    rec.running = false;
    rec.dirty = false;
    rec.failed = false;
  }
  const heading = el.pageHeader.querySelector("h1");
  if (heading && page.title) heading.innerHTML = page.title;
  const kicker = el.pageHeader.querySelector(".page-kicker");
  if (kicker && page.kicker) kicker.textContent = page.kicker;
  scrollThread();
  return true;
}

export async function sendChatTurn(text) {
  const page = modes.chat.pages[state.activePageIndex];
  let sessionId = page ? page.sessionId : state.activeSessionId;
  const pendingId = sessionId || "";
  if (!sessionId) {
    const created = await postJson("/api/sessions", {});
    if (!created || !created.id) throw new Error("Không tạo được phiên.");
    sessionId = created.id;
    if (page) {
      page.sessionId = sessionId;
      bindSession(page, sessionId);
    }
    rekeyLiveTurn(pendingId, sessionId);
    const status = chatStatusNode(sessionId);
    const rec = getLiveTurn(sessionId);
    if (status && rec) mountStaff(status, rec.score, staffOpts(sessionId));
  }
  const response = await fetch(`/api/sessions/${sessionId}/turn/stream`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const message = payload && payload.error ? String(payload.error) : "Không gửi được.";
    throw new Error(message);
  }
  if (!response.body) throw new Error("Không nhận được trả lời.");
  const decoder = createNdjsonDecoder();
  const reader = response.body.getReader();
  let completed = null;
  let failed = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const event of decoder.push(value)) {
      const terminal = ingestTurnEvent(sessionId, event);
      if (terminal === "completed") completed = event.detail;
      else if (terminal === "failed") failed = event;
    }
  }
  for (const event of decoder.flush()) {
    const terminal = ingestTurnEvent(sessionId, event);
    if (terminal === "completed") completed = event.detail;
    else if (terminal === "failed") failed = event;
  }
  if (failed) {
    const message = failed.message ? String(failed.message) : "Lượt đã dừng.";
    throw new Error(message);
  }
  if (!completed) throw new Error("Không nhận được trả lời.");
  applyDetail(completed);
  return completed;
}

function ingestTurnEvent(sessionId, event) {
  const rec = ensureLive(sessionId);
  rec.events.push(event);
  rec.score = scoreFromEvents(rec.events);
  rec.waiting = event.type === "llm.started";
  if (event.type === "turn.failed") rec.failed = true;
  if (event.type === "turn.completed" || event.type === "turn.failed") {
    rec.running = false;
    rec.dirty = true;
  }
  applyStatus(sessionId, event);
  const status = chatStatusNode(sessionId);
  if (status) {
    mountStaff(status, rec.score, staffOpts(sessionId));
    if (rec.failed) status.classList.add("is-error");
    // While the model thinks (llm.started → next event) the newest note
    // breathes — see .is-waiting in workspace.css.
    status.classList.toggle("is-waiting", rec.waiting);
  }
  if (event.type === "turn.completed") return "completed";
  if (event.type === "turn.failed") return "failed";
  return null;
}

function chatStatusNode(sessionId) {
  if (state.activeMode !== "chat") return null;
  if (sessionId && !isViewingSession(sessionId)) return null;
  const status = el.pageBody.querySelector(".entry-status");
  return status && status.isConnected ? status : null;
}

function applyStatus(sessionId, event) {
  const rec = ensureLive(sessionId);
  let text = statusTextForEvent(event);
  if (text === null) return;
  const opKind = event.type.startsWith("tool.") ? "tool"
    : event.type.startsWith("skill.") ? "skill" : null;
  if (opKind) {
    const id = event.call_id || "call";
    if (event.type.endsWith(".started")) {
      const name = event.name || (opKind === "skill" ? "skill" : "tool");
      rec.activeOps.set(id, { kind: opKind, name });
      rec.batchNames.push(name);
    } else {
      rec.activeOps.delete(id);
    }
    // Aggregate every in-flight op into one line; when the batch drains,
    // summarize what ran so the result text matches what was announced.
    const skills = [];
    const tools = [];
    for (const op of rec.activeOps.values()) (op.kind === "skill" ? skills : tools).push(op.name);
    const chunks = [];
    if (skills.length) chunks.push(`Đang mở skill ${skills.join(", ")}…`);
    if (tools.length) chunks.push(`Đang dùng ${tools.join(", ")}…`);
    text = chunks.length
      ? chunks.join(" · ")
      : `${rec.batchNames.join(", ")} đã xong…`;
    if (!rec.activeOps.size) rec.batchNames = [];
    rec.lastOperationalText = text;
  } else if (event.type === "llm.started") {
    // Linger the operational line through the llm wait — the round is still
    // visible on the staff as that wait's note pair.
    if (rec.lastOperationalText) return;
  } else if (event.type === "llm.finished" || event.type === "turn.accepted") {
    rec.lastOperationalText = "";
  }
  rec.statusText = text;
  const status = chatStatusNode(sessionId);
  const ticker = status && status.querySelector(".status-ticker");
  if (!ticker || !ticker.isConnected) {
    flushPendingStatus();
    return;
  }
  if (reduceMotion()) {
    flushPendingStatus();
    let line = ticker.querySelector(".status-line");
    if (!line) {
      line = document.createElement("span");
      line.className = "status-line";
      ticker.append(line);
    }
    line.textContent = text;
    return;
  }
  // Minimum dwell: parallel tool calls emit status lines tens of ms apart —
  // too fast to read. Hold each line ≥ STATUS_MIN_DWELL, coalescing to the
  // newest queued text. Terminals bypass so the outcome is never delayed.
  const terminal = event.type === "turn.completed" || event.type === "turn.failed";
  if (terminal) {
    flushPendingStatus();
    showStatusLine(ticker, text);
    return;
  }
  const wait = STATUS_MIN_DWELL_MS - (performance.now() - lastStatusSwapAt);
  if (wait <= 0) {
    flushPendingStatus();
    showStatusLine(ticker, text);
    return;
  }
  if (pendingStatus) {
    pendingStatus.text = text; // coalesce: newest wins
    return;
  }
  const tickerRef = ticker;
  pendingStatus = {
    text,
    timer: window.setTimeout(() => {
      const pending = pendingStatus;
      pendingStatus = null;
      if (pending && tickerRef.isConnected) showStatusLine(tickerRef, pending.text);
    }, wait),
  };
}

function showStatusLine(ticker, text) {
  lastStatusSwapAt = performance.now();
  slideStatus(ticker, text);
}

function flushPendingStatus() {
  if (pendingStatus) {
    window.clearTimeout(pendingStatus.timer);
    pendingStatus = null;
  }
}

export async function fillChatAt(index) {
  const gen = ++chatFillGen;
  const page = modes.chat.pages[index];
  if (!page) return false;
  const ok = await fillChatPage(page);
  // Fetch cũ về trễ: bỏ, giữ tab mới.
  if (gen !== chatFillGen) return false;
  state.activePageIndex = index;
  return ok;
}

async function fillChatPage(page) {
  if (!page) return false;
  if (!page.sessionId) {
    bindSession(page, null);
    page.body = EMPTY_BODY;
    return true;
  }
  let detail = null;
  try {
    // Timeout 15s như postJson: session kẹt không treo tab,
    // fail thì rơi vào nhánh LOAD_ERROR_BODY bên dưới.
    detail = await getJson(`/api/sessions/${page.sessionId}`);
  } catch {
    detail = null;
  }
  if (!detail || !Array.isArray(detail.messages)) {
    // Lỗi visible thay vì trang trắng: giữ body cũ nếu có, báo rõ.
    bindSession(page, page.sessionId);
    if (!page.body) page.body = LOAD_ERROR_BODY;
    page.loadError = true;
    return false;
  }
  page.loadError = false;
  applyDetailToPage(page, detail);
  return true;
}

function applyDetail(detail) {
  if (!detail || !detail.id) throw new Error("Không nhận được trả lời.");
  let pages = modes.chat.pages;
  const viewingId =
    state.activeMode === "chat" ? String(pages[state.activePageIndex]?.sessionId || "") : null;
  let index = pages.findIndex((page) => page.sessionId === detail.id);
  if (index < 0) {
    pages = [pageFromDetail(detail), ...pages.filter((page) => page.sessionId)];
    modes.chat.pages = pages;
    index = 0;
  } else {
    pages[index] = pageFromDetail(detail);
  }
  const count = document.querySelector('[data-mode="chat"] .mode-count');
  if (count) count.textContent = String(pages.filter((page) => page.sessionId).length);
  state.activeSessionId = detail.id;
  if (state.activeMode === "chat" && (viewingId === "" || viewingId === detail.id)) {
    state.activePageIndex = index;
  }
}

function applyDetailToPage(page, detail) {
  const next = pageFromDetail(detail);
  page.title = next.title;
  page.date = next.date;
  page.tag = next.tag;
  page.kicker = next.kicker;
  page.body = next.body;
  page.sessionId = next.sessionId;
  bindSession(page, next.sessionId);
}

function bindSession(page, sessionId) {
  if (modes.chat.pages[state.activePageIndex] !== page) return;
  state.activeSessionId = sessionId;
  // Giữ tab đang xem qua reload page (sessionStorage theo tab).
  try {
    if (sessionId) window.sessionStorage.setItem("thyca.activeSessionId", sessionId);
    else window.sessionStorage.removeItem("thyca.activeSessionId");
  } catch { /* storage bị chặn, bỏ qua */ }
}

async function refreshChatList() {
  const payload = await getJson("/api/sessions");
  if (!payload || !Array.isArray(payload.sessions)) return null;
  const pages = pagesFromSessions(payload);
  if (payload.model) state.lastChatModel = payload.model;
  try {
    const cfg = await getJson("/api/config");
    if (cfg?.values?.provider?.baseUrl) state.lastChatBaseUrl = cfg.values.provider.baseUrl;
  } catch { /* giữ baseUrl cũ, không chặn chat */ }
  modes.chat = {
    label: "Chat",
    listLabel: "Phiên gần đây",
    kicker: payload.model ? `~/.thyca · ${payload.model}` : modes.chat.kicker,
    note: modes.chat.note,
    chips: modes.chat.chips,
    pages,
  };
  const count = document.querySelector('[data-mode="chat"] .mode-count');
  if (count) count.textContent = String(payload.sessions.length);
  return pages;
}

export async function refreshChatKicker() {
  // Re-check provider sau khi settings lưu (TASK-040): mở khóa composer
  // nếu trước đó bị boot gate chặn, refresh kicker model mới.
  try {
    const status = await getJson("/api/config/status");
    if (status && status.ready !== false && !state.chatLive) {
      state.chatLive = true;
      if (el.line) el.line.disabled = false;
      if (el.send) el.send.disabled = false;
      if (el.hint && el.hint.textContent.includes("Cần cấu hình")) {
        el.hint.textContent = "";
        el.hint.className = "hint";
      }
    }
  } catch { /* giữ trạng thái cũ */ }
  const pages = await refreshChatList();
  if (!pages) return;
  if (state.activeMode === "chat" && el.modeBreadcrumb && !el.modeBreadcrumb.classList.contains("crumb-mark")) {
    el.modeBreadcrumb.textContent = modes.chat.kicker;
  }
}

export function pagesFromSessions(payload) {
  const model = payload.model || "";
  const sessions = payload.sessions || [];
  if (!sessions.length) return [emptyPage(model)];
  return sessions.map((item) => pageFromSummary(item, model));
}

function pageFromSummary(item, model) {
  const id = String(item.id || "");
  return {
    title: escapeHtml(item.title || "Phiên trống"),
    date: escapeHtml(formatUpdated(item.updated_at)),
    tag: "",
    tone: "chat",
    sessionId: id,
    kicker: escapeHtml(id && model ? `${id} · ${model}` : id || model),
    body: "",
  };
}

function pageFromDetail(detail) {
  const id = String(detail.id || "");
  const model = detail.model || "";
  return {
    title: escapeHtml(detail.title || "Phiên trống"),
    date: escapeHtml(formatUpdated((detail.messages || []).at(-1)?.ts)),
    tag: "",
    tone: "chat",
    sessionId: id,
    kicker: escapeHtml(id && model ? `${id} · ${model}` : id || model),
    body: threadHtml(detail.messages || []),
  };
}

function emptyPage(model) {
  return {
    title: "Phiên trống",
    date: "chưa lưu",
    tag: "mới",
    tone: "chat",
    sessionId: "",
    kicker: model ? `${model} · phiên mới` : "Phiên mới · chưa lưu",
    body: EMPTY_BODY,
  };
}

export function threadHtml(messages) {
  const parts = [];
  const pending = [];
  const flushTools = () => {
    const names = pending.map((name) => String(name || "").trim()).filter(Boolean);
    pending.length = 0;
    if (!names.length) return;
    const counts = new Map();
    for (const name of names) {
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    const items = [];
    for (const [name, count] of counts) {
      items.push(count > 1 ? `${name} ×${count}` : name);
    }
    parts.push(
      `<div class="tool-strip"><span class="tool-kicker">Tools used:</span> ${escapeHtml(items.join(", "))}</div>`,
    );
  };
  for (const message of messages) {
    if (!message || message.role === "system" || message.role === "tool") continue;
    if (message.role === "assistant" && message.tool_calls?.length) {
      for (const call of message.tool_calls) {
        pending.push(call.name || "");
      }
      if (!message.content) continue;
    }
    // meta-only messages (kind: "naming") carry no chat content — never a bubble
    if (message.role === "assistant" && !message.content && !message.tool_calls?.length) continue;
    flushTools();
    if (message.role === "user" || message.role === "assistant") {
      parts.push(entryHtml(message.role, message.content || ""));
    }
  }
  flushTools();
  if (!parts.length) return EMPTY_BODY;
  return `<div class="entry-list">${parts.join("")}</div>`;
}

function statusHtml(text = "Đang chờ Thyca…") {
  return `<article class="entry entry-thyca entry-status" aria-label="Thyca đang nghĩ" aria-live="off">
      <div class="entry-thyca-head"><time>thyca</time><span class="status-ticker"><span class="status-line">${escapeHtml(text)}</span></span></div>
    </article>`;
}

function slideStatus(ticker, next) {
  const outgoing = ticker.querySelector(".status-line:not(.is-out)");
  const incoming = document.createElement("span");
  incoming.className = "status-line is-in";
  incoming.textContent = next;
  ticker.append(incoming);
  if (outgoing) outgoing.classList.add("is-out");
  window.setTimeout(() => {
    if (outgoing) outgoing.remove();
    incoming.classList.remove("is-in");
  }, 200);
}

export function scrollThread() {
  if (!el.notebook) return;
  // Đợi layout ổn định (font/ảnh/markdown/staff SVG) rồi mới scroll,
  // nếu không scrollHeight đo sớm sẽ hụt. Rọi lại 1 nhịp sau 300ms
  // cho session nhiều staff hoặc font web chưa về.
  const doScroll = () => {
    if (!el.notebook || !el.notebook.isConnected) return;
    el.notebook.scrollTo({
      top: el.notebook.scrollHeight,
      behavior: reduceMotion() ? "auto" : "smooth",
    });
    updateToBottomVisibility();
  };
  const settle = () => {
    doScroll();
    window.setTimeout(() => {
      if (!isNearBottom()) doScroll();
    }, 300);
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => requestAnimationFrame(settle));
  } else {
    settle();
  }
}

const TO_BOTTOM_PX = 200;
let toBottomBound = false;

export function isNearBottom(margin = TO_BOTTOM_PX) {
  if (!el.notebook) return true;
  const distance = el.notebook.scrollHeight - el.notebook.scrollTop - el.notebook.clientHeight;
  return distance <= margin;
}

export function updateToBottomVisibility() {
  const button = el.toBottom;
  if (!button || !el.notebook) return;
  const scrollable = el.notebook.scrollHeight > el.notebook.clientHeight + TO_BOTTOM_PX;
  const show = state.activeMode === "chat" && scrollable && !isNearBottom();
  button.hidden = !show;
}

export function initToBottom() {
  if (toBottomBound || !el.notebook || !el.toBottom) return;
  toBottomBound = true;
  el.notebook.addEventListener("scroll", () => updateToBottomVisibility(), { passive: true });
  el.toBottom.addEventListener("click", () => scrollThread());
}

function reduceMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function entryHtml(role, content) {
  const cls = role === "user" ? "entry-user" : "entry-thyca";
  const stamp =
    role === "assistant" ? "<time>thyca</time>" : "";
  return `<article class="entry ${cls}">${stamp}<div class="entry-copy">${formatMarkdown(content)}</div></article>`;
}

