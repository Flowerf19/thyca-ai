// Chat screen: messages, composer, session switching, real NDJSON turn stream.
'use strict';
const fmtTime = ts => { const d = ts ? new Date(ts) : new Date(); return Number.isFinite(d.getTime()) ? d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' }) : ''; };
function userMessage(text, time) {
  const framed = discordConversationHtml(text);
  const body = framed ? framed.html : `<p>${escapeHTML(text).replace(/\n/g, '<br>')}</p>`;
  const source = framed ? `<span class="message-source">${framed.label}</span>` : '';
  return `<article class="message-user">${body}<footer>${source}<time>${time}</time>${icon('check')}</footer></article>`;
}
/* Discord bridge posts (thyca-discord-mcp build_conversation_prompt) wrap
   a compact conversation JSON between UNTRUSTED_CONVERSATION markers after
   trusted/policy/task scaffolding meant for the LLM, not the reader.
   Display layer only: parse the framed JSON into one readable line per
   message (sender name from participants, <@uid> mentions resolved),
   never the scaffolding, never raw JSON, never unescaped HTML. */
function discordConversationHtml(text) {
  const m = String(text || '').match(/UNTRUSTED_CONVERSATION job_ref=(\S+) START\n([\s\S]*?)\nUNTRUSTED_CONVERSATION job_ref=\1 END/);
  if (!m) return null;
  const inner = m[2].trim();
  let frame = null;
  try { frame = JSON.parse(inner); } catch { frame = null; }
  const messages = frame && typeof frame === 'object' && Array.isArray(frame.messages) ? frame.messages : null;
  if (!messages) {
    // Unparseable frame: show the inner text escaped, without markers.
    return { label: 'Từ Discord', html: `<p>${escapeHTML(inner).replace(/\n/g, '<br>')}</p>` };
  }
  const names = {};
  for (const p of Array.isArray(frame.participants) ? frame.participants : []) {
    if (p && p.uid) names[String(p.uid)] = String(p.display_name || p.uid);
  }
  const shortUid = uid => { const s = String(uid); return s.length > 12 ? s.slice(0, 6) + '…' + s.slice(-4) : s; };
  const nameOf = uid => names[String(uid)] || shortUid(uid);
  const textOf = raw => escapeHTML(String(raw ?? '')).replace(/&lt;@([^&<>]+)&gt;/g,
    (hit, uid) => '@' + escapeHTML(nameOf(String(uid).trim()))).replace(/\n/g, '<br>');
  const lines = [];
  for (const item of messages) {
    if (!item || typeof item !== 'object') continue;
    lines.push(`<strong>${escapeHTML(nameOf(item.sender_uid))}</strong> ${textOf(item.text)}`);
  }
  if (!lines.length) return { label: 'Từ Discord', html: `<p>${escapeHTML(inner).replace(/\n/g, '<br>')}</p>` };
  return { label: 'Từ Discord', html: `<p>${lines.join('<br>')}</p>` };
}
function assistantMessage(botId, content, time, toolNames = []) {
  const b = getBot(botId) || { id: botId, name: botId || 'Thyca', palette: derivePalette('#a65a42') };
  const tools = toolNames.length ? `<span class="usage-pill">${icon('leaf')}Đã dùng: ${toolNames.map(escapeHTML).join(', ')}</span>` : '';
  return `<article class="message-assistant" style="${paletteStyle(b)}"><header class="message-byline"><span>${escapeHTML(b.name)}</span><time>${time}</time></header><div class="live-copy markdown-body">${content}</div><footer class="message-footer">${tools}<button class="again" data-retry="${b.id}">${icon('retry')}Thử lại</button><button data-copy aria-label="Sao chép phản hồi">${icon('copy')}</button></footer></article>`;
}
/* Assistant content is markdown — rendered by share/markdown.js (marked GFM,
   sanitized). The escaped-paragraph path stays as a fallback for the brief
   window before the module evaluates. */
const contentHtml = text => window.formatMarkdown
  ? window.formatMarkdown(text)
  : escapeHTML(String(text || '')).split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');

/* Structured cards (ask_user/report_error/consent) ride on assistant rows:
   meta = {kind:'card', card:{type:'question'|'error', ...}}. Room cards
   carry content:"" (or the error summary); the DM consent card carries
   the title as content plus card.private_followup. Unknown card types
   fall through to the plain assistant row. */
function cardLabelOf(option) {
  const label = typeof option === 'string' ? option : String(option?.label || option?.id || '');
  return label.trim();
}
function cardChoices(card, answered, botId, choiceHook) {
  const options = Array.isArray(card.options) ? card.options
    : Array.isArray(card.choices) ? card.choices : [];
  const chips = [];
  for (const option of options) {
    const label = cardLabelOf(option);
    if (!label) continue;
    chips.push(`<button type="button" class="card-choice"${answered ? ' disabled' : ''} data-card-bot="${escapeHTML(botId)}" ${choiceHook(option, label)}>${escapeHTML(label)}</button>`);
  }
  if (!chips.length) return '';
  return `<div class="card-choices">${chips.join('')}</div>`;
}
function renderQuestionCard(card, answered, botId) {
  const title = String(card.title || 'Chọn một cách');
  const sub = typeof card.subtitle === 'string' && card.subtitle.trim()
    ? `<p class="card-sub">${escapeHTML(card.subtitle.trim())}</p>` : '';
  const hook = (option, label) => card.private_followup?.pending_id
    ? `data-pf-choice="${escapeHTML(String(option?.id || label))}" data-pending-id="${escapeHTML(String(card.private_followup.pending_id))}" data-revision="${escapeHTML(String(card.private_followup.revision ?? ''))}"`
    : `data-card-label="${escapeHTML(label)}"`;
  return `<section class="user-card card-question" aria-label="${escapeHTML(title)}"><h3>${escapeHTML(title)}</h3>${sub}${cardChoices(card, answered, botId, hook)}</section>`;
}
function renderErrorCard(card, answered, botId) {
  const steps = [];
  for (const step of Array.isArray(card.progress) ? card.progress : []) {
    const label = typeof step === 'string' ? step.trim() : String(step?.label || '').trim();
    if (!label) continue;
    const state = typeof step === 'object' ? String(step.state || '') : '';
    const mark = state === 'done' ? '\u2713' : state === 'stuck' ? '!' : '\u25cb';
    steps.push(`<li class="card-progress-row is-${escapeHTML(state || 'todo')}"><span class="card-progress-mark" aria-hidden="true">${mark}</span>${escapeHTML(label)}</li>`);
  }
  const hint = typeof card.hint === 'string' && card.hint.trim()
    ? `<p class="card-sub">${escapeHTML(card.hint.trim())}</p>` : '';
  const hook = (option, label) => `data-card-label="${escapeHTML(label)}"`;
  return `<section class="user-card card-error" aria-label="Bot gặp lỗi"><h3>Làm dở tới đâu</h3><ul class="card-progress">${steps.join('')}</ul><h3>Làm tiếp thế nào?</h3>${hint}${cardChoices(card, answered, botId, hook)}</section>`;
}
/* Consent decisions the backend never echoes as a user row: once a
   choice POST succeeds, its card stays disabled via this set, keyed by
   pending_id:revision — independent of later re-renders. No backend
   change: the set lives in the client that owns the chips. */
const decidedConsents = new Set();
function consentKeyOf(card) {
  const pf = card && typeof card === 'object' ? card.private_followup : null;
  if (!pf || typeof pf !== 'object' || !pf.pending_id) return '';
  return `${pf.pending_id}:${String(pf.revision ?? '')}`;
}
function cardArticle(m, detail, time, userAfter) {
  const card = m.meta.card;
  const botId = String(m.meta?.bot_id || detail.dm || '');
  const names = (m.tool_calls || []).map(c => c.skill || c.name).filter(Boolean);
  // Consent decisions are never echoed as a user row by the backend, so
  // a decided card stays disabled via the decided set as well.
  const answered = userAfter || (consentKeyOf(card) && decidedConsents.has(consentKeyOf(card)));
  const body = card.type === 'error' ? renderErrorCard(card, answered, botId) : renderQuestionCard(card, answered, botId);
  const content = typeof m.content === 'string' ? m.content.trim() : '';
  // The DM consent card repeats its title as content — render it once.
  const lead = content && content !== String(card.title || '').trim() ? contentHtml(m.content) : '';
  return assistantMessage(botId, lead + body, time, names);
}
/* Bot<->bot exchange rows in the room transcript: bus rows (bus_event)
   pair via explicit meta.recipient or the in-room result row whose
   parent_span_id is the bus span; result rows pair via the in-room bus
   row. Mirrors thyca/bots/exchanges.py (best effort, display only). */
function exchangePairOf(m, messages) {
  const meta = m && m.meta || {};
  if (m.role === 'assistant' && meta.bus_event === true) {
    const sender = String(meta.bot_id || '');
    let partner = String(meta.recipient || '');
    if (!partner && typeof meta.span_id === 'string' && meta.span_id) {
      const res = messages.find(n => n && n.role === 'assistant' && n.meta
        && n.meta.kind === 'result' && n.meta.parent_span_id === meta.span_id);
      partner = String((res && res.meta.bot_id) || '');
    }
    return sender && partner && sender !== partner ? [sender, partner] : null;
  }
  if (m.role === 'assistant' && meta.kind === 'result') {
    const bot = String(meta.bot_id || '');
    const parent = String(meta.parent_span_id || '');
    const bus = parent && messages.find(n => n && n.role === 'assistant' && n.meta
      && n.meta.bus_event === true && n.meta.span_id === parent);
    const sender = String((bus && bus.meta.bot_id) || '');
    return sender && bot && sender !== bot ? [sender, bot] : null;
  }
  return null;
}
function peerRunKey(pair) { return [...pair].sort().join('\u0000'); }
function peerDisplayName(id) { const b = getBot(id); return (b && b.name) || id; }
function peerBubble(m, time) {
  const names = (m.tool_calls || []).map(c => c.skill || c.name).filter(Boolean);
  return assistantMessage(String(m.meta?.bot_id || ''), contentHtml(m.content), time, names);
}
function peerDetails(run) {
  const [a, b] = run.pair;
  return `<details class="peer-exchange"><summary><span class="peer-exchange-lead">${escapeHTML(peerDisplayName(a))} và ${escapeHTML(peerDisplayName(b))} đã trao đổi ${run.rows.length} tin</span></summary><div class="peer-exchange-rows">${run.rows.join('')}</div></details>`;
}
function renderTranscript(detail) {
  const nodes = [];
  const messages = detail.messages || [];
  // Room: consecutive bot<->bot exchange rows with the same pair
  // collapse into one expandable entry; anything else flushes the
  // open run. DM: exchange rows stay hidden here — the grouped
  // "N messages with <Bot>" entry + thread modal own the DM view.
  const hidePeerRows = !!(detail && detail.dm);
  let peerRun = null;
  const flushPeerRun = () => {
    if (!peerRun) return;
    nodes.push(peerRun.rows.length > 1 ? peerDetails(peerRun) : peerRun.rows[0]);
    peerRun = null;
  };
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (!m || m.role === 'system' || m.role === 'tool') continue;
    const kind = m.meta?.kind;
    if (kind === 'naming' || kind === 'compaction') continue;
    const time = fmtTime(m.ts);
    if (m.role === 'user') { flushPeerRun(); nodes.push(userMessage(m.content || '', time)); continue; }
    if (m.role !== 'assistant') { flushPeerRun(); continue; }
    const card = m.meta?.card;
    if (m.meta?.kind === 'card' && card && typeof card === 'object'
        && (card.type === 'question' || card.type === 'error')) {
      // A later user message is the answer itself; consent cards rely on
      // the decided set instead (the backend never echoes a user row).
      flushPeerRun();
      const userAfter = messages.slice(i + 1).some(next => next && next.role === 'user');
      nodes.push(cardArticle(m, detail, time, userAfter));
      continue;
    }
    const pair = exchangePairOf(m, messages);
    if (pair) {
      if (hidePeerRows) continue;
      const key = peerRunKey(pair);
      if (!peerRun || peerRun.key !== key) { flushPeerRun(); peerRun = { key, pair, rows: [] }; }
      peerRun.rows.push(peerBubble(m, time));
      continue;
    }
    flushPeerRun();
    const names = (m.tool_calls || []).map(c => c.skill || c.name).filter(Boolean);
    nodes.push(assistantMessage(String(m.meta?.bot_id || detail.dm || ''), contentHtml(m.content), time, names));
  }
  flushPeerRun();
  return nodes.join('');
}

