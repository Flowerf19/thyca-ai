// Chat boot: fetch roster + sessions, initial render, chat-only event wiring.
// Shared shell wiring (menu, splitter, dialogs, motion, idle, roster sync)
// lives in ../share/shell-app.js; shared fetch/state in ../share/data.js.
'use strict';

/* @mention regex is built from the live roster — no hardcoded ids. */
const mentionPattern = () => new RegExp('@(' + bots.map(b => b.id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\b', 'i');

async function boot() {
  try { await loadRoster(); }          // API: GET /api/bots
  catch (err) {
    $('#message-list').innerHTML = `<article class="conversation-empty"><h2>Không kết nối được máy chủ</h2><p>${escapeHTML(err.message)}</p></article>`;
    return;
  }
  renderTeam();
  bots.forEach(updateCard);
  $('.bot-count').textContent = bots.length + ' người bạn';
  rebuildExpressionBots();
  try { await refreshSessions(); renderGroups(); } catch {}   // API: GET /api/sessions
  loadConfig();                                              // API: GET /api/config

  // Deep links from other pages: ?bot=<id> opens that DM, ?s=<id> a session.
  const params = new URLSearchParams(location.search);
  const linkSession = params.get('s');
  const linkBot = params.get('bot');
  const first = getBot(linkBot) || bots[0];
  if (first) { selectedBot = first.id; updateComposer(); }
  if (linkSession && groups.some(g => g.id === linkSession)) { await loadSession(linkSession); }
  else if (first) { await selectBot(first.id); }
  else $('#message-list').innerHTML = '<article class="conversation-empty"><h2>Chưa có bot nào</h2><p>Đăng ký một bot để bắt đầu trò chuyện.</p></article>';
}

$('#run-demo').hidden = true;            // prototype-only button; real turns go through the composer
$('#stop-button').onclick = stopRun;

$('#composer').onsubmit = e => {
  e.preventDefault(); const input = $('#message'); let text = input.value.trim(); if (!text || runs.has(currentSession)) return;
  const match = text.match(mentionPattern()); const recipient = match ? match[1].toLowerCase() : selectedBot;
  if (attachment) { toast('Tệp đính kèm chưa được hỗ trợ — chỉ gửi phần chữ.'); }
  sendTurn(text, recipient);
  input.value = ''; input.style.height = ''; $('#mention-popup').hidden = true; attachment = null; $('#file-chip').hidden = true; typedMention = '';
};
let typedMention = '';
$('#message').addEventListener('keydown', e => { if (e.isComposing) return; if (e.key === 'Escape') $('#mention-popup').hidden = true; if (e.key === 'ArrowDown' && !$('#mention-popup').hidden) { e.preventDefault(); $('.mention-option').focus(); return; } if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#composer').requestSubmit(); } });
$('#message').addEventListener('input', e => {
  const el = e.target; idleInput(selectedBot); el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  const complete = el.value.match(new RegExp(mentionPattern().source.replace('\\b', '') + '(?=\\s|$)', 'i'));
  if (complete && typedMention !== complete[1].toLowerCase()) { typedMention = complete[1].toLowerCase(); mentionBot(typedMention); }
  if (!complete) typedMention = '';
  const q = el.value.match(/@([\w-]*)$/); const popup = $('#mention-popup'); popup.hidden = !q;
  if (q) { const matches = bots.filter(b => b.id.startsWith(q[1].toLowerCase())); popup.innerHTML = matches.map(b => `<button type="button" class="mention-option" role="option" data-mention="${b.id}" style="color:${b.palette.ink}">@${b.name} · ${escapeHTML(b.role)}</button>`).join(''); popup.hidden = !matches.length; }
});
$('#message').addEventListener('focus', () => idleInput(selectedBot));
$('#mention-popup').onclick = e => { const item = e.target.closest('[data-mention]'); if (item) { $('#message').value = $('#message').value.replace(/@[\w-]*$/, '@' + item.dataset.mention + ' '); selectedBot = item.dataset.mention; typedMention = selectedBot; mentionBot(selectedBot); updateComposer(); $('#mention-popup').hidden = true; $('#message').focus(); } };
$('#attach-file').onclick = () => $('#file-input').click(); $('#file-input').onchange = e => { attachment = e.target.files[0]; if (!attachment) return; $('#file-chip span').textContent = attachment.name; $('#file-chip').hidden = false; toast('Tệp đính kèm chưa được backend hỗ trợ.'); }; $('#remove-file').onclick = () => { attachment = null; $('#file-chip').hidden = true; $('#file-input').value = ''; };
$('#message-list').addEventListener('click', async e => {
  const retry = e.target.closest('[data-retry]'); const copy = e.target.closest('[data-copy]');
  if (retry && !runs.has(currentSession)) sendTurn('', retry.dataset.retry, { retry: true });   // API: retry:true re-runs the last user message
  if (copy) { const text = $('.live-copy', copy.closest('article')).innerText; try { await navigator.clipboard.writeText(text); toast('Đã sao chép phản hồi.'); } catch { toast('Trình duyệt chưa cho phép sao chép.'); } }
});
$('#session-info').onclick = () => {
  const g = groups.find(g => g.id === currentSession);
  $('#bot-drawer-name').textContent = $('#chat-title').textContent;
  $('#bot-drawer-subtitle').textContent = g ? 'Hội thoại với ' + g.bots.map(id => (getBot(id) || {}).name || id).join(', ') : 'Phiên cùng ' + ((getBot(selectedBot) || {}).name || '…');
  $('#bot-drawer-body').innerHTML = '';
  $('#bot-drawer').showModal();
};
$('#conversation-scroll').addEventListener('scroll', () => $('#to-bottom').hidden = isNearBottom()); $('#to-bottom').onclick = () => toBottom(true);

/* Public adapter for tests/demos: shared shell surface + chat turn control. */
window.ThycaPrototype = Object.assign(window.ThycaShell || {}, { sendTurn, stop: stopRun });

boot();
