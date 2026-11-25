'use strict';
const fmtTime = ts => { const d = ts ? new Date(ts) : new Date(); return Number.isFinite(d.getTime()) ? d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' }) : ''; };
function userMessage(text, time) {
  const convo = framedConversationData(text);
  if (convo && convo.messages.length) return framedConversationBlock(convo, time);
  const framed = framedConversationFallback(text);
  const body = framed ? framed.html : `<p>${escapeHTML(text).replace(/\n/g, '<br>')}</p>`;
  const source = framed ? `<span class="message-source">${framed.label}</span>` : '';
  return `<article class="message-user">${body}<footer>${source}<time>${time}</time>${icon('check')}</footer></article>`;
}
function framedConversationData(text) {
  const m = String(text || '').match(/UNTRUSTED_CONVERSATION job_ref=(\S+) START\n([\s\S]*?)\nUNTRUSTED_CONVERSATION job_ref=\1 END/);
  if (!m) return null;
  let frame = null;
  try { frame = JSON.parse(m[2].trim()); } catch { frame = null; }
  if (!frame || typeof frame !== 'object' || !Array.isArray(frame.messages)) return null;
  const names = {};
  for (const p of Array.isArray(frame.participants) ? frame.participants : []) {
    if (p && p.uid) names[String(p.uid)] = String(p.display_name || p.uid);
  }
  const shortUid = uid => { const s = String(uid); return s.length > 12 ? s.slice(0, 6) + '…' + s.slice(-4) : s; };
  const nameOf = uid => names[String(uid)] || shortUid(uid);
  const byId = new Map();
  for (const item of frame.messages) if (item && typeof item === 'object' && item.id) byId.set(String(item.id), item);
  const messages = [];
  for (const item of frame.messages) {
    if (!item || typeof item !== 'object') continue;
    const replyTarget = item.reply_to ? byId.get(String(item.reply_to)) : null;
    const textHtml = escapeHTML(String(item.text ?? '')).replace(/&lt;@([^&<>]+)&gt;/g,
      (hit, uid) => '@' + escapeHTML(nameOf(String(uid).trim()))).replace(/\n/g, '<br>');
    messages.push({
      sender: nameOf(item.sender_uid),
      textHtml,
      time: item.timestamp ? fmtTime(item.timestamp) : '',
      isNew: frame.new_message_id != null && String(item.id) === String(frame.new_message_id),
      replyTo: replyTarget ? nameOf(replyTarget.sender_uid) : '',
    });
  }
  const sourceLabel = [frame.source_label, frame.source, frame.channel_name, frame.channel]
    .find(v => typeof v === 'string' && v.trim())?.trim() || 'Hội thoại ngoài';
  return messages.length ? { messages, label: sourceLabel } : null;
}

function framedConversationBlock(convo, time) {
  const rows = convo.messages.map(msg => `<article class="framed-msg${msg.isNew ? ' is-new' : ''}"><header><strong>${escapeHTML(msg.sender)}</strong>${msg.time ? `<time>${escapeHTML(msg.time)}</time>` : ''}${msg.replyTo ? `<span class="framed-reply">trả lời ${escapeHTML(msg.replyTo)}</span>` : ''}</header><div class="framed-text">${msg.textHtml}</div></article>`).join('');
  return `<section class="framed-convo" aria-label="${escapeHTML(convo.label)}"><header class="framed-head"><span class="message-source">${escapeHTML(convo.label)}</span><time>${time}</time></header>${rows}</section>`;
}

function framedConversationFallback(text) {
  const m = String(text || '').match(/UNTRUSTED_CONVERSATION job_ref=(\S+) START\n([\s\S]*?)\nUNTRUSTED_CONVERSATION job_ref=\1 END/);
  if (!m) return null;
  const inner = m[2].trim();
  return { label: 'Hội thoại ngoài', html: `<p>${escapeHTML(inner).replace(/\n/g, '<br>')}</p>` };
}
function assistantMessage(botId, content, time, toolNames = []) {
  const b = getBot(botId) || { id: botId, name: botId || 'Thyca', palette: derivePalette('#a65a42') };
  const tools = toolNames.length ? `<span class="usage-pill">${icon('leaf')}Đã dùng: ${toolNames.map(escapeHTML).join(', ')}</span>` : '';
  return `<article class="message-assistant" style="${paletteStyle(b)}"><header class="message-byline"><span>${escapeHTML(b.name)}</span><time>${time}</time></header><div class="live-copy markdown-body">${content}</div><footer class="message-footer">${tools}<button class="again" data-retry="${b.id}">${icon('retry')}Thử lại</button><button data-copy aria-label="Sao chép phản hồi">${icon('copy')}</button></footer></article>`;
}
const contentHtml = text => window.formatMarkdown
  ? window.formatMarkdown(text)
  : escapeHTML(String(text || '')).split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');

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
const decidedConsents = new Set();
function consentKeyOf(card) {
  const pf = card && typeof card === 'object' ? card.private_followup : null;
  if (!pf || typeof pf !== 'object' || !pf.pending_id) return '';
  return `${pf.pending_id}:${String(pf.revision ?? '')}`;
}
function cardArticle(m, detail, time, userAfter) {
  const card = m.meta.card;
  const botId = String(m.meta?.bot_id || detail.bot_id || '');
  const names = (m.tool_calls || []).map(c => c.skill || c.name).filter(Boolean);
  const answered = userAfter || (consentKeyOf(card) && decidedConsents.has(consentKeyOf(card)));
  const body = card.type === 'error' ? renderErrorCard(card, answered, botId) : renderQuestionCard(card, answered, botId);
  const content = typeof m.content === 'string' ? m.content.trim() : '';
  const lead = content && content !== String(card.title || '').trim() ? contentHtml(m.content) : '';
  return assistantMessage(botId, lead + body, time, names);
}
function peerDisplayName(id) { const b = getBot(id); return (b && b.name) || id; }
function renderTranscript(detail) {
  const nodes = [];
  const messages = detail.messages || [];
  const stamp = (html, ts) => ts
    ? html.replace('<article ', `<article data-ts="${escapeHTML(String(ts))}" `)
    : html;
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (!m || m.role === 'system' || m.role === 'tool') continue;
    const kind = m.meta?.kind;
    if (kind === 'compaction') continue;
    if (m.meta?.bus_event === true || kind === 'result') continue;
    const time = fmtTime(m.ts);
    if (m.role === 'user') { nodes.push(stamp(userMessage(m.content || '', time), m.ts)); continue; }
    if (m.role !== 'assistant') continue;
    const card = m.meta?.card;
    if (m.meta?.kind === 'card' && card && typeof card === 'object'
        && (card.type === 'question' || card.type === 'error')) {
      const userAfter = messages.slice(i + 1).some(next => next && next.role === 'user');
      nodes.push(stamp(cardArticle(m, detail, time, userAfter), m.ts));
      continue;
    }
    const names = (m.tool_calls || []).map(c => c.skill || c.name).filter(Boolean);
    nodes.push(stamp(assistantMessage(String(m.meta?.bot_id || detail.bot_id || ''), contentHtml(m.content), time, names), m.ts));
  }
  return nodes.join('');
}