/* DM exchange groups: in a bot's DM, one entry per bot it exchanged
   messages with (read from the origin rooms via the exchanges API).
   Entries append at the end of the transcript; tapping one opens the
   A-B thread modal. */
function botFace(id) {
  const b = getBot(id);
  if (b) { try { return avatar(b); } catch { /* fall through to initial */ } }
  const name = (b && b.name) || String(id || '?');
  return `<span class="seal-avatar seal-fallback" aria-hidden="true">${escapeHTML(name.slice(0, 1).toUpperCase())}</span>`;
}
async function renderExchangeGroups(detail) {
  document.querySelectorAll('#message-list [data-exchange-partner]').forEach(n => n.remove());
  const owner = detail && detail.dm;
  const sid = currentSession;
  if (!owner) return;
  let partners = [];
  try {
    const data = await getJson(`/api/bots/${encodeURIComponent(owner)}/exchanges`);
    partners = (data && data.exchanges) || [];
  } catch { return; }
  if (currentSession !== sid || !partners.length) return;
  const list = $('#message-list');
  if (!list) return;
  for (const p of partners) {
    if (!p || !p.partner) continue;
    const name = peerDisplayName(p.partner);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'exchange-group';
    btn.dataset.exchangePartner = p.partner;
    btn.setAttribute('aria-label', `${p.count} messages with ${name}`);
    btn.innerHTML = `${botFace(p.partner)}<span><strong>${escapeHTML(String(p.count))} messages</strong> with ${escapeHTML(name)}</span>`;
    btn.addEventListener('click', () => openExchangeModal(owner, p.partner));
    list.append(btn);
  }
}
async function openExchangeModal(ownerId, partnerId) {
  const owner = getBot(ownerId) || { id: ownerId, name: ownerId };
  const partner = getBot(partnerId) || { id: partnerId, name: partnerId };
  $('#exchange-modal-title').innerHTML =
    `${botFace(owner.id)}<span>${escapeHTML(owner.name)}</span><span class="exchange-vs" aria-hidden="true">\u21c4</span>${botFace(partner.id)}<span>${escapeHTML(partner.name)}</span>`;
  const body = $('#exchange-modal-body');
  const dialog = $('#exchange-modal');
  if (!body || !dialog) return;
  body.innerHTML = '<p class="exchange-loading">Đang tải hội thoại…</p>';
  dialog.showModal();
  let thread = [];
  try {
    const data = await getJson(`/api/bots/${encodeURIComponent(ownerId)}/exchanges/${encodeURIComponent(partnerId)}`);
    thread = (data && data.messages) || [];
  } catch (err) {
    body.innerHTML = `<p class="exchange-error">${escapeHTML(err.message || 'Không tải được.')}</p>`;
    return;
  }
  if (!thread.length) {
    body.innerHTML = '<p class="exchange-empty">Chưa có tin nhắn nào được ghi.</p>';
    return;
  }
  body.innerHTML = thread.map(row => {
    const author = row.author || row.sender;
    const b = getBot(author) || { id: author, name: author };
    const side = author === ownerId ? 'owner' : 'partner';
    return `<div class="exchange-row is-${side}"><header><span>${escapeHTML(b.name || author)}</span><time>${fmtTime(row.ts)}</time></header><div class="exchange-bubble markdown-body">${contentHtml(row.content || '')}</div></div>`;
  }).join('');
}

