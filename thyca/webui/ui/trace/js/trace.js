/* Turn-detail HTML renderer shared by the Usage workspace. Loaded once by
   the root SPA; it owns no page, route, request, list, or controller state. */
(function () {
  const STATUS_META = {
    completed: ['Thành công', 's-ok'],
    failed: ['Lỗi', 's-error'],
    context_limit: ['Hết ngữ cảnh', 's-warn'],
    loop_limit: ['Chạm giới hạn', 's-warn'],
  };
  const SIDE_KIND_LABEL = { naming: 'Đổi tên phiên', compaction: 'Nén ngữ cảnh' };
  const BUS_KIND_LABEL = { delegate: 'Giao việc', step: 'Nhịp làm việc', status: 'Trạng thái', help_request: 'Cần hỗ trợ', card: 'Thẻ hỏi' };
  const STOP_LABEL = { context_limit: 'Dừng — hết ngữ cảnh', loop_limit: 'Dừng — chạm giới hạn vòng lặp' };

  const fmtInt = n => (typeof n === 'number' && Number.isFinite(n)) ? n.toLocaleString('vi-VN') : '—';
  function fmtCost(usd) {
    if (typeof usd !== 'number' || !Number.isFinite(usd)) return '—';
    return '$' + usd.toFixed(usd >= 1 ? 2 : usd >= 0.01 ? 4 : 6);
  }
  function fmtDur(ms) {
    if (typeof ms !== 'number' || !Number.isFinite(ms)) return '—';
    if (ms < 1000) return fmtInt(ms) + ' ms';
    const s = ms / 1000;
    if (s < 60) return s.toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + ' giây';
    const m = Math.floor(s / 60);
    return m + ' ph ' + Math.round(s - m * 60) + ' giây';
  }
  function fmtDateTime(iso) {
    const d = new Date(iso);
    if (!Number.isFinite(d.getTime())) return '—';
    return d.getDate() + ' thg ' + (d.getMonth() + 1) + ', '
      + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }
  const fmtTime = iso => {
    const d = new Date(iso);
    return Number.isFinite(d.getTime())
      ? d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
      : '';
  };
  const safeJson = v => { try { return JSON.stringify(v, null, 2); } catch { return String(v); } };
  const safeDecode = s => { try { return decodeURIComponent(s); } catch { return s; } };
  const contentHtml = text => window.formatMarkdown
    ? window.formatMarkdown(text)
    : escapeHTML(String(text || '')).split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');

  const botFor = id => (id && getBot(id)) || null;
  const botName = id => { const b = botFor(id); return b ? b.name : (id || '—'); };
  const botSeal = id => {
    const b = botFor(id);
    return b ? avatar(b, b.state, 'expression-preview') : '';
  };
  const turnBotIds = t => {
    const ids = [];
    const push = id => { if (id && !ids.includes(id)) ids.push(id); };
    push(t.bot_id);
    (t.bot_breakdown || []).forEach(e => push(e.bot_id));
    return ids;
  };
  function statusChip(status) {
    const [label, cls] = STATUS_META[status] || [status || 'không rõ', ''];
    return `<span class="tl-chip ${cls}">${escapeHTML(label)}</span>`;
  }

  const isSyntheticStop = m => {
    const meta = m.meta || {};
    return (meta.status === 'context_limit' || meta.status === 'loop_limit') && meta.kind !== 'message';
  };
  const isBusRow = m => {
    const meta = m.meta || {};
    return meta.bus_event === true || BUS_KIND_LABEL[meta.kind] !== undefined;
  };
  function metaLineHtml(m, time) {
    const meta = m.meta || {};
    const kind = meta.kind;
    let label, cls = '';
    if (m.role === 'system') label = 'Hệ thống';
    else if (SIDE_KIND_LABEL[kind]) label = SIDE_KIND_LABEL[kind];
    else if (STOP_LABEL[meta.status]) { label = STOP_LABEL[meta.status]; cls = ' s-warn'; }
    else if (meta.bus_event === true || BUS_KIND_LABEL[kind]) label = BUS_KIND_LABEL[kind] || 'Sự kiện bot';
    else label = kind || m.role || 'meta';
    const who = meta.bot_id ? ' · ' + botName(meta.bot_id) : '';
    const preview = String(m.content ?? '').trim();
    return `<div class="tl-meta${cls}"><span class="tl-meta-kind">${escapeHTML(label)}${escapeHTML(who)}</span>` +
      (preview ? `<span class="tl-meta-text">${escapeHTML(preview.length > 240 ? preview.slice(0, 240) + '…' : preview)}</span>` : '') +
      `<time>${escapeHTML(time)}</time></div>`;
  }
  function userHtml(m, time) {
    const meta = m.meta || {};
    const to = meta.bot_id ? `<span class="tl-msg-meta">→ ${escapeHTML(botName(meta.bot_id))}</span>` : '';
    return `<div class="tl-item tl-item-user"><div class="tl-user"><p>${escapeHTML(String(m.content ?? '')).replace(/\n/g, '<br>')}</p><footer>${to}<time>${escapeHTML(time)}</time></footer></div></div>`;
  }
  function reasonHtml(m) {
    const parts = [];
    if (typeof m.reasoning === 'string' && m.reasoning.trim()) parts.push(m.reasoning);
    (Array.isArray(m.reasoning_details) ? m.reasoning_details : []).forEach(d => {
      if (!d || typeof d !== 'object') return;
      if (d.type === 'reasoning.text' && d.text) parts.push(String(d.text));
      else if (d.type === 'reasoning.summary' && d.summary) parts.push(String(d.summary));
      else if (d.type === 'reasoning.encrypted') parts.push('(đoạn suy nghĩ đã mã hoá)');
    });
    if (!parts.length) return '';
    return `<details class="tl-reason"><summary>Suy nghĩ</summary><pre class="tl-code">${escapeHTML(parts.join('\n\n'))}</pre></details>`;
  }
  function stepHtml(n, call, res) {
    const name = call.skill || call.name || 'tool';
    const flags = [];
    if (call.parse_error) flags.push('Lỗi cú pháp');
    if (res && res.meta && res.meta.is_error === true) flags.push('Thực thi lỗi');
    const metaBits = [];
    if (call.skill && call.name && call.skill !== call.name) metaBits.push('tool ' + call.name);
    if (res && res.meta && typeof res.meta.latency_ms === 'number') metaBits.push(fmtDur(res.meta.latency_ms));
    if (call.id) metaBits.push(call.id);
    const resErr = !!(res && res.meta && res.meta.is_error === true);
    const outHtml = res
      ? `<pre class="tl-code${resErr ? ' tl-code-err' : ''}">${escapeHTML(String(res.content ?? '')) || '(trống)'}</pre>`
      : '<p class="tl-step-meta">Chưa ghi nhận output.</p>';
    return `<li class="tl-step${flags.length ? ' s-error' : ''}"><span class="tl-step-num">${String(n).padStart(2, '0')}</span>
      <div><p class="tl-step-title">${escapeHTML(name)}${flags.map(f => ` <span class="tl-step-flag">${escapeHTML(f)}</span>`).join('')}</p>
      ${metaBits.length ? `<p class="tl-step-meta">${escapeHTML(metaBits.join(' · '))}</p>` : ''}
      <details class="tl-step-data"><summary>Tham số &amp; kết quả</summary>
        <p class="tl-io-label">Input</p><pre class="tl-code">${escapeHTML(safeJson(call.arguments ?? {}))}</pre>
        <p class="tl-io-label">Output</p>${outHtml}
        ${call.parse_error ? `<p class="tl-io-label">Parse error</p><pre class="tl-code tl-code-err">${escapeHTML(call.parse_error)}</pre>` : ''}
      </details></div></li>`;
  }
  function orphanStepHtml(n, m, time) {
    const resErr = !!(m.meta && m.meta.is_error === true);
    return `<li class="tl-step${resErr ? ' s-error' : ''}"><span class="tl-step-num">${String(n).padStart(2, '0')}</span>
      <div><p class="tl-step-title">Kết quả tool${resErr ? ' <span class="tl-step-flag">Thực thi lỗi</span>' : ''}</p>
      <p class="tl-step-meta">${escapeHTML([m.tool_call_id ? 'call ' + m.tool_call_id : '', time].filter(Boolean).join(' · '))}</p>
      <p class="tl-io-label">Output</p>
      <pre class="tl-code${resErr ? ' tl-code-err' : ''}">${escapeHTML(String(m.content ?? '')) || '(trống)'}</pre></div></li>`;
  }
  function timelineHtml(d) {
    const messages = Array.isArray(d.messages) ? d.messages : [];
    const results = new Map();
    const callIds = new Set();
    for (const m of messages) {
      if (!m) continue;
      if (m.role === 'tool' && m.tool_call_id) results.set(m.tool_call_id, m);
      (m.tool_calls || []).forEach(c => { if (c && c.id) callIds.add(c.id); });
    }
    let stepNo = 0;
    const out = [];
    for (const m of messages) {
      if (!m) continue;
      const meta = m.meta || {};
      const kind = meta.kind;
      const time = fmtTime(m.ts);
      if (m.role === 'system' || SIDE_KIND_LABEL[kind]) { out.push(metaLineHtml(m, time)); continue; }
      if (m.role === 'user') { out.push(userHtml(m, time)); continue; }
      if (m.role === 'tool') {
        if (m.tool_call_id && callIds.has(m.tool_call_id) && results.get(m.tool_call_id) === m) continue;
        out.push(`<ol class="tl-steps">${orphanStepHtml(++stepNo, m, time)}</ol>`);
        continue;
      }
      if (m.role === 'assistant') {
        if (isSyntheticStop(m) || isBusRow(m)) { out.push(metaLineHtml(m, time)); continue; }
        const botId = meta.bot_id || d.bot_id || '';
        const b = botFor(botId);
        const bits = [];
        if (meta.model) bits.push(meta.model);
        if (typeof meta.latency_ms === 'number') bits.push(fmtDur(meta.latency_ms));
        if (meta.usage && typeof meta.usage.total_tokens === 'number') bits.push(fmtInt(meta.usage.total_tokens) + ' tok');
        if (typeof meta.round === 'number' && meta.round > 1) bits.push('vòng ' + meta.round);
        if (meta.finish_reason && meta.finish_reason !== 'stop' && meta.finish_reason !== 'tool_calls') bits.push('kết thúc ' + meta.finish_reason);
        const errMeta = meta.error && typeof meta.error === 'object'
          ? `<p class="tl-error">${escapeHTML(meta.error.code || 'error')}: ${escapeHTML(meta.error.message || '')}</p>` : '';
        const body = contentHtml(m.content);
        const steps = (m.tool_calls || []).map(call => stepHtml(++stepNo, call, call.id ? results.get(call.id) : null));
        out.push(`<div class="tl-item tl-item-assistant"${b ? ` style="${paletteStyle(b)}"` : ''}>
          <header class="tl-byline">${botSeal(botId)}<span>${escapeHTML(botName(botId))}</span><time>${escapeHTML(time)}</time>${bits.length ? `<span class="tl-byline-meta">${escapeHTML(bits.join(' · '))}</span>` : ''}</header>
          ${errMeta}${reasonHtml(m)}
          ${body ? `<div class="tl-body markdown-body">${body}</div>` : ''}
          ${steps.length ? `<ol class="tl-steps">${steps.join('')}</ol>` : ''}
        </div>`);
        continue;
      }
      out.push(metaLineHtml(m, time));
    }
    return out.join('') || `<div class="empty-note"><strong>Lượt này không ghi message nào.</strong></div>`;
  }
  function detailHeadHtml(d) {
    const ids = turnBotIds(d);
    const byId = new Map((d.bot_breakdown || []).map(e => [e.bot_id, e]));
    const botsHtml = ids.length ? ids.map(id => {
      const e = byId.get(id);
      const bits = [];
      if (e && typeof e.requests === 'number' && e.requests) bits.push(fmtInt(e.requests) + ' req');
      if (e && typeof e.total_tokens === 'number') bits.push(fmtInt(e.total_tokens) + ' tok');
      const b = botFor(id);
      return `<span class="tl-bot"${b ? ` style="${paletteStyle(b)}"` : ''}>${botSeal(id)}<span>${escapeHTML(botName(id))}${bits.length ? ' · ' + bits.join(' · ') : ''}</span></span>`;
    }).join('') : '—';
    const rows = [
      ['Phiên', `<span class="tl-mono">${escapeHTML(d.session_id)}</span>`],
      ['Thời gian', `${escapeHTML(fmtDateTime(d.started_at))} → ${escapeHTML(fmtTime(d.ended_at) || '—')}`],
      ['Bot', `<span class="tl-bots">${botsHtml}</span>`],
      ['Model', escapeHTML(d.model || '—')],
      ['Gọi model', `${fmtInt(d.requests)} request · ${fmtInt(d.rounds)} vòng · ${fmtInt(d.tool_calls)} tool`],
      ['Token', `vào ${fmtInt(d.prompt_tokens)} · cache ${fmtInt(d.cached_tokens)} · ra ${fmtInt(d.completion_tokens)} · tổng ${fmtInt(d.total_tokens)}`],
      ['Chi phí', fmtCost(d.cost_usd)],
      ['Thời lượt', fmtDur(d.latency_ms)],
    ];
    if (d.trace_id) rows.push(['Trace', `<span class="tl-mono">${escapeHTML(d.trace_id)}</span>`]);
    if (d.work_id) rows.push(['Work', `<span class="tl-mono">${escapeHTML(d.work_id)}</span>`]);
    const err = d.error && (d.error.message || d.error.code)
      ? `<p class="tl-error-line"><b>Lỗi${d.error.code ? ' · ' + escapeHTML(d.error.code) : ''}</b>${escapeHTML(d.error.message || '')}</p>`
      : '';
    return `<article class="paper-card tl-detail-head">
      <div class="eyebrow">Lượt #${d.turn_index} ${statusChip(d.status)}</div>
      <h2 class="tl-detail-title">${escapeHTML(d.title || d.session_id)}</h2>
      <ul class="meta-list tl-detail-meta">${rows.map(([k, v]) => `<li><span>${k}</span><span>${v}</span></li>`).join('')}</ul>
      ${err}
    </article>`;
  }

  window.ThycaTraceDetail = Object.freeze({
    render(detail) {
      const value = detail && typeof detail === 'object' ? detail : {};
      return detailHeadHtml(value) + `<div class="tl-timeline">${timelineHtml(value)}</div>`;
    },
  });
})();
