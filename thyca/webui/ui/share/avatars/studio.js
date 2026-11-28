'use strict';
const lab = { timers: [], mode: null, botId: null, lastChoice: 'idle', sample: '', ownsGroup: false, idleTab: false, lastIdle: 'look' };

function buildIdleGallery(b) {
  if (!b) return;
  const grid = $('#idle-expression-grid');
  if (grid) {
    grid.innerHTML = Object.entries(idleCatalog).map(([key, v], i) => `<button class="expression-tile" data-idle-choice="${key}" style="${paletteStyle(b)}" aria-label="Thử cử chỉ ${v.label}" aria-pressed="${b.idle?.active === key}"><span class="expression-index">${String(i + 1).padStart(2, '0')}</span>${avatar(b, 'idle', 'expression-preview', { idle: key, preview: true })}<strong>${v.label}</strong><small>${v.hint}</small></button>`).join('');
  }
  if ($('#idle-auto-toggle')) $('#idle-auto-toggle').checked = idleOptions.enabled;
  if ($('#idle-comments-toggle')) $('#idle-comments-toggle').checked = idleOptions.comments;
}
function studioTab(idle) {
  lab.idleTab = idle;
  if ($('#idle-panel')) $('#idle-panel').hidden = !idle;
  if ($('#lifecycle-panel')) $('#lifecycle-panel').hidden = idle;
  if ($('#lab-play')) $('#lab-play').hidden = idle;
  $('#idle-tab')?.setAttribute('aria-selected', String(idle));
  $('#work-tab')?.setAttribute('aria-selected', String(!idle));
  if ($('#idle-tab')) $('#idle-tab').tabIndex = idle ? 0 : -1;
  if ($('#work-tab')) $('#work-tab').tabIndex = idle ? -1 : 0;
  if ($('#lab-playback-note')) {
    $('#lab-playback-note').textContent = idle ? 'Mắt tự nhìn quanh, cử chỉ và lời nhắn đổi ngẫu nhiên.' : 'Chọn biểu cảm để xem chuyển động.';
  }
}
function chooseIdle(key = null) {
  if (typeof run !== 'undefined' && run) {
    if (typeof toast === 'function') toast('Dừng lượt trả lời đang chạy trước khi thử cử chỉ rảnh.');
    return false;
  }
  stopLab(false);
  lab.botId = $('#expression-bot')?.value || null;
  lab.mode = 'idle-preview';
  lab.sample = '';
  studioTab(true);
  const b = getBot(lab.botId);
  if (!b) return false;
  setState(b.id, 'idle', undefined, 0);
  const chosen = beginIdle(b.id, key, true);
  lab.lastIdle = chosen;
  if ($('#lab-stop')) $('#lab-stop').hidden = false;
  if (chosen && typeof announce === 'function') announce(b.name + ': ' + idleCatalog[chosen].label);
  return chosen;
}
function buildShapePicker(b) {
  const picker = $('#shape-picker');
  if (!picker || !b) return;
  const curShape = crewShapeFor(b).id;
  picker.innerHTML = CREW.map(c => {
    const isPressed = (b.shape === c.id) || (!b.shape && curShape === c.id);
    return `<button type="button" data-shape="${c.id}" style="${paletteStyle(b)}" aria-pressed="${isPressed}" aria-label="Hình dạng ${escapeHTML(c.name)}" title="${escapeHTML(c.name)}"><span class="shape-preview">${avatar({ ...b, shape: c.id }, b.state, 'expression-preview size-pick', { crew: c.id, preview: true })}</span><strong>${escapeHTML(c.name)}</strong></button>`;
  }).join('');
}
function buildAccessoryPicker(b) {
  const picker = $('#accessory-picker');
  if (!picker || !b) return;
  const cur = accessoryFor(b);
  picker.innerHTML = ACCESSORIES.map(a => `<button type="button" data-acc="${a.id}" style="${paletteStyle(b)}" aria-pressed="${cur === a.id}" aria-label="Phụ kiện ${escapeHTML(a.name)}"><span class="shape-preview">${avatar({ ...b, accessory: a.id }, 'idle', 'expression-preview size-pick', { accessory: a.id, preview: true })}</span><strong>${escapeHTML(a.name)}</strong></button>`).join('');
}
function buildThemePicker(b) {
  const picker = $('#theme-picker');
  if (!picker || !b) return;
  const cur = themeFor(b);
  picker.innerHTML = THEME_FAMILIES.map(f => `<button type="button" data-theme-id="${f.id}" style="${paletteStyle(b)}" aria-pressed="${cur === f.id}" aria-label="Chủ đề ${escapeHTML(f.name)}"><span class="theme-swatch-mini card-stage" data-bot-theme="${f.id}">${avatar({ ...b, theme: f.id }, 'idle', 'expression-preview', { variant: 'stage', theme: f.id, preview: true })}</span><strong>${escapeHTML(f.name)}</strong></button>`).join('');
}
function buildExpressions() {
  const id = $('#expression-bot')?.value || (typeof selectedBot !== 'undefined' ? selectedBot : '') || bots[0]?.id || '';
  const b = getBot(id);
  if (!b) return;
  lab.botId = b.id;
  buildShapePicker(b);
  buildAccessoryPicker(b);
  buildThemePicker(b);
  const previewEl = $('#palette-preview');
  if (previewEl && b.palette) {
    const wcag = b.palette.inkContrast ? `WCAG AA: Chữ ${b.palette.inkContrast.toFixed(2)}:1 · Phụ ${b.palette.mutedContrast.toFixed(2)}:1` : 'Sáp · chữ · giấy';
    previewEl.innerHTML = `<span class="swatch" style="background:${b.color}"></span><span class="swatch" style="background:${b.palette.ink}"></span><span class="swatch" style="background:${b.palette.paper}"></span><span>${wcag}</span>`;
  }
  if ($('#expression-stage')) paint($('#expression-stage'), b);
  if ($('#group-preview')) paint($('#group-preview'), b);
  if ($('#lab-portrait')) $('#lab-portrait').innerHTML = avatar(b, b.state, 'lab-avatar');
  const tile = (s, i) => `<button class="expression-tile" data-expression="${s}" style="${paletteStyle(b)}" aria-pressed="${expressionFor(b) === s}" aria-label="Thử biểu cảm ${states[s][0]}"><span class="expression-index">${String(i + 1).padStart(2, '0')}</span>${avatar(b, s, 'expression-preview')}<strong>${states[s][0]}</strong><small>${states[s][2]}</small></button>`;
  if ($('#expression-grid')) $('#expression-grid').innerHTML = primaryStates.map(tile).join('');
  if ($('#expression-extras')) $('#expression-extras').innerHTML = extraStates.map((s, i) => tile(s, i + 8)).join('');
  const gpm = $('#group-preview-members');
  if (gpm) {
    const allBtn = `<button class="group-preview-member" data-speaker="" style="${paletteStyle(b)}" aria-label="Cả đội cùng làm" aria-pressed="${!activeGroup?.speaker}"><span>Cả đội cùng làm</span><small>Song song</small></button>`;
    gpm.innerHTML = allBtn + bots.map(x => `<button class="group-preview-member" data-speaker="${x.id}" style="${paletteStyle(x)}" aria-label="Cho ${x.name} nói" aria-pressed="false">${avatar(x)}<span>${x.name}</span><small>Đang lắng nghe</small></button>`).join('');
  }
  buildIdleGallery(b);
  studioTab(lab.idleTab);
  refreshLab(b);
  syncGroupAvatars();
}
function refreshLab(b) {
  if (!b || !$('#lab-state') || lab.botId !== b.id) return;
  const state = expressionFor(b);
  const moment = state === 'idle' && b.idle?.active ? idleCatalog[b.idle.active] : null;
  $('#lab-state').textContent = moment ? moment.label : states[state][1];
  if ($('#lab-description')) $('#lab-description').textContent = moment ? moment.detail : stateDetails[state];
  if ($('#lab-bot-name')) $('#lab-bot-name').textContent = b.name;
  $$('#idle-expression-grid [data-idle-choice]').forEach(t => t.setAttribute('aria-pressed', String(t.dataset.idleChoice === b.idle?.active)));
  $$('#expressions-dialog .expression-tile').forEach(t => t.setAttribute('aria-pressed', String(t.dataset.expression === state)));
  if ($('#lab-speech')) $('#lab-speech').textContent = lab.sample || (state === 'idle' && b.idle?.comment) || stateSummaries[state];
  const por = $('#lab-portrait .crew-avatar');
  if (por) syncAvatar(por, b);
  if ($('#group-preview')) $('#group-preview').hidden = lab.mode !== 'group';
  registerLiveRigs();
  wakeEyeLoop();
}
function stopLab(reset = true) {
  lab.timers.forEach(t => { clearTimeout(t); clearInterval(t); });
  lab.timers = [];
  if (lab.ownsGroup) {
    setGroupActivity(null);
    lab.ownsGroup = false;
  }
  const b = lab.botId ? getBot(lab.botId) : null;
  if (reset && b) {
    if (b.state !== 'idle') setState(b.id, 'idle', undefined, 0);
    if (typeof clearIdle === 'function') clearIdle(b, true);
  }
  lab.mode = null;
  lab.sample = '';
  if ($('#lab-stop')) $('#lab-stop').hidden = true;
  if ($('#lab-playback-note')) {
    $('#lab-playback-note').textContent = lab.idleTab ? 'Mắt tự nhìn quanh, cử chỉ và lời nhắn đổi ngẫu nhiên.' : 'Chọn biểu cảm để xem chuyển động.';
  }
  if (b) refreshLab(b);
}
function labDelay(ms, fn) { lab.timers.push(setTimeout(fn, ms)); }
function labWriting() {
  const b = getBot($('#expression-bot')?.value || lab.botId);
  if (!b) return;
  const sample = 'Từng ý tưởng nhỏ ghép lại thành bản nháp rõ ràng.';
  let position = 0;
  setState(b.id, 'writing', undefined, 0);
  const timer = setInterval(() => {
    if (b.state !== 'writing') { clearInterval(timer); return; }
    const delta = sample.slice(position, position + 2);
    position += 2;
    lab.sample = sample.slice(0, position);
    if ($('#lab-speech')) $('#lab-speech').textContent = lab.sample;
    notifyText(b.id, delta);
    if (position >= sample.length) {
      clearInterval(timer);
      labDelay(350, () => {
        if (b.state === 'writing') {
          lab.mode = null;
          setState(b.id, 'done', undefined, 0);
          if ($('#lab-stop')) $('#lab-stop').hidden = true;
          if ($('#lab-playback-note')) $('#lab-playback-note').textContent = 'Đã xong một lượt · tự trở về nghỉ.';
        }
      });
    }
  }, 65);
  lab.timers.push(timer);
}
function chooseExpression(state) {
  const b = getBot($('#expression-bot')?.value || lab.botId);
  if (!b) return;
  if (typeof run !== 'undefined' && run) {
    if (typeof toast === 'function') toast('Dừng lượt minh họa đang chạy trước khi thử biểu cảm.');
    return;
  }
  stopLab(false);
  studioTab(false);
  lab.botId = b.id;
  lab.lastChoice = state;
  lab.sample = '';
  if (state === 'mention') {
    if (['offline', 'error'].includes(b.state)) setState(b.id, 'idle');
    mentionBot(b.id);
  } else if (state === 'writing') {
    lab.mode = 'writing';
    if ($('#lab-stop')) $('#lab-stop').hidden = false;
    labWriting();
  } else if (state === 'group') {
    lab.mode = 'group';
    lab.ownsGroup = true;
    const gid = groups[0]?.id || 'group-1';
    setState(b.id, 'group', undefined, 0);
    setGroupActivity(gid, b.id);
    if ($('#lab-stop')) $('#lab-stop').hidden = false;
    if ($('#lab-playback-note')) $('#lab-playback-note').textContent = 'Chọn cả đội hoặc từng người đang nói.';
  } else {
    setState(b.id, state, undefined, state === 'thinking' ? 8 : 0);
  }
  refreshLab(b);
  if (['done', 'error', 'mention'].includes(state) && !motionPaused && !reduced.matches) {
    $$(`.crew-avatar[data-bot-avatar="${b.id}"]:not(.expression-preview)`).forEach(el => {
      (el.getAnimations?.({ subtree: true }) || []).forEach(a => {
        if (['crew-done', 'crew-error', 'crew-mention'].includes(a.animationName)) {
          a.currentTime = 0;
          a.play();
        }
      });
    });
  }
  if (typeof announce === 'function') announce(b.name + ': ' + states[state][0]);
}
function playLabSequence() {
  if (typeof run !== 'undefined' && run) {
    if (typeof toast === 'function') toast('Dừng lượt minh họa đang chạy trước khi thử biểu cảm.');
    return;
  }
  stopLab(false);
  studioTab(false);
  lab.botId = $('#expression-bot')?.value || null;
  lab.mode = 'sequence';
  lab.sample = '';
  setState(lab.botId, 'idle', undefined, 0);
  if ($('#lab-stop')) $('#lab-stop').hidden = false;
  if ($('#lab-playback-note')) $('#lab-playback-note').textContent = 'Nghỉ → chờ → nghĩ → làm → viết → xong';
  labDelay(650, () => setState(lab.botId, 'waiting', undefined, 0));
  labDelay(2300, () => setState(lab.botId, 'thinking', undefined, 1));
  labDelay(4800, () => setState(lab.botId, 'working', undefined, 3));
  labDelay(6800, labWriting);
}
function renderStudioSwatches() {
  const vib = $('#swatches-vibrant');
  const pap = $('#swatches-paper');
  if (vib) vib.innerHTML = VIBRANT_SWATCHES.map(c => `<button type="button" class="swatch" data-color="${c}" style="background:${c}" aria-label="Màu tươi ${c}"></button>`).join('');
  if (pap) pap.innerHTML = PAPER_SWATCHES.map(c => `<button type="button" class="swatch" data-color="${c}" style="background:${c}" aria-label="Màu giấy ${c}"></button>`).join('');
}
function openExpressionStudio(pick) {
  const id = pick && getBot(pick) ? pick : (selectedBot || bots[0]?.id || '');
  const target = getBot(id);
  if (!target) return;
  stopLab(false);
  if ($('#expression-bot')) {
    $('#expression-bot').innerHTML = bots.map(b => `<option value="${b.id}"${b.id === id ? ' selected' : ''}>${b.name}</option>`).join('');
    $('#expression-bot').value = id;
  }
  lab.lastChoice = expressionFor(target);
  if ($('#bot-color')) $('#bot-color').value = target.color;
  renderStudioSwatches();
  buildExpressions();
  $('#expressions-dialog')?.showModal();
}
$('#expression-bot')?.addEventListener('change', () => {
  stopLab();
  const b = getBot($('#expression-bot')?.value);
  if (!b) return;
  lab.lastChoice = expressionFor(b);
  if ($('#bot-color')) $('#bot-color').value = b.color;
  buildExpressions();
});
if ($('#bot-color')) {
  $('#bot-color').oninput = e => (window.ThycaShell || window.ThycaPrototype)?.setBotColor($('#expression-bot').value, e.target.value);
}
['#swatches-vibrant', '#swatches-paper'].forEach(sel => {
  $(sel)?.addEventListener('click', e => {
    const sw = e.target.closest('[data-color]');
    if (!sw) return;
    const id = $('#expression-bot').value;
    if ((window.ThycaShell || window.ThycaPrototype)?.setBotColor(id, sw.dataset.color)) buildExpressions();
  });
});
$('#shape-picker')?.addEventListener('click', e => {
  const btn = e.target.closest('[data-shape]');
  if (!btn) return;
  const id = $('#expression-bot').value;
  if ((window.ThycaShell || window.ThycaPrototype)?.setBotShape(id, btn.dataset.shape)) buildExpressions();
});
$('#accessory-picker')?.addEventListener('click', e => {
  const btn = e.target.closest('[data-acc]');
  if (!btn) return;
  const id = $('#expression-bot').value;
  if ((window.ThycaShell || window.ThycaPrototype)?.setBotAccessory(id, btn.dataset.acc)) buildExpressions();
});
$('#theme-picker')?.addEventListener('click', e => {
  const btn = e.target.closest('[data-theme-id]');
  if (!btn) return;
  const id = $('#expression-bot').value;
  if ((window.ThycaShell || window.ThycaPrototype)?.setBotTheme(id, btn.dataset.themeId)) buildExpressions();
});
['#expression-grid', '#expression-extras'].forEach(selector => $(selector)?.addEventListener('click', e => {
  const button = e.target.closest('button[data-expression]');
  if (button) chooseExpression(button.dataset.expression);
}));
if ($('#lab-play')) $('#lab-play').onclick = playLabSequence;
if ($('#lab-replay')) $('#lab-replay').onclick = () => (lab.idleTab ? chooseIdle(lab.lastIdle) : chooseExpression(lab.lastChoice));
if ($('#lab-stop')) $('#lab-stop').onclick = () => stopLab();
if ($('#lab-done')) $('#lab-done').onclick = () => chooseExpression('done');
if ($('#work-tab')) $('#work-tab').onclick = () => { stopLab(); studioTab(false); };
if ($('#idle-tab')) $('#idle-tab').onclick = () => { stopLab(); studioTab(true); if (typeof run === 'undefined' || !run) chooseIdle('look'); };
$$('.studio-tab').forEach(tab => tab.addEventListener('keydown', e => {
  if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
    e.preventDefault();
    const target = e.key === 'Home' ? $('#work-tab') : e.key === 'End' ? $('#idle-tab') : tab.id === 'work-tab' ? $('#idle-tab') : $('#work-tab');
    target?.focus();
    target?.click();
  }
}));
if ($('#idle-auto-toggle')) $('#idle-auto-toggle').onchange = e => setIdleOptions({ enabled: e.target.checked });
if ($('#idle-comments-toggle')) $('#idle-comments-toggle').onchange = e => setIdleOptions({ comments: e.target.checked });
if ($('#idle-random')) $('#idle-random').onclick = () => chooseIdle();
if ($('#idle-expression-grid')) {
  $('#idle-expression-grid').onclick = e => {
    const btn = e.target.closest('[data-idle-choice]');
    if (btn) chooseIdle(btn.dataset.idleChoice);
  };
}
if ($('#group-preview-members')) {
  $('#group-preview-members').onclick = e => {
    const btn = e.target.closest('[data-speaker]');
    if (!btn || lab.mode !== 'group') return;
    const gid = groups[0]?.id || 'group-1';
    const spk = btn.dataset.speaker || null;
    setGroupActivity(gid, spk);
    if (typeof announce === 'function') announce(spk ? getBot(spk).name + ' đang nói.' : 'Cả đội đang cùng làm.');
  };
}
$('#expressions-dialog')?.addEventListener('close', () => stopLab());