/* Card answers. Question/error chips send the visible label as a normal
   turn through the composer's send path (same busy guard, no bypass).
   Consent chips POST the option id to this DM's private-followup
   endpoint, then re-render from fresh detail. */
function answerCardLabel(label, button) {
  label = String(label || '').trim();
  if (!label || !currentSession || runs.has(currentSession)) return false;
  if (button) button.disabled = true;
  const botId = String(button?.dataset?.cardBot || '') || selectedBot;
  // sendTurn resolves false when nothing was actually sent (bot offline
  // or missing): reopen the chip instead of stranding it disabled.
  void Promise.resolve(sendTurn(label, botId)).then(
    ok => { if (!ok && button) button.disabled = false; },
    () => { if (button) button.disabled = false; });
  return true;
}
async function decidePrivateFollowup(pendingId, revision, choice, button) {
  const rev = Number(revision);
  const sessionId = currentSession;
  if (!pendingId || !Number.isInteger(rev) || !choice
      || !sessionId || runs.has(sessionId)) return false;
  const key = `${pendingId}:${String(rev)}`;
  const group = button ? [...button.closest('.card-choices')?.querySelectorAll('.card-choice') || []] : [];
  group.forEach(b => { b.disabled = true; });
  try {
    await postJson(`/api/sessions/${encodeURIComponent(sessionId)}/private-followup`,
      { pending_id: pendingId, revision: rev, choice });
  } catch (err) {
    group.forEach(b => { b.disabled = false; });
    toast('Không gửi được lựa chọn: ' + (err.message || err));
    return false;
  }
  decidedConsents.add(key);
  try {
    const fresh = await getJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (currentSession === sessionId && fresh && Array.isArray(fresh.messages)) $('#message-list').innerHTML = renderTranscript(fresh);
  } catch { /* keep the answered-disabled chips as-is */ }
  return true;
}

