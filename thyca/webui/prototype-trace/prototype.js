/* Thyca Trace prototype — scope-aware static rendering; no APIs or storage. */
(function () {
  "use strict";

  var BOTS = window.PROTO_BOTS;
  var BOT_IDS = Object.keys(BOTS);
  var ORIGINAL = window.PROTO_SESSIONS;
  var MAIN = document.getElementById("proto-view");
  var SCOPES = { overview: window.PROTO_SCOPE.build("") };
  BOT_IDS.forEach(function (id) { SCOPES[id] = window.PROTO_SCOPE.build(id); });
  var ACTIVE = SCOPES.overview;
  var SESSIONS = ACTIVE.sessions;
  var STATS = ACTIVE.stats;

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtInt(n) { return Number(n || 0).toLocaleString("vi-VN"); }
  function fmtCost(n) {
    if (n == null) { return "—"; }
    return "$" + Number(n).toFixed(n >= 1 ? 2 : n >= 0.01 ? 4 : 6);
  }
  function fmtDur(ms) {
    if (ms < 1000) { return fmtInt(ms) + " ms"; }
    var secs = ms / 1000;
    if (secs < 60) { return secs.toLocaleString("vi-VN", { maximumFractionDigits: 1 }) + " giây"; }
    return Math.floor(secs / 60) + " ph " + Math.round(secs % 60) + " giây";
  }
  function fmtDateTime(iso) {
    var d = new Date(iso);
    return String(d.getDate()).padStart(2, "0") + "/" + String(d.getMonth() + 1).padStart(2, "0") + " · " +
      String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }
  function fmtDay(key) { return key ? key.slice(8, 10) + "/" + key.slice(5, 7) : "—"; }
  function share(n, d) {
    if (!d || !Number.isFinite(n / d)) { return "0%"; }
    var p = n / d * 100;
    return (p >= 10 ? Math.round(p) : Math.round(p * 10) / 10).toLocaleString("vi-VN") + "%";
  }
  function knownCost(t) { return typeof t.knownCostUsd === "number" ? t.knownCostUsd : (typeof t.costUsd === "number" ? t.costUsd : null); }
  function turnCostLabel(t) {
    var cost = knownCost(t);
    if (cost == null) { return "chưa ghi nhận"; }
    return fmtCost(cost) + (t.costUsd == null ? " đã ghi nhận · chưa đủ giá" : "");
  }
  function costLabel(s) {
    var known = 0, any = false, partial = false;
    s.turns.forEach(function (t) {
      var c = knownCost(t);
      if (c != null) { known += c; any = true; }
      if (t.costUsd == null) { partial = true; }
    });
    if (!any) { return "Chưa ghi nhận"; }
    return fmtCost(known) + (partial ? " · có lượt chưa ghi nhận" : "");
  }
  function fmtRange(s) { return fmtDay(s.rangeStart) + " – " + fmtDay(s.rangeEnd); }

  /* Amber pebble and stack geometry are intentionally unchanged from the
   * original static prototype assets. */
  var STACK = { 2: [[32, 32, 68], [0, 0, 64]], 3: [[34, 34, 64], [0, 22, 54], [24, 0, 50]], over: [[34, 34, 64], [0, 22, 54]], coin: [50, 0, 42] };
  function glyphSvg(glyph) {
    return glyph === "quill"
      ? '<svg class="fill" viewBox="0 0 640 427" aria-hidden="true"><use href="#g-quill"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#g-' + glyph + '"/></svg>';
  }
  function pebble(id, size) {
    var b = BOTS[id];
    return '<span class="mk mk-peb" data-state="idle" style="--bot:' + b.color + (size ? ';--size:' + size : '') + '" role="img" aria-label="' + esc(b.name) + '">' +
      '<span class="peb-shadow"></span><span class="peb-float"><span class="peb-body"><span class="peb-core"></span></span><span class="glyph">' + glyphSvg(b.glyph) + '</span></span></span>';
  }
  function leadBot(session) {
    var t = session.turns[session.turns.length - 1];
    if (!t || !t.botBreakdown.length) { return session.bots[0]; }
    return t.botBreakdown.reduce(function (a, b) {
      return (a.promptTokens + a.completionTokens) >= (b.promptTokens + b.completionTokens) ? a : b;
    }).botId;
  }
  function avatarFor(session, size) {
    if (session.bots.length === 1) { return pebble(session.bots[0], size); }
    var lead = leadBot(session);
    var order = [lead].concat(session.bots.filter(function (id) { return id !== lead; }));
    var n = order.length, pos = STACK[n] || STACK.over, shown = order.slice(0, pos.length);
    var parts = shown.map(function (id, i) {
      var p = pos[i];
      return '<span class="gm-slot" style="left:' + p[0] + '%;top:' + p[1] + '%;width:' + p[2] + '%;height:' + p[2] + '%">' + pebble(id) + '</span>';
    }).reverse().join("");
    var c = STACK.coin;
    var coin = STACK[n] ? "" : '<span class="gm-coin" style="left:' + c[0] + '%;top:' + c[1] + '%;width:' + c[2] + '%;height:' + c[2] + '%">+' + (n - shown.length) + '</span>';
    return '<span class="gm"' + (size ? ' style="--gsize:' + size + '"' : '') + ' role="img" aria-label="Nhóm: ' +
      esc(order.map(function (id) { return BOTS[id].name; }).join(", ")) + '">' + coin + parts + '</span>';
  }

  function sessionById(id) { return ORIGINAL.find(function (s) { return s.id === id; }) || null; }
  function scopedSessionById(id) { return SESSIONS.find(function (s) { return s.id === id; }) || null; }
  function scopedTurn(sessionId, index) {
    var s = scopedSessionById(sessionId);
    if (!s) { return null; }
    var t = s.turns.find(function (turn) { return String(turn.index) === String(index); });
    return t ? { session: s, turn: t, source: t.sourceTurn } : null;
  }
  function sumSession(s) {
    return s.turns.reduce(function (a, t) {
      a.tokens += t.totalTokens; a.requests += t.requests; a.prompt += t.promptTokens;
      a.cached += t.cachedTokens; a.output += t.completionTokens;
      var c = knownCost(t); if (c != null) { a.cost += c; a.hasCost = true; }
      if (t.costUsd == null) { a.partialCost = true; }
      return a;
    }, { tokens: 0, requests: 0, prompt: 0, cached: 0, output: 0, cost: 0, hasCost: false, partialCost: false });
  }
  function statusChip(failed, scoped) {
    return failed
      ? '<span class="proto-chip-status is-err">! ' + (scoped ? "Lượt có lỗi" : "Có lượt lỗi") + '</span>'
      : '<span class="proto-chip-status is-ok">✓ Hoàn thành</span>';
  }
  function setNav() {
    var currentView = ROUTE.view;
    var overview = document.querySelector(".proto-overview-link");
    var nav = document.querySelectorAll(".proto-nav a");
    var isOverview = ROUTE.scope === "overview";
    if (isOverview) { overview.setAttribute("aria-current", "page"); } else { overview.removeAttribute("aria-current"); }
    nav.forEach(function (a) {
      if (a.getAttribute("data-bot") === ROUTE.scope) { a.setAttribute("aria-current", "page"); }
      else { a.removeAttribute("aria-current"); }
    });
    document.querySelectorAll(".proto-subnav a").forEach(function (a) {
      if (a.getAttribute("data-view") === currentView) { a.setAttribute("aria-current", "page"); }
      else { a.removeAttribute("aria-current"); }
    });
    var picker = document.querySelector(".scope-picker");
    if (picker) {
      picker.open = !window.matchMedia("(max-width: 56rem)").matches;
      var summary = picker.querySelector("summary");
      summary.textContent = "Chọn phạm vi · " + scopeTitle();
    }
  }

  function routeLink(scope, view) { return "#" + (scope === "overview" ? "overview" : "bot/" + encodeURIComponent(scope)) + "?view=" + view; }
  function routeHrefTurn(scope, sid, index) {
    return "#" + (scope === "overview" ? "overview" : "bot/" + encodeURIComponent(scope)) + "/turn/" + encodeURIComponent(sid) + "/" + encodeURIComponent(index);
  }
  function routeHrefSession(scope, sid) {
    return "#" + (scope === "overview" ? "overview" : "bot/" + encodeURIComponent(scope)) + "/session/" + encodeURIComponent(sid);
  }
  function scopeTitle() { return ROUTE.scope === "overview" ? "Tổng quan" : BOTS[ROUTE.scope].name; }
  function scopeHeader() {
    var scope = ROUTE.scope;
    var top = scope === "overview"
      ? '<div class="scope-heading-mark"><span class="scope-dot" aria-hidden="true">T</span></div>'
      : pebble(scope, "2.75rem");
    var label = scope === "overview" ? "Toàn bộ hoạt động của đội" : esc(BOTS[scope].role);
    var note = scope === "overview"
      ? "Mỗi phiên nhóm chỉ tính một lần vào tổng số phiên. “Phiên tham gia” đếm số phiên có bot đó; tổng tham gia có thể lớn hơn số phiên."
      : "Chỉ tính phần của " + esc(BOTS[scope].name) + ", gồm chat riêng và chat nhóm.";
    var tabs = [ ["usage", "Sử dụng token"], ["cost", "Chi phí"], ["trace", "Trace"] ].map(function (v) {
      return '<a href="' + routeLink(scope, v[0]) + '" data-view="' + v[0] + '">' + v[1] + '</a>';
    }).join("");
    var title = ROUTE.view === "usage" ? "Sử dụng token" : ROUTE.view === "cost" ? "Chi phí" : "Trace";
    return '<header class="proto-scope-header"><div class="proto-scope-brand">' + top +
      '<div><p class="proto-kicker">' + esc(scopeTitle()) + '</p><h1 id="view-h">' + title + '</h1>' +
      '<p class="proto-scope-subtitle">' + label + '</p></div></div>' +
      '<p class="proto-scope-note">' + note + '</p><nav class="proto-subnav" aria-label="Nội dung ' + esc(scopeTitle()) + '">' + tabs + '</nav></header>';
  }

  function barRow(o) {
    var body = '<div class="proto-row-head">' + (o.avatar ? '<span class="proto-row-avatar">' + o.avatar + '</span>' : '') +
      '<span class="proto-row-name">' + o.name + '</span><span class="proto-row-count num">' + o.count + '</span>' +
      '<span class="proto-row-share num">' + o.shareText + '</span></div>' +
      '<span class="proto-bar"><span class="proto-fill" style="width:' + (Number.isFinite(o.fillPct) ? Math.max(0, o.fillPct) : 0) + '%;background:' + (o.fillColor || "var(--color-accent)") + '"></span></span>' +
      (o.meta ? '<p class="proto-row-meta">' + o.meta + '</p>' : '');
    return '<li class="proto-row">' + (o.href ? '<a class="proto-row-link" href="' + o.href + '">' + body + '</a>' : body) + '</li>';
  }

  function chartSvg(kind) {
    var usage = kind === "usage", W = 640, H = usage ? 220 : 170, left = 8, bottom = 26, top = usage ? 12 : 22;
    var keys = STATS.dayKeys || [], max = 0;
    keys.forEach(function (k) {
      var d = STATS.daily[k] || {};
      var value = usage ? (d.input || 0) + (d.cache || 0) + (d.output || 0) : d.cost;
      if (typeof value === "number") { max = Math.max(max, value); }
    });
    var slot = keys.length ? (W - left * 2) / keys.length : W - left * 2;
    var bw = Math.min(44, slot * 0.62), bodyH = H - top - bottom;
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + (usage ? "Token" : "Chi phí") + ' theo ngày">' +
      '<line x1="' + left + '" y1="' + (top + bodyH) + '" x2="' + (W - left) + '" y2="' + (top + bodyH) + '" class="proto-grid"/>';
    svg += '<text x="' + left + '" y="' + (top - 2) + '" class="proto-axis">' + (usage ? "cao nhất " + fmtInt(max) : max ? "cao nhất " + fmtCost(max) : "Chưa ghi nhận") + '</text>';
    keys.forEach(function (key, i) {
      var d = STATS.daily[key] || {}, x = left + slot * i + (slot - bw) / 2, base = top + bodyH;
      if (usage) {
        var vals = [[d.input || 0, "proto-seg-input"], [d.cache || 0, "proto-seg-cache"], [d.output || 0, "proto-seg-output"]];
        var total = vals.reduce(function (a, v) { return a + v[0]; }, 0), y = base;
        vals.forEach(function (v) {
          var h = max ? v[0] / max * bodyH : 0;
          if (v[0] && h > 0) { y -= h; svg += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" class="' + v[1] + '"><title>' + fmtDay(key) + ': ' + fmtInt(v[0]) + '</title></rect>'; }
        });
        if (!total) { svg += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (base - 4) + '" class="proto-axis" text-anchor="middle">—</text>'; }
      } else {
        var v = d.cost;
        if (typeof v === "number") {
          var h = max ? v / max * bodyH : 0;
          if (h > 0) { svg += '<rect x="' + x.toFixed(1) + '" y="' + (base - h).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(2, h).toFixed(1) + '" class="proto-seg-cost"><title>' + fmtDay(key) + ': ' + fmtCost(v) + '</title></rect>'; }
          else { svg += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (base - 4) + '" class="proto-axis" text-anchor="middle">$0</text>'; }
        } else { svg += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (base - 4) + '" class="proto-axis" text-anchor="middle">—</text>'; }
      }
      svg += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 8) + '" class="proto-axis" text-anchor="middle">' + fmtDay(key) + '</text>';
    });
    return svg + '</svg>';
  }
  function usageView() {
    var s = STATS, botScope = !!ROUTE.botId, rows = Object.keys(s.perModel).sort(function (a, b) { return s.perModel[b].tokens - s.perModel[a].tokens; });
    var max = rows.reduce(function (m, id) { return Math.max(m, s.perModel[id].tokens); }, 0);
    var modelRows = rows.length ? rows.map(function (id) {
      var pm = s.perModel[id];
      return barRow({ name: esc(id), count: fmtInt(pm.tokens) + " token", shareText: share(pm.tokens, s.tokens), fillPct: max ? pm.tokens / max * 100 : 0,
        meta: fmtInt(pm.turns) + " lượt · " + fmtInt(pm.requests) + " lần gọi model" });
    }).join("") : '<li class="proto-empty">Chưa có lượt đóng góp trong phạm vi này.</li>';
    var botIds = BOT_IDS.slice().sort(function (a, b) { return s.perBot[b].tokens - s.perBot[a].tokens; });
    var botMax = botIds.reduce(function (m, id) { return Math.max(m, s.perBot[id].tokens); }, 0);
    var botRows = botIds.map(function (id) {
      var p = s.perBot[id];
      return barRow({ avatar: pebble(id, "2rem"), name: '<b>' + esc(BOTS[id].name) + '</b> · ' + esc(BOTS[id].role),
        count: fmtInt(p.tokens) + " token", shareText: share(p.tokens, s.tokens), fillPct: botMax ? p.tokens / botMax * 100 : 0,
        fillColor: BOTS[id].color, href: routeLink(id, "usage"),
        meta: fmtInt(p.turns) + " lượt đóng góp · " + fmtInt(p.participations) + " phiên tham gia" });
    }).join("");
    var summary = '<article class="proto-card proto-scope-summary" aria-label="Tóm tắt phạm vi">' +
      '<div><strong class="num">' + fmtInt(s.totalSessions) + '</strong><span>' + (botScope ? "phiên tham gia" : "phiên (mỗi phiên một lần)") + '</span></div>' +
      '<div><strong class="num">' + fmtInt(s.turns) + '</strong><span>lượt đóng góp</span></div>' +
      '<div><strong class="num">' + fmtInt(s.requests) + '</strong><span>lần gọi model</span></div>' +
      '<div><strong class="num">' + fmtInt(s.tokens) + '</strong><span>token</span></div>' +
      '<div><strong class="num">' + (s.costKnown == null ? "—" : fmtCost(s.costKnown)) + '</strong><span>chi phí đã ghi nhận</span></div></article>';
    var cards = [
      { label: "Tổng Input", value: s.prompt, foot: share(s.prompt, s.tokens) + " tổng token", tag: fmtInt(s.turns) + " lượt" },
      { label: "Tổng Cache read", value: s.cached, foot: share(s.cached, s.prompt) + " lượng input", tag: "cache ⊆ input" },
      { label: "Tổng Output", value: s.completion, foot: share(s.completion, s.tokens) + " tổng token", tag: fmtInt(s.turns) + " lượt" }
    ].map(function (c) { return '<article class="proto-card proto-summary"><header>' + c.label + '</header><strong class="num">' + fmtInt(c.value) + ' <small>token</small></strong><footer><span>' + c.foot + '</span><span>' + c.tag + '</span></footer></article>'; }).join("");
    return '<section aria-labelledby="view-h">' + summary +
      '<p class="proto-note proto-counting">' + (botScope ? 'Số liệu là phần đóng góp của ' + esc(BOTS[ROUTE.botId].name) + ' trong chat riêng và chat nhóm; phiên nhóm vẫn là một phiên.' : 'Mỗi phiên nhóm chỉ tính một lần; tổng “phiên tham gia” theo bot không được cộng thành tổng phiên.') + '</p>' +
      '<article class="proto-card proto-chart-card"><div class="proto-legend"><span class="lg-input">Vào (chưa cache)</span><span class="lg-cache">Cache read</span><span class="lg-output">Ra</span></div>' + chartSvg("usage") +
      '<p class="proto-note">' + (s.rangeStart ? fmtRange(s) : "Chưa có dữ liệu") + ' · Cache là tập con của Input; biểu đồ trừ Cache khỏi phần Vào để không cộng trùng.</p></article>' +
      '<div class="proto-summaries">' + cards + '</div>' +
      '<h2 class="proto-h2">Token theo mô hình</h2><ul class="proto-bars">' + modelRows + '</ul>' +
      (botScope ? '' : '<h2 class="proto-h2">Token theo bot</h2><p class="proto-note">Tỉ trọng mỗi bot tính trên tổng ' + fmtInt(s.tokens) + ' token.</p><ul class="proto-bars">' + botRows + '</ul>') +
      '</section>';
  }
  function costView() {
    var s = STATS, models = Object.keys(s.perModel).sort(function (a, b) { return (s.perModel[b].costKnown || 0) - (s.perModel[a].costKnown || 0); });
    var max = models.reduce(function (m, id) { return Math.max(m, s.perModel[id].costKnown || 0); }, 0);
    var rows = models.map(function (id) {
      var p = s.perModel[id], value = p.costKnown;
      return barRow({ name: esc(id), count: value == null ? "Chưa ghi nhận" : fmtCost(value),
        shareText: value != null && s.costKnown ? share(value, s.costKnown) : "—", fillPct: max && value != null ? value / max * 100 : 0,
        meta: "đã ghi nhận " + fmtInt(p.pricedTurns) + "/" + fmtInt(p.turns) + " lượt" + (p.pricedTurns < p.turns ? " · chỉ cộng phần chi phí đã ghi nhận" : "") });
    }).join("") || '<li class="proto-empty">Chưa có lượt đóng góp trong phạm vi này.</li>';
    var coverage = s.unpricedTurns.length ? '<ul>' + s.unpricedTurns.map(function (item) {
      return '<li>' + esc(item.session.title) + ' · Lượt ' + item.turn.index + ' · chưa đủ giá; phần thiếu không tính $0.</li>';
    }).join("") + '</ul>' : '<p>Tất cả lượt đóng góp đều có giá.</p>';
    var botIds = BOT_IDS.slice().sort(function (a, b) { return (s.perBot[b].costKnown || 0) - (s.perBot[a].costKnown || 0); });
    var botMax = botIds.reduce(function (m, id) { return Math.max(m, s.perBot[id].costKnown || 0); }, 0);
    var botRows = botIds.map(function (id) {
      var p = s.perBot[id], value = p.costKnown, partial = p.pricedTurns < p.turns;
      return barRow({ avatar: pebble(id, "2rem"), name: '<b>' + esc(BOTS[id].name) + '</b> · ' + esc(BOTS[id].role),
        count: value == null ? "Chưa ghi nhận" : fmtCost(value), shareText: value != null && s.costKnown ? share(value, s.costKnown) : "—",
        fillPct: botMax && value != null ? value / botMax * 100 : 0, fillColor: BOTS[id].color, href: routeLink(id, "cost"),
        meta: fmtInt(p.requests) + ' lần gọi model · số đã ghi nhận ' + fmtInt(p.pricedTurns) + '/' + fmtInt(p.turns) + ' lượt' + (partial ? ' · có lượt chưa ghi nhận' : '') });
    }).join("");
    var average = s.averageTurns ? fmtCost(s.averageCostUsd / s.averageTurns) : "—";
    return '<section aria-labelledby="view-h"><article class="proto-card proto-chart-card"><div class="proto-cost-top"><p>Tổng chi phí <strong class="num">' + (s.costKnown == null ? "—" : fmtCost(s.costKnown)) + '</strong> <span>(đã ghi nhận)</span></p>' +
      '<p>' + fmtInt(s.averageTurns) + ' lượt thành công đã có giá · trung bình ' + average + '/lượt</p></div>' + chartSvg("cost") +
      '<div class="proto-note"><p><b>Độ phủ giá:</b> ' + fmtInt(s.pricedTurns) + '/' + fmtInt(s.turns) + ' lượt có giá đầy đủ; “đã ghi nhận” gồm cả phần giá biết được của lượt chưa đủ giá. Lượt chưa có giá không phải $0.</p>' + coverage + '</div></article>' +
      '<h2 class="proto-h2">Chi phí theo mô hình</h2><ul class="proto-bars">' + rows + '</ul>' +
      (ROUTE.botId ? '' : '<h2 class="proto-h2">Chi phí theo bot</h2><p class="proto-note">Tỉ trọng tính trên tổng chi phí đã ghi nhận.</p><ul class="proto-bars proto-bot-cost">' + botRows + '</ul>') +
      '</section>';
  }

  function traceView() {
    var rows = [];
    SESSIONS.forEach(function (s) {
      s.turns.forEach(function (t) { rows.push({ session: s, turn: t }); });
    });
    rows.sort(function (a, b) { return Date.parse(b.turn.startedAt) - Date.parse(a.turn.startedAt); });
    var list = rows.map(function (item) {
      var s = item.session, t = item.turn, original = s.sourceSession;
      var failed = t.status === "failed";
      var avatar = ROUTE.botId ? pebble(ROUTE.botId, "2.5rem") : avatarFor(original, "2.5rem");
      var cost = turnCostLabel(t);
      return '<li><a class="proto-turn-row" href="' + routeHrefTurn(ROUTE.scope, s.id, t.index) + '"><time class="proto-turn-time">' + fmtDateTime(t.startedAt) + '</time>' +
        '<span class="proto-turn-avatar">' + avatar + '</span><span class="proto-turn-main"><strong>Lượt ' + t.index + ' · ' + esc(original.title) + '</strong>' +
        '<small>' + (original.kind === "group" ? "Chat nhóm" : "Chat riêng") + ' · ' + esc(t.model) + ' · ' + fmtInt(t.requests) + ' lần gọi · ' + fmtInt(t.totalTokens) + ' token · ' + cost + '</small></span>' + statusChip(failed, !!ROUTE.botId) + '</a></li>';
    }).join("") || '<li class="proto-empty">Chưa có lượt đóng góp trong phạm vi này.</li>';
    return '<section aria-labelledby="view-h"><article class="proto-card proto-trace-summary"><strong class="num">' + fmtInt(STATS.totalSessions) + '</strong><span>' + (ROUTE.botId ? "phiên tham gia" : "phiên duy nhất") + '</span><strong class="num">' + fmtInt(STATS.turns) + '</strong><span>lượt có đóng góp</span></article>' +
      '<p class="proto-note proto-counting">Mỗi phiên nhóm chỉ tính một lần vào tổng phiên; phiên tham gia theo bot là số phiên có bot đó, không cộng các bot vào tổng phiên.</p>' +
      '<ol class="proto-trace-list">' + list + '</ol></section>';
  }

  function stepHtml(n, st) {
    return '<li class="proto-step' + (st.type === "tool" ? " is-tool" : "") + '"><span class="proto-step-num num">' + String(n).padStart(2, "0") + '</span><div><p class="proto-step-title">' + esc(st.name) +
      (st.isError ? ' <span class="proto-chip-status is-err">lỗi</span>' : '') + '</p><p class="proto-row-meta">' + esc(st.type) + ' · ' + fmtDur(st.latencyMs) + '</p>' +
      (st.detail ? '<p class="proto-step-note">' + esc(st.detail) + '</p>' : '') + '</div></li>';
  }
  function turnDetails(source) {
    var steps = source.steps.map(function (st, i) { return stepHtml(i + 1, st); }).join("");
    var io = (source.user ? '<p class="proto-io-label">Input</p><pre class="proto-code">' + esc(source.user) + '</pre>' : '') +
      (source.assistant ? '<p class="proto-io-label">Output</p><pre class="proto-code">' + esc(source.assistant) + '</pre>' : '');
    return '<details class="proto-turn-details"><summary>Xem các bước &amp; dữ liệu</summary><p class="proto-row-meta">Ngữ cảnh toàn bộ lượt · dữ liệu dưới đây thuộc toàn bộ lượt, có thể gồm bot khác. Cache là tập con của Input.</p>' +
      '<p class="proto-row-meta">Vào ' + fmtInt(source.promptTokens) + ' (Cache ' + fmtInt(source.cachedTokens) + ') · Ra ' + fmtInt(source.completionTokens) + '</p>' +
      '<ol class="proto-steps">' + steps + '</ol>' + io + '</details>';
  }
  function turnDetail(scope, sid, index) {
    var found = scopedTurn(sid, index);
    if (!found) { return notFound(scope, "Không tìm thấy lượt đóng góp này trong phạm vi đã chọn."); }
    var s = found.session, t = found.turn, source = found.source, original = s.sourceSession;
    var avatar = ROUTE.botId ? pebble(ROUTE.botId, "3rem") : avatarFor(original, "3rem");
    var cost = turnCostLabel(t);
    var ctx = ROUTE.botId
      ? '<article class="proto-card proto-own-summary"><h2>Phần đóng góp của ' + esc(BOTS[ROUTE.botId].name) + '</h2><div><span><b>' + fmtInt(t.totalTokens) + '</b> token</span><span><b>' + fmtInt(t.requests) + '</b> lần gọi</span><span><b>' + cost + '</b> chi phí đã ghi nhận</span></div></article>'
      : '';
    return '<section><a class="proto-back" href="' + routeLink(ROUTE.scope, "trace") + '">‹ Trace</a>' +
      '<header class="proto-turn-detail-head">' + avatar + '<div><p class="proto-kicker">' + (ROUTE.botId ? "Lượt trong phạm vi bot" : "Lượt toàn đội") + '</p><h2>Lượt ' + t.index + ' · ' + esc(original.title) + '</h2>' +
      '<p class="proto-row-meta">' + (original.kind === "group" ? "Chat nhóm" : "Chat riêng") + ' · ' + esc(t.model) + ' · ' + fmtDateTime(t.startedAt) + ' · ' + fmtDur(t.latencyMs) + '</p></div>' + statusChip(t.status === "failed", !!ROUTE.botId) + '</header>' +
      ctx + '<article class="proto-card proto-turn"><p class="proto-turn-metrics">' + fmtInt(t.requests) + ' lần gọi · ' + fmtInt(t.promptTokens) + ' Input · ' + fmtInt(t.cachedTokens) + ' Cache · ' + fmtInt(t.completionTokens) + ' Output · ' + fmtInt(t.totalTokens) + ' token · ' + cost + '</p>' +
      (t.status === "failed" && source.error ? '<p class="proto-error">' + esc(source.error) + '</p>' : '') + turnDetails(source) + '</article>' +
      '<a class="proto-context-link" href="' + routeHrefSession(ROUTE.scope, original.id) + '">Xem cả phiên · ' + esc(original.title) + '</a></section>';
  }

  function sessionDetail(scope, sid) {
    var original = sessionById(sid);
    if (!original || (scope !== "overview" && original.bots.indexOf(scope) < 0)) { return notFound(scope, "Không tìm thấy phiên trong phạm vi đã chọn."); }
    var projected = SCOPES[scope].sessions.find(function (s) { return s.id === sid; });
    var own = projected ? sumSession(projected) : null, full = sumSession(original);
    var ownBox = scope === "overview" ? '' : '<article class="proto-card proto-own-summary"><h2>Đóng góp của ' + esc(BOTS[scope].name) + ' trong phiên này</h2><div><span><b>' + fmtInt(own.tokens) + '</b> token</span><span><b>' + fmtInt(own.requests) + '</b> lần gọi</span><span><b>' + (own.hasCost ? fmtCost(own.cost) + (own.partialCost ? " · chưa đủ giá" : "") : "Chưa ghi nhận") + '</b> chi phí đã ghi nhận</span></div></article><p class="proto-note">Phần dưới là ngữ cảnh toàn bộ phiên, không phải chỉ số của bot.</p>';
    var members = original.bots.map(function (id) {
      var metrics = { tokens: 0, req: 0, cost: 0, hasCost: false, partialCost: false };
      original.turns.forEach(function (turn) { turn.botBreakdown.filter(function (p) { return p.botId === id; }).forEach(function (p) {
        metrics.tokens += p.promptTokens + p.completionTokens; metrics.req += p.requests;
        if (typeof p.costUsd === "number") { metrics.cost += p.costUsd; metrics.hasCost = true; }
        else { metrics.partialCost = true; }
      }); });
      return '<span class="proto-member">' + pebble(id, "1.75rem") + '<span><b>' + esc(BOTS[id].name) + '</b> · ' + fmtInt(metrics.tokens) + ' token · ' + (metrics.hasCost ? fmtCost(metrics.cost) + (metrics.partialCost ? " đã ghi nhận · chưa đủ giá" : "") : "chưa ghi nhận") + '</span></span>';
    }).join("");
    var turns = original.turns.map(function (turn) {
      var parts = turn.botBreakdown.map(function (p) { return p.botId; });
      var av = parts.length === 1 ? pebble(parts[0], "2.25rem") : avatarFor({ bots: Array.from(new Set(parts)), turns: [turn] }, "2.25rem");
      return '<article class="proto-card proto-turn"><header class="proto-turn-head">' + av + '<div><h3>Lượt ' + turn.index + '</h3><p class="proto-row-meta">' + esc(turn.model) + ' · ' + fmtInt(turn.requests) + ' lần gọi · ' + fmtInt(turn.totalTokens) + ' token · ' + turnCostLabel(turn) + '</p></div>' + statusChip(turn.status === "failed", false) + '</header>' +
        (turn.status === "failed" && turn.error ? '<p class="proto-error">' + esc(turn.error) + '</p>' : '') + turnDetails(turn) + '</article>';
    }).join("");
    return '<section><a class="proto-back" href="' + routeLink(scope, "trace") + '">‹ Trace</a><header class="proto-session-detail-head">' + avatarFor(original, "3rem") +
      '<div><p class="proto-kicker">Toàn bộ phiên</p><h2>' + esc(original.title) + '</h2><p class="proto-row-meta">' + esc(original.id) + ' · ' + (original.kind === "group" ? "Chat nhóm · " + original.bots.length + " bot" : "Chat riêng · 1 bot") + ' · ' + fmtDateTime(original.startedAt) + ' → ' + fmtDateTime(original.turns[original.turns.length - 1].endedAt || original.turns[original.turns.length - 1].startedAt) + '</p></div></header>' +
      ownBox + '<div class="proto-members">' + members + '</div><article class="proto-card proto-strip"><div><strong>' + fmtInt(original.turns.length) + '</strong><span>lượt toàn phiên</span></div><div><strong>' + fmtInt(full.requests) + '</strong><span>lần gọi</span></div><div><strong>' + fmtInt(full.tokens) + '</strong><span>token toàn phiên</span></div><div><strong>' + (full.hasCost ? fmtCost(full.cost) + (full.partialCost ? " · chưa đủ giá" : "") : "Chưa ghi nhận") + '</strong><span>chi phí đã ghi nhận</span></div></article>' +
      '<p class="proto-note">Dữ liệu dưới đây thuộc toàn bộ lượt và có thể gồm các bot khác.</p><div class="proto-turns">' + turns + '</div></section>';
  }
  function notFound(scope, message) {
    return '<section><a class="proto-back" href="' + routeLink(scope, "trace") + '">‹ Trace</a><article class="proto-card"><p>' + esc(message) + '</p></article></section>';
  }

  var ROUTE = { scope: "overview", botId: "", view: "usage", type: "view", sessionId: "", turnIndex: "" };
  function parseRoute() {
    var raw = location.hash || "#overview", text = raw.slice(1), legacy = { usage: "usage", cost: "cost", sessions: "trace", bots: "usage" };
    var q = text.indexOf("?");
    var path = q < 0 ? text : text.slice(0, q);
    var params = new URLSearchParams(q < 0 ? "" : text.slice(q + 1));
    var parts = path.split("/").map(function (p) { try { return decodeURIComponent(p); } catch (_) { return p; } });
    var scope = "overview", view = params.get("view") || "usage", type = "view", sid = "", index = "";
    if (legacy[path] || parts[0] === "session") {
      scope = "overview";
      if (parts[0] === "session" && parts[1]) { type = "session"; sid = parts[1]; view = "trace"; }
      else { view = legacy[path] || "usage"; }
    } else if (parts[0] === "bot") {
      scope = parts[1] || "";
      if (parts[2] === "turn") { type = "turn"; sid = parts[3] || ""; index = parts[4] || ""; view = "trace"; }
      else if (parts[2] === "session") { type = "session"; sid = parts[3] || ""; view = "trace"; }
    } else if (parts[0] === "overview") {
      if (parts[1] === "turn") { type = "turn"; sid = parts[2] || ""; index = parts[3] || ""; view = "trace"; }
      else if (parts[1] === "session") { type = "session"; sid = parts[2] || ""; view = "trace"; }
    } else { scope = "overview"; view = "usage"; }
    if (scope !== "overview" && !Object.prototype.hasOwnProperty.call(BOTS, scope)) { scope = "overview"; type = "invalid"; }
    if (["usage", "cost", "trace"].indexOf(view) < 0) { view = "usage"; }
    return { scope: scope, botId: scope === "overview" ? "" : scope, view: view, type: type, sessionId: sid, turnIndex: index };
  }
  function render() {
    ROUTE = parseRoute();
    ACTIVE = SCOPES[ROUTE.scope] || SCOPES.overview;
    SESSIONS = ACTIVE.sessions; STATS = ACTIVE.stats;
    var title = ROUTE.view === "usage" ? "Sử dụng token" : ROUTE.view === "cost" ? "Chi phí" : "Trace";
    var header = scopeHeader();
    var body;
    if (ROUTE.type === "invalid") { body = notFound("overview", "Bot không tồn tại trong mẫu."); }
    else if (ROUTE.type === "turn") { body = turnDetail(ROUTE.scope, ROUTE.sessionId, ROUTE.turnIndex); }
    else if (ROUTE.type === "session") { body = sessionDetail(ROUTE.scope, ROUTE.sessionId); }
    else { body = ROUTE.view === "usage" ? usageView() : ROUTE.view === "cost" ? costView() : traceView(); }
    MAIN.innerHTML = header + body;
    setNav();
    document.title = "Thyca · " + scopeTitle() + " · " + title;
  }

  function buildSidebar() {
    document.getElementById("scope-bot-count").textContent = fmtInt(BOT_IDS.length);
    var nav = document.getElementById("scope-nav");
    nav.innerHTML = BOT_IDS.map(function (id) {
      return '<a href="#bot/' + encodeURIComponent(id) + '?view=usage" data-bot="' + id + '">' + pebble(id, "2.75rem") +
        '<span class="scope-bot-copy"><b>' + esc(BOTS[id].name) + '</b><small>' + esc(BOTS[id].role) + '</small></span></a>';
    }).join("");
    var overview = document.querySelector(".proto-overview-link");
    overview.addEventListener("click", function (e) { e.preventDefault(); location.hash = routeLink("overview", ROUTE.view); });
    nav.addEventListener("click", function (e) {
      var a = e.target.closest("a[data-bot]");
      if (!a) { return; }
      e.preventDefault(); location.hash = routeLink(a.getAttribute("data-bot"), ROUTE.view);
    });
    window.addEventListener("resize", function () {
      var picker = document.querySelector(".scope-picker");
      if (picker) { picker.open = !window.matchMedia("(max-width: 56rem)").matches; }
    });
  }
  buildSidebar();
  window.addEventListener("hashchange", render);
  render();
})();