function botFace(id) {
  const b = getBot(id);
  if (b) { try { return avatar(b); } catch {  } }
  const name = (b && b.name) || String(id || '?');
  return `<span class="seal-avatar seal-fallback" aria-hidden="true">${escapeHTML(name.slice(0, 1).toUpperCase())}</span>`;
}
async function renderExchangeGroups(detail) {
  document.querySelectorAll('#message-list [data-exchange-partner]').forEach(n => n.remove());
  const owner = detail && detail.kind === 'dm' ? detail.bot_id : null;
  const sid = currentConversation;
  if (!owner) return;
  let partners = [];
  try {
    const data = await getJson(`/api/bots/${encodeURIComponent(owner)}/exchanges`);
    partners = (data && data.exchanges) || [];
  } catch { return; }
  if (currentConversation !== sid || !partners.length) return;
  const list = $('#message-list');
  if (!list) return;
  for (const p of partners) {
    if (!p || !p.partner) continue;
    if (p.partner === owner) continue;
    const name = peerDisplayName(p.partner);
    const partnerBot = getBot(p.partner);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'exchange-group';
    btn.dataset.exchangeOwner = owner;
    btn.dataset.exchangePartner = p.partner;
    if (partnerBot) btn.setAttribute('style', paletteStyle(partnerBot));
    btn.setAttribute('aria-label', `${p.count} tin nhắn với ${name}`);
    const when = p.last_ts
      ? `<time class="exchange-group-time" datetime="${escapeHTML(String(p.last_ts))}">${escapeHTML(fmtTime(p.last_ts))}</time>`
      : '';
    btn.innerHTML = `${botFace(p.partner)}<span><strong>${escapeHTML(String(p.count))} tin nhắn</strong> với ${escapeHTML(name)}</span>${when}`;
    btn.addEventListener('click', () => openExchangeModal(owner, p.partner));
    const lastTs = String(p.last_ts || '');
    let before = null;
    if (lastTs) {
      for (const child of list.children) {
        const ts = child.getAttribute && child.getAttribute('data-ts');
        if (ts && ts > lastTs) { before = child; break; }
      }
    }
    if (before) list.insertBefore(btn, before); else list.append(btn);
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

function answerCardLabel(label, button) {
  label = String(label || '').trim();
  if (!label || !currentConversation || runs.has(currentConversation)) return false;
  if (button) button.disabled = true;
  const botId = String(button?.dataset?.cardBot || '') || selectedBot;
  void Promise.resolve(sendTurn(label, botId)).then(
    ok => { if (!ok && button) button.disabled = false; },
    () => { if (button) button.disabled = false; });
  return true;
}
async function decidePrivateFollowup(pendingId, revision, choice, button) {
  const rev = Number(revision);
  const conversationId = currentConversation;
  const parts = convParts(conversationId);
  if (!pendingId || !Number.isInteger(rev) || !choice
      || !conversationId || !parts || parts.kind !== 'dm' || runs.has(conversationId)) return false;
  const key = `${pendingId}:${String(rev)}`;
  const group = button ? [...button.closest('.card-choices')?.querySelectorAll('.card-choice') || []] : [];
  group.forEach(b => { b.disabled = true; });
  try {
    await postJson(`/api/bots/${encodeURIComponent(parts.bot_id)}/dm/private-followup`,
      { pending_id: pendingId, revision: rev, choice });
  } catch (err) {
    group.forEach(b => { b.disabled = false; });
    toast('Không gửi được lựa chọn: ' + (err.message || err));
    return false;
  }
  decidedConsents.add(key);
  try {
    const fresh = await getJson(`/api/bots/${encodeURIComponent(parts.bot_id)}/dm`);
    if (currentConversation === conversationId && fresh && Array.isArray(fresh.messages)) $('#message-list').innerHTML = renderTranscript(fresh);
  } catch {  }
  return true;
}

function updateComposer() {
  const b = getBot(selectedBot) || bots[0];
  if (!b) return;
  document.documentElement.style.setProperty('--active-ink', b.palette.ink);
  $('#composer-label-text').textContent = 'Gửi ' + b.name + ',';
  $('#message').placeholder = 'Viết vài dòng cho ' + b.name + '…';
}
function saveHistory() { if (currentConversation && !runs.has(currentConversation)) histories.set(currentConversation, $('#message-list').innerHTML); }

function setDayDivider(detail) {
  const el = document.querySelector('.day-divider');
  if (!el) return;
  const b = detail && detail.kind !== 'group' ? getBot(detail.bot_id || selectedBot) : null;
  const desc = b && typeof b.description === 'string' ? b.description.trim() : '';
  el.textContent = desc || 'Hôm nay, một khởi đầu mới';
}

async function loadConversation(id, botId) {
  const parts = convParts(id);
  if (!parts) return;
  if (window.ThycaRouter?.currentPage() === 'chat') {
    ThycaRouter.updateCurrent({ search: parts.kind === 'group'
      ? '?g=' + encodeURIComponent(parts.group_id)
      : '?bot=' + encodeURIComponent(parts.bot_id), hash: '' });
  }
  stopLab(); setGroupActivity(null); saveHistory();
  currentConversation = id; if (botId) selectedBot = botId;
  const nav = nextChatNav();
  updateComposer();
  $('#chat-title').textContent = '…';
  try {
    const detail = await getJson(convDetailUrl(id));
    if (nav !== chatNavCurrent() || currentConversation !== id) return;
    const group = detail.kind === 'group';
    const groupTitle = groupTitleOf(detail.title);
    $('#chat-title').textContent = group ? groupTitle : ((getBot(detail.bot_id || selectedBot) || {}).name || 'Thyca');
    $('#conversation-eyebrow').textContent = group ? groupTitle : 'Sổ trò chuyện · ' + ((getBot(detail.bot_id || selectedBot) || {}).name || 'Thyca');
    setDayDivider(detail);
    if (detail.bot_id && getBot(detail.bot_id)) selectedBot = detail.bot_id;
    $('#message-list').innerHTML = renderTranscript(detail);
    void renderExchangeGroups(detail);
    if (detail.running) followRunningTurn(id);
  } catch (err) {
    if (nav !== chatNavCurrent() || currentConversation !== id) return;
    $('#message-list').innerHTML = `<article class="conversation-empty"><h2>Không tải được cuộc trò chuyện</h2><p>${escapeHTML(err.message)}</p></article>`;
  }
  updateComposer();
  if (nav !== chatNavCurrent()) return;
  bots.forEach(updateCard);
  if (typeof refreshOpenPocketsOnWindowChange === 'function') refreshOpenPocketsOnWindowChange();
  closeMobile(); busy(runs.has(id)); toBottom(true);
}

async function selectBot(id) {
  const b = getBot(id); if (!b) return;
  if (window.ThycaRouter?.currentPage() === 'chat') ThycaRouter.updateCurrent({ search: '?bot=' + encodeURIComponent(id), hash: '' });
  if (selectedBot === id && currentConversation) return;
  const nav = nextChatNav();
  try {
    const detail = await getJson(`/api/bots/${encodeURIComponent(id)}/dm`);
    if (nav !== chatNavCurrent()) return;
    const key = dmKey(detail.bot_id || id);
    saveHistory(); currentConversation = key; selectedBot = id;
    updateComposer();
    if (nav !== chatNavCurrent()) return;
    $('#chat-title').textContent = b.name;
    $('#conversation-eyebrow').textContent = 'Sổ trò chuyện · ' + b.name;
    setDayDivider(detail);
    $('#message-list').innerHTML = renderTranscript(detail);
    void renderExchangeGroups(detail);
    if (detail.running) followRunningTurn(key);
    bots.forEach(updateCard);
    if (typeof refreshOpenPocketsOnWindowChange === 'function') refreshOpenPocketsOnWindowChange();
    busy(runs.has(key)); toBottom(true);
  } catch (err) {
    if (nav !== chatNavCurrent()) return;
    toast('Không mở được phiên với ' + b.name + ': ' + err.message);
  }
}

function isNearBottom() { const e = $('#conversation-scroll'); return e.scrollHeight - e.scrollTop - e.clientHeight < 100; }
function toBottom(force = false) { const e = $('#conversation-scroll'); if (force || isNearBottom()) e.scrollTop = e.scrollHeight; }
function busy(value) { $('#send-message').hidden = value; $('#stop-button').hidden = !value; $('#message-list').setAttribute('aria-busy', String(value)); }

async function sendTurn(text, botId = selectedBot, { retry = false } = {}) {
  if (runs.has(currentConversation) || !currentConversation) return false;
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
  const token = { botId: b.id, conversationId: currentConversation, start: Date.now(), node, text: text || '', phase: 'waiting', timers: [], tools: [] };
  runs.set(token.conversationId, token); run = token; busy(true); toBottom(true);
  clearLiveCalls(b, token.conversationId); updateCard(b);
  setState(b.id, 'waiting', 'Mình đang đọc yêu cầu\nvà ghi nhận những điều bạn cần.', 0);
  token.interval = setInterval(() => { if (runs.get(token.conversationId) !== token) { clearInterval(token.interval); return; } b.elapsed = Math.floor((Date.now() - token.start) / 1000); const e = $('.elapsed', $('#bot-' + b.id)); if (e) e.textContent = formatElapsed(b.elapsed); }, 1000);

  const body = { text, retry };
  try {
    const res = await fetch(convDoorUrl(token.conversationId, 'turn/stream'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`; try { msg = (await res.json()).error || msg; } catch {}
      throw Object.assign(new Error(msg), { status: res.status });
    }
    await readNdjson(res.body, ev => handleStreamEvent(token, ev));
    if (runs.get(token.conversationId) === token && token.phase !== 'settled') finishTurn(token);
  } catch (err) {
    if (runs.get(token.conversationId) === token) { token.phase = 'settled'; cancelTurn(token, 'Lượt trả lời gặp lỗi: ' + err.message, 'gặp lỗi'); setState(token.botId, 'error'); }
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

function handleStreamEvent(token, ev) {
  if (!token || token.superseded) return;
  const type = ev.type, botId = ev.bot_id || token.botId;
  const fb = getBot(botId);
  const liveCopy = $('.live-copy', token.node);
  const own = botId === token.botId;
  switch (type) {
    case 'turn.accepted':
      setState(botId, 'waiting');
      if (fb) {
        const accepted = token.acceptedBots || (token.acceptedBots = new Set());
        if (!accepted.has(botId)) { clearLiveCalls(fb, token.conversationId); accepted.add(botId); }
        updateCard(fb);
      }
      break;
    case 'llm.started':
      if (own) {
        if (token.mdBuf) token.needRoundBreak = true;
        token.phase = 'thinking';
        $('.message-byline time', token.node).textContent = 'đang suy nghĩ';
      }
      setState(botId, 'thinking');
      break;
    case 'llm.thinking':
      setState(botId, 'thinking');
      break;
    case 'tool.started': case 'skill.started': {
      if (own) {
        token.phase = 'working'; token.tools.push(ev.name || 'tool');
        $('.message-byline time', token.node).textContent = 'đang làm việc';
      }
      const kind = type === 'skill.started' ? 'skill' : 'tool';
      setState(botId, 'working', `Đang gọi ${ev.name || 'tool'}`);
      if (fb) {
        liveCallStarted(fb, { kind, name: ev.name || 'tool', call_id: ev.call_id, invocation_id: ev.invocation_id, turn: token, conversation: token.conversationId });
        updateCard(fb);
        if (typeof refreshOpenPocket === 'function') refreshOpenPocket(fb);
      }
      break;
    }
    case 'tool.finished': case 'skill.finished': {
      const finKind = type === 'skill.finished' ? 'skill' : 'tool';
      if (fb) {
        liveCallFinished(fb, ev.call_id,
          { kind: finKind, name: ev.name || undefined, invocation_id: ev.invocation_id, turn: token, conversation: token.conversationId });
        const remaining = liveCallsForWindow(fb, token.conversationId);
        setState(botId, remaining.length ? 'working' : 'thinking');
        updateCard(fb);
        if (typeof refreshOpenPocket === 'function') refreshOpenPocket(fb);
      } else setState(botId, 'thinking');
      if (/^bot_(create|pause|delete|restore|edit_card)$/.test(ev.name || '')) scheduleRosterSync();
      break;
    }
    case 'llm.finished':
      if (own) { const p = token.node && $('.stream-text', token.node);
        if (p && token.mdBuf) { p.innerHTML = contentHtml(token.mdBuf); if (token.caret) p.append(token.caret); } }
      break;
    case 'llm.content': {
      setState(botId, 'writing'); notifyText(botId, ev.delta || '');
      if (!own) break;
      const delta = ev.delta || '';
      const sep = token.needRoundBreak && delta ? '\n\n' : '';
      if (sep) token.needRoundBreak = false;
      token.phase = 'writing';
      token.mdBuf = (token.mdBuf || '') + sep + delta;
      if (token.conversationId === currentConversation) {
        $('.message-byline time', token.node).textContent = 'đang viết';
        let p = $('.stream-text', liveCopy);
        if (!p) {
          p = document.createElement('div'); p.className = 'stream-text markdown-body';
          token.caret = document.createElement('span'); token.caret.className = 'streaming-caret';
          p.append(token.caret); liveCopy.replaceChildren(p);
        }
        p.insertBefore(document.createTextNode(sep + delta), token.caret);
        toBottom();
      }
      break;
    }
    case 'turn.completed': token.phase = 'settled'; if (fb) { clearLiveCalls(fb, token.conversationId); updateCard(fb); if (typeof refreshOpenPocket === 'function') refreshOpenPocket(fb); } finishTurn(token, ev.detail); break;
    case 'turn.cancelled': token.phase = 'settled'; if (fb) { clearLiveCalls(fb, token.conversationId); updateCard(fb); if (typeof refreshOpenPocket === 'function') refreshOpenPocket(fb); } cancelTurn(token, 'Đã dừng lượt trả lời.', 'đã dừng'); setState(botId, 'idle'); break;
    case 'turn.failed': token.phase = 'settled'; if (fb) { clearLiveCalls(fb, token.conversationId); updateCard(fb); if (typeof refreshOpenPocket === 'function') refreshOpenPocket(fb); } cancelTurn(token, ev.message || 'Lượt trả lời gặp lỗi.', 'gặp lỗi'); setState(botId, 'error'); break;
    case 'llm.retry': toast(`Đang thử lại (${ev.attempt}/${ev.max_attempts})…`); break;
  }
  const group = groups.find(g => groupKey(g.id) === token.conversationId);
  if (group && ev.bot_id) setGroupActivity(group.id, ev.bot_id);
}

async function finishTurn(token, detail) {
  clearInterval(token.interval); clearMusic(token.botId); setGroupActivity(null);
  runs.delete(token.conversationId); if (run === token) run = [...runs.values()].at(-1) || null; busy(runs.has(currentConversation));
  if (currentConversation === token.conversationId) {
    try {
      const fresh = detail && detail.messages ? detail : await getJson(convDetailUrl(token.conversationId));
      $('#message-list').innerHTML = renderTranscript(fresh);
      void renderExchangeGroups(fresh);
    } catch {  }
  }
  setState(token.botId, 'done', 'Đã hoàn tất.\nPhản hồi đã được gửi vào hội thoại.');
  refreshConversations().then(() => bots.forEach(updateCard)).catch(() => {});
  announce(getBot(token.botId)?.name + ' đã hoàn tất.');
}

function cancelTurn(token, message, byline) {
  clearInterval(token.interval); clearMusic(token.botId); setGroupActivity(null);
  runs.delete(token.conversationId); if (run === token) run = [...runs.values()].at(-1) || null; busy(runs.has(currentConversation));
  const t = $('.message-byline time', token.node); if (t) t.textContent = byline;
  $('.streaming-caret', token.node)?.remove();
  const copy = $('.live-copy', token.node);
  if (copy) { const note = document.createElement('p'); note.className = 'stream-interruption'; note.textContent = message; copy.append(note); }
  refreshConversations().then(() => bots.forEach(updateCard)).catch(() => {});
}

async function stopRun() {
  const token = runs.get(currentConversation) || run; if (!token) return false;
  try { await fetch(convDoorUrl(token.conversationId, 'turn/cancel'), { method: 'POST' }); }
  catch { token.phase = 'settled'; cancelTurn(token, 'Đã dừng lượt trả lời.', 'đã dừng'); setState(token.botId, 'idle'); }
  announce('Đã dừng.'); return true;
}

function followRunningTurn(conversationId) {
  const prev = runs.get(conversationId);
  if (prev && prev.phase !== 'settled') {
    $('#message-list').append(prev.node);
    if (prev.mdBuf) {
      const copy = $('.live-copy', prev.node);
      const text = document.createElement('div');
      text.className = 'stream-text markdown-body';
      text.textContent = prev.mdBuf;
      prev.caret = document.createElement('span');
      prev.caret.className = 'streaming-caret';
      text.append(prev.caret); copy.replaceChildren(text);
    }
    run = prev; busy(true);
    return;
  }
  const node = document.createElement('article'); node.className = 'message-assistant running-response';
  const b = getBot(selectedBot) || bots[0]; if (b) paint(node, b);
  node.innerHTML = `<header class="message-byline"><span>${b ? escapeHTML(b.name) : 'Thyca'}</span><time>đang chạy</time></header><div class="live-copy"><p class="live-message">${dots}<span>Đang theo lượt trả lời hiện tại…</span></p></div>`;
  $('#message-list').append(node);
  const token = { botId: b?.id || '', conversationId, start: Date.now(), node, phase: 'following', timers: [], tools: [] };
  runs.set(conversationId, token); run = token; busy(true);
  if (b) token.interval = setInterval(() => { if (runs.get(conversationId) !== token) { clearInterval(token.interval); return; } b.elapsed = Math.floor((Date.now() - token.start) / 1000); const e = $('.elapsed', $('#bot-' + b.id)); if (e) e.textContent = formatElapsed(b.elapsed); }, 1000);
  fetch(convDoorUrl(conversationId, 'turn/stream'))
    .then(res => res.ok ? readNdjson(res.body, ev => handleStreamEvent(token, ev)) : Promise.reject(new Error('HTTP ' + res.status)))
    .then(() => { if (runs.get(conversationId) === token && token.phase !== 'settled') finishTurn(token); })
    .catch(() => { if (runs.get(conversationId) === token) finishTurn(token); });
}