function updateComposer() {
  const b = getBot(selectedBot) || bots[0];
  if (!b) return;
  document.documentElement.style.setProperty('--active-ink', b.palette.ink);
  $('#composer-label-text').textContent = 'Gửi ' + b.name + ',';
  $('#message').placeholder = 'Viết vài dòng cho ' + b.name + '…';
  // Reset per-turn overrides only on an actual bot/room change — plain
  // appearance repaints (color, roster sync) keep explicitly chosen values.
  const key = selectedBot + '|' + currentSession;
  if (key !== composerScopeKey) { composerScopeKey = key; resetComposerOverrides(); }
}
function saveHistory() { if (currentSession && !runs.has(currentSession)) histories.set(currentSession, $('#message-list').innerHTML); }

async function loadSession(id, title, botId) {
  if (window.ThycaRouter?.currentPage() === 'chat') ThycaRouter.updateCurrent({ search: '?s=' + encodeURIComponent(id), hash: '' });
  stopLab(); setGroupActivity(null); saveHistory();
  currentSession = id; if (botId) selectedBot = botId;
  const nav = nextChatNav();
  updateComposer();
  $('#chat-title').textContent = title || '…';
  try {
    const detail = await getJson(`/api/sessions/${encodeURIComponent(id)}`);
    if (nav !== chatNavCurrent() || currentSession !== id) return;   // user switched meanwhile
    $('#chat-title').textContent = detail.title || title || 'Chưa đặt tên';
    const group = detail.kind === 'group';
    $('#session-eyebrow').textContent = group ? 'Hội thoại chung' : 'Sổ trò chuyện · ' + ((getBot(detail.dm || selectedBot) || {}).name || 'Thyca');
    if (detail.dm && getBot(detail.dm)) selectedBot = detail.dm;
    $('#message-list').innerHTML = renderTranscript(detail);
    void renderExchangeGroups(detail);
    if (detail.running) followRunningTurn(id);        // API: GET /api/sessions/<id>/turn/stream replay
  } catch (err) {
    if (nav !== chatNavCurrent() || currentSession !== id) return;
    $('#message-list').innerHTML = `<article class="conversation-empty"><h2>Không tải được cuộc trò chuyện</h2><p>${escapeHTML(err.message)}</p></article>`;
  }
  updateComposer(); await refreshComposerInherit(selectedBot, nav);
  if (nav !== chatNavCurrent()) return;
  bots.forEach(updateCard); closeMobile(); busy(runs.has(id)); toBottom(true);
}

async function selectBot(id) {
  const b = getBot(id); if (!b) return;
  if (window.ThycaRouter?.currentPage() === 'chat') ThycaRouter.updateCurrent({ search: '?bot=' + encodeURIComponent(id), hash: '' });
  openBot(id, true);
  if (selectedBot === id && currentSession) return;
  const nav = nextChatNav();
  try {                                          // API: GET /api/bots/<id>/dm — fixed DM, find-or-create
    const detail = await getJson(`/api/bots/${encodeURIComponent(id)}/dm`);
    if (nav !== chatNavCurrent()) return;        // user switched meanwhile
    saveHistory(); currentSession = detail.id; selectedBot = id;
    updateComposer(); await refreshComposerInherit(id, nav);
    if (nav !== chatNavCurrent()) return;
    $('#chat-title').textContent = detail.title || b.name;
    $('#session-eyebrow').textContent = 'Sổ trò chuyện · ' + b.name;
    $('#message-list').innerHTML = renderTranscript(detail);
    void renderExchangeGroups(detail);
    if (detail.running) followRunningTurn(detail.id);   // API: GET turn/stream replay — rejoin in-flight turn after reload
    bots.forEach(updateCard); busy(runs.has(detail.id)); toBottom(true);
  } catch (err) {
    if (nav !== chatNavCurrent()) return;
    toast('Không mở được phiên với ' + b.name + ': ' + err.message);
  }
}

function isNearBottom() { const e = $('#conversation-scroll'); return e.scrollHeight - e.scrollTop - e.clientHeight < 100; }
function toBottom(force = false) { const e = $('#conversation-scroll'); if (force || isNearBottom()) e.scrollTop = e.scrollHeight; }
function busy(value) { $('#send-message').hidden = value; $('#stop-button').hidden = !value; $('#message-list').setAttribute('aria-busy', String(value)); }

/* POST /api/sessions/<id>/turn/stream — NDJSON. Events drive the seal
   (handleEvent maps them to lifecycle states) and stream into the live
   message node. Exactly one terminal event: turn.completed / cancelled / failed. */
async function sendTurn(text, botId = selectedBot, { retry = false } = {}) {
  if (runs.has(currentSession) || !currentSession) return false;
  const b = getBot(botId) || bots[0]; if (!b) return false;
  if (b.state === 'offline') { toast(b.name + ' đang offline. Khôi phục kết nối trước khi gửi.'); return false; }
  stopLab();
  const now = fmtTime();
  const list = $('#message-list');
  list.querySelector('.conversation-empty')?.remove();
  if (text && !retry) list.insertAdjacentHTML('beforeend', userMessage(text, now));
  const node = document.createElement('article'); node.className = 'message-assistant running-response'; paint(node, b);
  node.innerHTML = `<header class="message-byline"><span>${escapeHTML(b.name)}</span><time>đang lắng nghe</time></header><div class="live-copy"><p class="live-message">${dots}<span>Mình đang đọc lời nhắn của bạn…</span></p></div>`;
  list.append(node);
  const token = { botId: b.id, sessionId: currentSession, start: Date.now(), node, text: text || '', phase: 'waiting', timers: [], tools: [] };
  runs.set(token.sessionId, token); run = token; busy(true); openBot(b.id); toBottom(true);
  setState(b.id, 'waiting', 'Mình đang đọc yêu cầu\nvà ghi nhận những điều bạn cần.', 0);
  bots.forEach(x => { (x.feed || []).forEach(f => feedExit(x, f)); x.thinkBuf = ''; x.toolCounts = new Map(); x.toolRunning = 0; updateCard(x); });
  token.interval = setInterval(() => { if (runs.get(token.sessionId) !== token) { clearInterval(token.interval); return; } b.elapsed = Math.floor((Date.now() - token.start) / 1000); const e = $('.elapsed', $('#bot-' + b.id)); if (e) e.textContent = b.elapsed + ' giây'; }, 1000);

  const body = { text, retry };
  if ($('#composer-model')?.value) body.model = $('#composer-model').value;
  if ($('#thinking-effort')?.value) body.effort = $('#thinking-effort').value;
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(token.sessionId)}/turn/stream`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`; try { msg = (await res.json()).error || msg; } catch {}
      throw Object.assign(new Error(msg), { status: res.status });
    }
    await readNdjson(res.body, ev => handleStreamEvent(token, ev));
    if (runs.get(token.sessionId) === token && token.phase !== 'settled') finishTurn(token);   // stream ended without terminal event
  } catch (err) {
    if (runs.get(token.sessionId) === token) { token.phase = 'settled'; cancelTurn(token, 'Lượt trả lời gặp lỗi: ' + err.message, 'gặp lỗi'); setState(token.botId, 'error'); }
  }
  return true;
}

async function readNdjson(stream, onEvent) {
  const reader = stream.getReader(), decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n'); buf = lines.pop();
    for (const line of lines) { const s = line.trim(); if (s) { try { onEvent(JSON.parse(s)); } catch {} } }
  }
  const s = buf.trim(); if (s) { try { onEvent(JSON.parse(s)); } catch {} }
}

/* Pocket feed — real activity lines from the stream (thinking tail,
   tool/skill calls, writing), replacing the old fixed 3-step list. */
/* Window = last 3 lines. Entries pushed beyond it get `exiting` — they keep
   rendering with a fade-out class for ~450ms, then drop out of the state. */
function feedPush(b, entry) {
  entry.eid = ++feedSeq;
  b.feed.push(entry);
  if (b.feed.length > 8) b.feed.splice(0, b.feed.length - 8);
  b.feed.filter(f => !f.exiting).slice(0, -3).forEach(f => feedExit(b, f));
  updateCard(b);
}
function feedExit(b, f) {
  if (f.exiting) return;
  f.exiting = true;
  setTimeout(() => { const i = b.feed.indexOf(f); if (i > -1) { b.feed.splice(i, 1); updateCard(b); } }, 460);
}
/* Last entry that still counts for continuation — exiting ghosts are invisible. */
function feedLast(b) { return (b.feed || []).filter(f => !f.exiting).at(-1); }
function feedDone(b, match) { for (let i = b.feed.length - 1; i >= 0; i--) { const f = b.feed[i]; if (!f.done && match(f)) { f.done = true; if (f.kind === 'write') f.text = 'đã gửi phản hồi'; } } }
function feedSettle(b) { (b.feed || []).forEach(f => { if (!f.done) { f.done = true; if (f.kind === 'write') f.text = 'đã gửi phản hồi'; } }); updateCard(b); }
/* Aggregated tools line per block: "đã dùng edit, bashx3, rememberx2" —
   only folds into the LAST entry; a tool after a new Thinking row starts
   a fresh line below it. */
function feedTools(b) {
  const counts = b.toolCounts || new Map();
  const names = [...counts.keys()].slice(-3);
  const text = 'đã dùng ' + names.map(n => counts.get(n) > 1 ? n + 'x' + counts.get(n) : n).join(', ');
  const last = feedLast(b);
  const done = (b.toolRunning || 0) === 0;
  if (last && last.kind === 'tools') { last.text = text; last.done = done; updateCard(b); }
  else feedPush(b, { kind: 'tools', text, done });
}

function handleStreamEvent(token, ev) {
  const type = ev.type, botId = ev.bot_id || token.botId;
  const fb = getBot(botId);
  const liveCopy = $('.live-copy', token.node);
  switch (type) {
    case 'turn.accepted': setState(botId, 'waiting'); if (fb) { (fb.feed || []).forEach(f => feedExit(fb, f)); fb.thinkBuf = ''; fb.toolCounts = new Map(); fb.toolRunning = 0; updateCard(fb); } break;
    case 'llm.started': token.phase = 'thinking'; setState(botId, 'thinking'); $('.message-byline time', token.node).textContent = 'đang suy nghĩ'; if (fb) { feedDone(fb, f => f.kind === 'think' || f.kind === 'write'); fb.thinkBuf = ''; } break;
    case 'llm.thinking': {
      setState(botId, 'thinking');
      if (fb) {
        const last = feedLast(fb);
        // Consecutive think rounds (nothing pushed between) fold into "ThinkingxN".
        if (last && last.kind === 'think') {
          if (last.done) { last.n = (last.n || 1) + 1; last.done = false; last.text = 'Thinkingx' + last.n; updateCard(fb); }
        } else feedPush(fb, { kind: 'think', text: 'Thinking', n: 1, cls: 'li-think' });
      }
      break;
    }
    case 'tool.started': case 'skill.started': {
      token.phase = 'working'; token.tools.push(ev.name || 'tool');
      setState(botId, 'working', `Đang gọi ${ev.name || 'tool'}`);
      $('.message-byline time', token.node).textContent = 'đang làm việc';
      if (fb) {
        fb.toolRunning = (fb.toolRunning || 0) + 1;
        const lastEntry = feedLast(fb);
        if (!(lastEntry && lastEntry.kind === 'tools')) fb.toolCounts = new Map();   // new block below the last line
        fb.toolCounts = fb.toolCounts || new Map();
        const n = ev.name || 'tool'; fb.toolCounts.set(n, (fb.toolCounts.get(n) || 0) + 1);
        feedTools(fb);
      }
      break;
    }
    case 'tool.finished': case 'skill.finished':
      setState(botId, 'thinking');
      if (fb) { fb.toolRunning = Math.max(0, (fb.toolRunning || 0) - 1); const lastT = feedLast(fb); if (!fb.toolRunning && lastT && lastT.kind === 'tools') lastT.done = true; updateCard(fb); }
      // Admin tools may have added/removed/renamed a bot — re-sync the roster.
      if (/^bot_(create|pause|delete|restore|edit_card)$/.test(ev.name || '')) scheduleRosterSync();
      break;
    case 'llm.finished':
      if (fb) { feedDone(fb, f => f.kind === 'think' || f.kind === 'write'); updateCard(fb); }
      { const p = token.node && $('.stream-text', token.node);
        if (p && token.mdBuf) { p.innerHTML = contentHtml(token.mdBuf); if (token.caret) p.append(token.caret); } }
      break;
    case 'llm.content': {
      token.phase = 'writing'; setState(botId, 'writing'); notifyText(botId, ev.delta || '');
      if (fb && !fb.feed.some(f => f.kind === 'write' && !f.done && !f.exiting)) feedPush(fb, { kind: 'write', text: 'đang viết phản hồi' });
      token.mdBuf = (token.mdBuf || '') + (ev.delta || '');
      if (token.sessionId === currentSession) {          // node may be detached after a session switch
        $('.message-byline time', token.node).textContent = 'đang viết';
        let p = $('.stream-text', liveCopy);
        if (!p) {
          p = document.createElement('div'); p.className = 'stream-text markdown-body';
          token.caret = document.createElement('span'); token.caret.className = 'streaming-caret';
          p.append(token.caret); liveCopy.replaceChildren(p);
        }
        // Raw text during the stream — markdown is only re-parsed at stable
        // boundaries (llm.finished), same as the old UI, so half-written
        // fences/bold markers never glitch the render.
        p.insertBefore(document.createTextNode(ev.delta || ''), token.caret);
        toBottom();
      }
      break;
    }
    case 'session.naming.finished': if (ev.title && token.sessionId === currentSession) $('#chat-title').textContent = ev.title; break;
    case 'turn.completed': token.phase = 'settled'; if (fb) feedSettle(fb); finishTurn(token, ev.detail); break;
    case 'turn.cancelled': token.phase = 'settled'; if (fb) feedSettle(fb); cancelTurn(token, 'Đã dừng lượt trả lời.', 'đã dừng'); setState(botId, 'idle'); break;
    case 'turn.failed': token.phase = 'settled'; if (fb) feedSettle(fb); cancelTurn(token, ev.message || 'Lượt trả lời gặp lỗi.', 'gặp lỗi'); setState(botId, 'error'); break;
    case 'llm.retry': toast(`Đang thử lại (${ev.attempt}/${ev.max_attempts})…`); break;
  }
  const group = groups.find(g => g.id === token.sessionId);
  if (group && ev.bot_id) setGroupActivity(group.id, ev.bot_id);
}

/* turn.completed carries the authoritative session detail — re-render the
   transcript from it so streamed deltas reconcile with stored messages. */
async function finishTurn(token, detail) {
  clearInterval(token.interval); clearMusic(token.botId); setGroupActivity(null);
  runs.delete(token.sessionId); if (run === token) run = [...runs.values()].at(-1) || null; busy(runs.has(currentSession));
  if (currentSession === token.sessionId) {
    try {
      const fresh = detail && detail.messages ? detail : await getJson(`/api/sessions/${encodeURIComponent(token.sessionId)}`);
      $('#message-list').innerHTML = renderTranscript(fresh);
      void renderExchangeGroups(fresh);
      if (fresh.title) $('#chat-title').textContent = fresh.title;
    } catch { /* keep the streamed node as-is */ }
  }
  setState(token.botId, 'done', 'Đã hoàn tất.\nPhản hồi đã được gửi vào hội thoại.');
  refreshSessions().then(() => bots.forEach(updateCard)).catch(() => {});
  announce(getBot(token.botId)?.name + ' đã hoàn tất.');
}

function cancelTurn(token, message, byline) {
  clearInterval(token.interval); clearMusic(token.botId); setGroupActivity(null);
  runs.delete(token.sessionId); if (run === token) run = [...runs.values()].at(-1) || null; busy(runs.has(currentSession));
  const t = $('.message-byline time', token.node); if (t) t.textContent = byline;
  $('.streaming-caret', token.node)?.remove();
  const copy = $('.live-copy', token.node);
  if (copy) { const note = document.createElement('p'); note.className = 'stream-interruption'; note.textContent = message; copy.append(note); }
  refreshSessions().then(() => bots.forEach(updateCard)).catch(() => {});
}

async function stopRun() {
  const token = runs.get(currentSession) || run; if (!token) return false;
  try { await fetch(`/api/sessions/${encodeURIComponent(token.sessionId)}/turn/cancel`, { method: 'POST' }); }   // API: POST turn/cancel
  catch { token.phase = 'settled'; cancelTurn(token, 'Đã dừng lượt trả lời.', 'đã dừng'); setState(token.botId, 'idle'); }
  announce('Đã dừng.'); return true;
}

/* Rejoin an in-flight turn (another tab or reload): GET turn/stream replays
   the event journal, then tails live deltas. */
function followRunningTurn(sessionId) {
  const node = document.createElement('article'); node.className = 'message-assistant running-response';
  const b = getBot(selectedBot) || bots[0]; if (b) paint(node, b);
  node.innerHTML = `<header class="message-byline"><span>${b ? escapeHTML(b.name) : 'Thyca'}</span><time>đang chạy</time></header><div class="live-copy"><p class="live-message">${dots}<span>Đang theo lượt trả lời hiện tại…</span></p></div>`;
  $('#message-list').append(node);
  const token = { botId: b?.id || '', sessionId, start: Date.now(), node, phase: 'following', timers: [], tools: [] };
  runs.set(sessionId, token); run = token; busy(true);
  if (b) token.interval = setInterval(() => { if (runs.get(sessionId) !== token) { clearInterval(token.interval); return; } b.elapsed = Math.floor((Date.now() - token.start) / 1000); const e = $('.elapsed', $('#bot-' + b.id)); if (e) e.textContent = b.elapsed + ' giây'; }, 1000);
  fetch(`/api/sessions/${encodeURIComponent(sessionId)}/turn/stream`)
    .then(res => res.ok ? readNdjson(res.body, ev => handleStreamEvent(token, ev)) : Promise.reject(new Error('HTTP ' + res.status)))
    .then(() => { if (runs.get(sessionId) === token && token.phase !== 'settled') finishTurn(token); })
    .catch(() => { if (runs.get(sessionId) === token) finishTurn(token); });
}

/* openMobile/closeMobile moved to ../share/shell-app.js — shared by every page. */
