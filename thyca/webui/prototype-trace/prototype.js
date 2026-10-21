/* Thyca Trace prototype — rendering + hash routing (plain script, file:// safe).
 * Every number shown is computed from window.PROTO_SESSIONS by computeStats()
 * exactly once; views only read the STATS object. No totals are hardcoded.
 *
 * Session-counting rule (holds in code and in every label/note):
 * - totalSessions = sessions.length (each group session counted ONCE).
 * - Per-bot "phiên tham gia" counts sessions whose bots[] include that bot;
 *   the per-bot sum may exceed totalSessions and is NEVER added into it.
 * - Per-bot tokens/cost/requests/turns come ONLY from that bot's
 *   botBreakdown parts; per-bot token share denominator = total tokens.
 */
(function () {
  "use strict";

  var BOTS = window.PROTO_BOTS;
  var SESSIONS = window.PROTO_SESSIONS;
  var BOT_IDS = Object.keys(BOTS);

  /* ---------------- small helpers ---------------- */

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmtInt(n) {
    return Number(n).toLocaleString("vi-VN");
  }

  /* Format dollars like the dashboard's formatCost: trim to a sensible
   * precision, never invent cents for unpriced (null -> em dash). */
  function fmtCost(usd) {
    if (usd == null) { return "—"; }
    var f = usd >= 1 ? 2 : usd >= 0.01 ? 4 : 6;
    return "$" + Number(usd).toFixed(f);
  }

  function fmtDur(ms) {
    if (ms < 1000) { return fmtInt(ms) + " ms"; }
    var s = ms / 1000;
    if (s < 60) {
      return s.toLocaleString("vi-VN", { maximumFractionDigits: 1 }) + " giây";
    }
    var m = Math.floor(s / 60);
    var rest = Math.round(s - m * 60);
    return m + " ph " + rest + " giây";
  }

  function fmtDateTime(iso) {
    var d = new Date(iso);
    var hh = String(d.getHours()).padStart(2, "0");
    var mm = String(d.getMinutes()).padStart(2, "0");
    return d.getDate() + " thg " + (d.getMonth() + 1) + ", " + hh + ":" + mm;
  }

  function fmtDay(key) {
    var p = key.split("-");
    return p[2] + "/" + p[1];
  }

  /* ---------------- avatars ----------------
   * Single pebble markup follows the .mk-peb CSS selectors from the
   * reference file (shadow + float > body > core + glyph). Group avatar
   * uses the reference "Chồng dấu" (stack) slot geometry with pebbles
   * inside; topmost slot = lead bot (most recently active). */

  var STACK = {
    2: [[32, 32, 68], [0, 0, 64]],
    3: [[34, 34, 64], [0, 22, 54], [24, 0, 50]],
    over: [[34, 34, 64], [0, 22, 54]],
    coin: [50, 0, 42]
  };

  function glyphSvg(glyph) {
    if (glyph === "quill") {
      return '<svg class="fill" viewBox="0 0 640 427" aria-hidden="true"><use href="#g-quill"/></svg>';
    }
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#g-' + glyph + '"/></svg>';
  }

  function pebble(botId, size) {
    var b = BOTS[botId];
    var style = "--bot:" + b.color + (size ? ";--size:" + size : "");
    return '<span class="mk mk-peb" data-state="idle" style="' + style + '" role="img" aria-label="' + esc(b.name) + '">' +
      '<span class="peb-shadow"></span>' +
      '<span class="peb-float"><span class="peb-body"><span class="peb-core"></span></span>' +
      '<span class="glyph">' + glyphSvg(b.glyph) + "</span></span></span>";
  }

  function sessionOrder(session) {
    var lead = leadBot(session);
    var rest = session.bots.filter(function (id) { return id !== lead; });
    return [lead].concat(rest);
  }

  function leadBot(session) {
    var last = session.turns[session.turns.length - 1];
    var best = null, bestTok = -1;
    last.botBreakdown.forEach(function (p) {
      var tok = p.promptTokens + p.completionTokens;
      if (tok > bestTok) { bestTok = tok; best = p.botId; }
    });
    return best || session.bots[0];
  }

  function avatarFor(session, size) {
    if (session.bots.length === 1) { return pebble(session.bots[0], size); }
    var order = sessionOrder(session);
    var n = order.length;
    var pos = STACK[n] || STACK.over;
    var shown = order.slice(0, pos.length);
    var parts = shown.map(function (id, i) {
      var s = pos[i];
      return '<span class="gm-slot" style="left:' + s[0] + "%;top:" + s[1] + "%;width:" + s[2] + "%;height:" + s[2] + '%">' + pebble(id) + "</span>";
    }).reverse().join(""); /* first (lead) bot renders last -> on top */
    var coin = "";
    if (!STACK[n]) {
      var c = STACK.coin;
      coin = '<span class="gm-coin" style="left:' + c[0] + "%;top:" + c[1] + "%;width:" + c[2] + "%;height:" + c[2] + '%">+' + (n - shown.length) + "</span>";
    }
    var gsize = size ? ' style="--gsize:' + size + '"' : "";
    return '<span class="gm"' + gsize + ' role="img" aria-label="Nhóm: ' +
      esc(order.map(function (id) { return BOTS[id].name; }).join(", ")) + '">' + coin + parts + "</span>";
  }

  /* ---------------- aggregation (computed once) ---------------- */

  function blankBot() {
    return { participations: 0, turns: 0, requests: 0, prompt: 0, cached: 0, completion: 0, tokens: 0, costKnown: 0, pricedTurns: 0, errors: 0, sessions: [] };
  }

  function computeStats() {
    var st = {
      totalSessions: SESSIONS.length,
      groupSessions: 0, dmSessions: 0,
      turns: 0, requests: 0, prompt: 0, cached: 0, completion: 0, tokens: 0,
      costKnown: 0, pricedTurns: 0,
      unpricedTurns: [], failedTurns: [],
      perBot: {}, perModel: {}, daily: {}, dayKeys: [],
      rangeStart: null, rangeEnd: null
    };
    BOT_IDS.forEach(function (id) { st.perBot[id] = blankBot(); });

    SESSIONS.forEach(function (s) {
      if (s.kind === "group") { st.groupSessions++; } else { st.dmSessions++; }
      s.bots.forEach(function (id) {
        var pb = st.perBot[id];
        pb.participations++;
        if (pb.sessions.indexOf(s.id) < 0) { pb.sessions.push(s.id); }
      });
      s.turns.forEach(function (t) {
        st.turns++; st.requests += t.requests;
        st.prompt += t.promptTokens; st.cached += t.cachedTokens;
        st.completion += t.completionTokens; st.tokens += t.totalTokens;
        var priced = typeof t.costUsd === "number";
        if (priced) { st.costKnown += t.costUsd; st.pricedTurns++; }
        else { st.unpricedTurns.push({ session: s, turn: t }); }
        if (t.status === "failed") { st.failedTurns.push({ session: s, turn: t }); }

        var day = t.startedAt.slice(0, 10);
        if (!st.daily[day]) { st.daily[day] = { input: 0, cache: 0, output: 0, cost: 0, turns: 0 }; }
        var d = st.daily[day];
        d.input += t.promptTokens - t.cachedTokens; /* uncached input only: cache is a subset */
        d.cache += t.cachedTokens;
        d.output += t.completionTokens;
        if (priced) { d.cost += t.costUsd; }
        d.turns++;
        if (!st.rangeStart || day < st.rangeStart) { st.rangeStart = day; }
        if (!st.rangeEnd || day > st.rangeEnd) { st.rangeEnd = day; }

        if (!st.perModel[t.model]) {
          st.perModel[t.model] = { turns: 0, pricedTurns: 0, requests: 0, tokens: 0, costKnown: 0 };
        }
        var pm = st.perModel[t.model];
        pm.turns++; pm.requests += t.requests; pm.tokens += t.totalTokens;
        if (priced) { pm.pricedTurns++; pm.costKnown += t.costUsd; }

        var seen = {};
        t.botBreakdown.forEach(function (p) {
          var pb = st.perBot[p.botId];
          pb.requests += p.requests;
          pb.prompt += p.promptTokens; pb.cached += p.cachedTokens;
          pb.completion += p.completionTokens;
          pb.tokens += p.promptTokens + p.completionTokens;
          if (typeof p.costUsd === "number") { pb.costKnown += p.costUsd; }
          if (!seen[p.botId]) {
            seen[p.botId] = true; pb.turns++;
            if (priced) { pb.pricedTurns++; }
          }
          if (t.status === "failed" && !p._errCounted) { p._errCounted = true; pb.errors++; }
        });
      });
    });

    /* Round cost sums to 6dp so displayed sums match their parts. */
    function round6(n) { return Math.round(n * 1e6) / 1e6; }
    st.costKnown = round6(st.costKnown);
    BOT_IDS.forEach(function (id) { st.perBot[id].costKnown = round6(st.perBot[id].costKnown); });
    Object.keys(st.perModel).forEach(function (m) { st.perModel[m].costKnown = round6(st.perModel[m].costKnown); });

    /* Day axis: 10 days ending on the newest turn day. */
    var end = new Date(st.rangeEnd + "T12:00:00+07:00");
    for (var i = 9; i >= 0; i--) {
      var dd = new Date(end.getTime() - i * 86400000);
      var key = dd.getFullYear() + "-" + String(dd.getMonth() + 1).padStart(2, "0") + "-" + String(dd.getDate()).padStart(2, "0");
      st.dayKeys.push(key);
      if (!st.daily[key]) { st.daily[key] = { input: 0, cache: 0, output: 0, cost: 0, turns: 0 }; }
    }
    return st;
  }

  var STATS = computeStats();

  function sessionById(id) {
    for (var i = 0; i < SESSIONS.length; i++) {
      if (SESSIONS[i].id === id) { return SESSIONS[i]; }
    }
    return null;
  }

  function sessionHasError(s) {
    return s.turns.some(function (t) { return t.status === "failed"; });
  }

  function sessionCost(s) {
    var known = 0, priced = 0;
    s.turns.forEach(function (t) {
      if (typeof t.costUsd === "number") { known += t.costUsd; priced++; }
    });
    return { known: Math.round(known * 1e6) / 1e6, priced: priced, total: s.turns.length };
  }

  function sessionSpan(s) {
    var min = null, max = 0;
    s.turns.forEach(function (t) {
      var a = new Date(t.startedAt).getTime();
      var b = new Date(t.endedAt).getTime();
      if (min === null || a < min) { min = a; }
      if (b > max) { max = b; }
    });
    return max - min;
  }

  function costLabel(s) {
    var c = sessionCost(s);
    if (c.priced === 0) { return "Chưa ghi nhận"; }
    if (c.priced < c.total) { return fmtCost(c.known) + " · còn lượt chưa ghi nhận"; }
    return fmtCost(c.known);
  }

  function share(num, den) {
    if (!den) { return "0%"; }
    var p = (num / den) * 100;
    return (p >= 10 ? Math.round(p) : Math.round(p * 10) / 10).toLocaleString("vi-VN") + "%";
  }

  /* ---------------- shared row builders ---------------- */

  function barRow(o) {
    /* o: {avatar, name, count, shareText, fillPct, fillColor, meta} */
    return '<li class="proto-row">' +
      '<div class="proto-row-head">' +
      (o.avatar ? '<span class="proto-row-avatar">' + o.avatar + "</span>" : "") +
      '<span class="proto-row-name">' + o.name + "</span>" +
      '<span class="proto-row-count num">' + o.count + "</span>" +
      '<span class="proto-row-share num">' + o.shareText + "</span>" +
      "</div>" +
      '<span class="proto-bar" role="img" aria-label="' + esc(o.ariaLabel || (o.name + " " + o.shareText)) + '">' +
      '<span class="proto-fill" style="width:' + o.fillPct + "%" + (o.fillColor ? ";background:" + o.fillColor : "") + '"></span></span>' +
      (o.meta ? '<p class="proto-row-meta">' + o.meta + "</p>" : "") +
      "</li>";
  }

  function statusChip(failed) {
    return failed
      ? '<span class="proto-chip-status is-err">! Có lỗi</span>'
      : '<span class="proto-chip-status is-ok">✓ Hoàn thành</span>';
  }

  var COUNT_NOTE = "Mỗi phiên nhóm chỉ tính một lần vào tổng số phiên. " +
    "“Phiên tham gia” của từng bot đếm số phiên có bot đó — cộng các bot lại có thể vượt tổng số phiên.";

  /* ---------------- view A: usage ---------------- */

  function usageChart() {
    var W = 640, H = 220, padL = 8, padB = 26, padT = 12;
    var keys = STATS.dayKeys;
    var max = 1;
    keys.forEach(function (k) {
      var d = STATS.daily[k];
      max = Math.max(max, d.input + d.cache + d.output);
    });
    var slot = (W - padL * 2) / keys.length;
    var bw = Math.min(44, slot * 0.62);
    var bodyH = H - padT - padB;
    var out = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Token theo ngày, xếp chồng Vào, Cache và Ra">';
    out += '<line x1="' + padL + '" y1="' + (padT + bodyH) + '" x2="' + (W - padL) + '" y2="' + (padT + bodyH) + '" class="proto-grid"/>';
    out += '<text x="' + padL + '" y="' + (padT - 2) + '" class="proto-axis">cao nhất ' + fmtInt(max) + "</text>";
    keys.forEach(function (k, i) {
      var d = STATS.daily[k];
      var tot = d.input + d.cache + d.output;
      var x = padL + slot * i + (slot - bw) / 2;
      var base = padT + bodyH;
      function seg(v, cls, y0) {
        var h = tot ? (v / max) * bodyH : 0;
        var y = y0 - h;
        out += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" class="' + cls + '"><title>' + fmtDay(k) + ": " + fmtInt(v) + "</title></rect>";
        return y;
      }
      var y = base;
      if (d.output) { y = seg(d.output, "proto-seg-output", y); }
      if (d.cache) { y = seg(d.cache, "proto-seg-cache", y); }
      if (d.input) { y = seg(d.input, "proto-seg-input", y); }
      if (!tot) {
        out += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (base - 4) + '" class="proto-axis proto-empty" text-anchor="middle">—</text>';
      }
      out += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 8) + '" class="proto-axis" text-anchor="middle">' + fmtDay(k) + "</text>";
    });
    return out + "</svg>";
  }

  function renderUsage() {
    var s = STATS;
    var uncached = s.prompt - s.cached;
    var cards = [
      { label: "Tổng Input", value: fmtInt(s.prompt), unit: "token", foot: share(s.prompt, s.tokens) + " tổng token", tag: fmtInt(s.turns) + " lượt" },
      { label: "Tổng Cache read", value: fmtInt(s.cached), unit: "token", foot: share(s.cached, s.prompt) + " lượng input", tag: "cache ⊆ input" },
      { label: "Tổng Output", value: fmtInt(s.completion), unit: "token", foot: share(s.completion, s.tokens) + " tổng token", tag: fmtInt(s.turns) + " lượt" }
    ].map(function (c) {
      return '<article class="proto-card proto-summary"><header>' + esc(c.label) + "</header>" +
        '<strong class="num">' + c.value + " <small>" + esc(c.unit) + "</small></strong>" +
        '<footer><span>' + esc(c.foot) + "</span><span>" + esc(c.tag) + "</span></footer></article>";
    }).join("");

    var models = Object.keys(s.perModel).sort(function (a, b) { return s.perModel[b].tokens - s.perModel[a].tokens; });
    var maxTok = Math.max.apply(null, models.map(function (m) { return s.perModel[m].tokens; }));
    var modelRows = models.map(function (m) {
      var pm = s.perModel[m];
      return barRow({
        name: esc(m), count: fmtInt(pm.tokens) + " token", shareText: share(pm.tokens, s.tokens),
        fillPct: Math.max(1.5, (pm.tokens / maxTok) * 100),
        meta: fmtInt(pm.turns) + " lượt · " + fmtInt(pm.requests) + " lần gọi model"
      });
    }).join("");

    var botIds = BOT_IDS.slice().sort(function (a, b) { return s.perBot[b].tokens - s.perBot[a].tokens; });
    var maxBot = Math.max.apply(null, botIds.map(function (id) { return s.perBot[id].tokens; }));
    var botRows = botIds.map(function (id) {
      var pb = s.perBot[id];
      return barRow({
        avatar: pebble(id, "2rem"), name: "<b>" + esc(BOTS[id].name) + "</b> · " + esc(BOTS[id].role),
        count: fmtInt(pb.tokens) + " token", shareText: share(pb.tokens, s.tokens),
        fillPct: Math.max(1.5, (pb.tokens / maxBot) * 100), fillColor: BOTS[id].color,
        meta: "tính từ " + fmtInt(pb.turns) + " lượt đóng góp · " + fmtInt(pb.participations) + " phiên tham gia"
      });
    }).join("");

    return '<section aria-labelledby="usage-h">' +
      '<header class="proto-heading"><div><p class="proto-kicker">Nguyên mẫu redesign · số liệu mẫu</p>' +
      '<h1 id="usage-h">Sử dụng token</h1></div>' +
      '<label class="proto-period">Khoảng <select aria-label="Khoảng thời gian" disabled><option>30 ngày qua</option></select></label></header>' +
      '<article class="proto-card proto-chart-card"><div class="proto-legend" aria-label="Chú giải">' +
      '<span class="lg-input">Vào (chưa cache)</span><span class="lg-cache">Cache read</span><span class="lg-output">Ra</span></div>' +
      usageChart() +
      '<p class="proto-note">Cột mỗi ngày xếp chồng: Vào chưa cache + Cache read + Ra. Cache là tập con của Input nên phần “Vào” đã trừ Cache, không cộng trùng.</p></article>' +
      '<div class="proto-summaries">' + cards + "</div>" +
      '<article class="proto-card proto-total"><div><strong class="num">' + fmtInt(s.tokens) + ' <small>token</small></strong>' +
      "<p>" + fmtInt(s.turns) + " lượt · " + fmtDay(s.rangeStart) + " – " + fmtDay(s.rangeEnd) + " · " + fmtInt(s.totalSessions) + " phiên (mỗi phiên nhóm tính một lần)</p></div>" +
      '<p class="proto-note">' + esc(COUNT_NOTE) + "</p></article>" +
      '<h2 class="proto-h2">Token theo mô hình</h2><ul class="proto-bars">' + modelRows + "</ul>" +
      '<h2 class="proto-h2">Token theo bot</h2>' +
      '<p class="proto-note">Tỉ trọng mỗi bot = token từ các phần botBreakdown của bot đó / tổng token (' + fmtInt(s.tokens) + "). Cộng tỉ trọng các bot = 100%.</p>" +
      '<ul class="proto-bars">' + botRows + "</ul></section>";
  }

  /* ---------------- view B: cost ---------------- */

  function costChart() {
    var W = 640, H = 170, padL = 8, padB = 26, padT = 22;
    var keys = STATS.dayKeys;
    var max = 0;
    keys.forEach(function (k) { max = Math.max(max, STATS.daily[k].cost); });
    if (!max) { max = 1; }
    var slot = (W - padL * 2) / keys.length;
    var bw = Math.min(44, slot * 0.62);
    var bodyH = H - padT - padB;
    var out = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Chi phí theo ngày">';
    out += '<line x1="' + padL + '" y1="' + (padT + bodyH) + '" x2="' + (W - padL) + '" y2="' + (padT + bodyH) + '" class="proto-grid"/>';
    out += '<text x="' + padL + '" y="' + (padT - 6) + '" class="proto-axis">cao nhất ' + fmtCost(max) + "</text>";
    keys.forEach(function (k, i) {
      var v = STATS.daily[k].cost;
      var h = (v / max) * bodyH;
      var x = padL + slot * i + (slot - bw) / 2;
      var y = padT + bodyH - h;
      if (v > 0) {
        out += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(h, 2).toFixed(1) + '" class="proto-seg-cost"><title>' + fmtDay(k) + ": " + fmtCost(v) + "</title></rect>";
      } else {
        out += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (padT + bodyH - 4) + '" class="proto-axis" text-anchor="middle">—</text>';
      }
      out += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 8) + '" class="proto-axis" text-anchor="middle">' + fmtDay(k) + "</text>";
    });
    return out + "</svg>";
  }

  function renderCost() {
    var s = STATS;
    var pricedNonFailed = 0, costNonFailed = 0;
    SESSIONS.forEach(function (sn) {
      sn.turns.forEach(function (t) {
        if (typeof t.costUsd === "number" && t.status !== "failed") {
          pricedNonFailed++; costNonFailed += t.costUsd;
        }
      });
    });
    var avg = pricedNonFailed ? costNonFailed / pricedNonFailed : null;

    var coverage = s.unpricedTurns.length
      ? "<ul>" + s.unpricedTurns.map(function (u) {
        return "<li>“" + esc(u.session.title) + "” · Lượt " + u.turn.index + " · " + esc(u.turn.model) + " — chưa có giá, không tính $0.</li>";
      }).join("") + "</ul>"
      : "<p>Tất cả các lượt đều đã có giá.</p>";

    var models = Object.keys(s.perModel).sort(function (a, b) { return s.perModel[b].costKnown - s.perModel[a].costKnown; });
    var maxCost = Math.max.apply(null, models.map(function (m) { return s.perModel[m].costKnown; }).concat([0]));
    var modelRows = models.map(function (m) {
      var pm = s.perModel[m];
      var costText = pm.pricedTurns ? fmtCost(pm.costKnown) : "Chưa ghi nhận";
      return barRow({
        name: esc(m), count: costText, shareText: pm.pricedTurns && s.costKnown ? share(pm.costKnown, s.costKnown) : "—",
        fillPct: maxCost && pm.pricedTurns ? Math.max(1.5, (pm.costKnown / maxCost) * 100) : 1.5,
        meta: "đã ghi nhận " + fmtInt(pm.pricedTurns) + "/" + fmtInt(pm.turns) + " lượt" + (pm.pricedTurns < pm.turns ? " · phần chưa ghi nhận không tính vào tỉ trọng" : "")
      });
    }).join("");

    var botIds = BOT_IDS.slice().sort(function (a, b) { return s.perBot[b].costKnown - s.perBot[a].costKnown; });
    var maxBot = Math.max.apply(null, botIds.map(function (id) { return s.perBot[id].costKnown; }).concat([0]));
    var botRows = botIds.map(function (id) {
      var pb = s.perBot[id];
      var partial = pb.pricedTurns < pb.turns;
      return barRow({
        avatar: pebble(id, "2rem"), name: "<b>" + esc(BOTS[id].name) + "</b> · " + esc(BOTS[id].role),
        count: pb.pricedTurns ? fmtCost(pb.costKnown) : "Chưa ghi nhận",
        shareText: pb.pricedTurns && s.costKnown ? share(pb.costKnown, s.costKnown) : "—",
        fillPct: maxBot && pb.pricedTurns ? Math.max(2.5, (pb.costKnown / maxBot) * 100) : 2.5,
        fillColor: BOTS[id].color,
        meta: fmtInt(pb.requests) + " lần gọi model" + (partial
          ? " · số đã ghi nhận " + fmtInt(pb.pricedTurns) + "/" + fmtInt(pb.turns) + " lượt"
          : " · đã ghi nhận " + fmtInt(pb.turns) + "/" + fmtInt(pb.turns) + " lượt")
      });
    }).join("");

    return '<section aria-labelledby="cost-h">' +
      '<header class="proto-heading"><div><p class="proto-kicker">Nguyên mẫu redesign · số liệu mẫu</p>' +
      '<h1 id="cost-h">Chi phí</h1></div></header>' +
      '<article class="proto-card proto-chart-card"><div class="proto-cost-top">' +
      "<p>Tổng chi phí <strong class=\"num\">" + fmtCost(s.costKnown) + "</strong> <span>(đã ghi nhận)</span></p>" +
      "<p>" + fmtInt(pricedNonFailed) + " lượt có giá · trung bình " + (avg == null ? "—" : fmtCost(avg)) + "/lượt (chỉ tính lượt thành công đã có giá)</p></div>" +
      costChart() +
      '<div class="proto-note"><p><b>Độ phủ giá:</b> ' + fmtInt(s.pricedTurns) + "/" + fmtInt(s.turns) + " lượt đã ghi nhận. Các lượt chưa có giá liệt kê dưới đây — không tính $0:</p>" + coverage + "</div></article>" +
      '<h2 class="proto-h2">Chi phí theo mô hình</h2><ul class="proto-bars">' + modelRows + "</ul>" +
      '<h2 class="proto-h2">Chi phí theo bot</h2>' +
      '<p class="proto-note">Tỉ trọng = chi phí đã ghi nhận của bot / tổng chi phí đã ghi nhận (' + fmtCost(s.costKnown) + ").</p>" +
      '<ul class="proto-bars proto-bot-cost">' + botRows + "</ul></section>";
  }

  /* ---------------- view C: sessions ---------------- */

  var sessionFilter = "all";

  function renderSessions() {
    var s = STATS;
    var chips = [{ id: "all", label: "Tất cả", count: s.totalSessions }].concat(BOT_IDS.map(function (id) {
      return { id: id, label: BOTS[id].name, count: s.perBot[id].participations };
    }));
    var chipHtml = chips.map(function (c) {
      var inner = c.id === "all" ? esc(c.label) : pebble(c.id, "1.4rem") + "<span>" + esc(c.label) + "</span>";
      return '<button type="button" class="proto-chip" data-bot="' + c.id + '" aria-pressed="' + (sessionFilter === c.id) + '">' +
        inner + ' <span class="num">(' + fmtInt(c.count) + ")</span></button>";
    }).join("");

    var list = SESSIONS.filter(function (sn) {
      return sessionFilter === "all" || sn.bots.indexOf(sessionFilter) >= 0;
    });
    var rows = list.length ? list.map(function (sn) {
      var failed = sessionHasError(sn);
      var kind = sn.kind === "group" ? "Nhóm " + sn.bots.length + " bot" : "1 bot";
      var toks = 0;
      sn.turns.forEach(function (t) { toks += t.totalTokens; });
      return '<li><a class="proto-card proto-session" href="#session/' + sn.id + '">' +
        '<span class="proto-session-avatar">' + avatarFor(sn, "3rem") + "</span>" +
        '<span class="proto-session-main"><span class="proto-session-title">' + esc(sn.title) + "</span>" +
        '<span class="proto-session-sub"><span class="proto-kind">' + esc(kind) + "</span> · " + fmtDateTime(sn.startedAt) +
        " · " + fmtInt(sn.turns.length) + " lượt · " + fmtDur(sessionSpan(sn)) +
        " · " + fmtInt(toks) + " token · " + esc(costLabel(sn)) + "</span></span>" +
        statusChip(failed) + "</a></li>";
    }).join("") : '<li class="proto-card"><p>Không có phiên nào khớp bộ lọc.</p></li>';

    return '<section aria-labelledby="sess-h">' +
      '<header class="proto-heading"><div><p class="proto-kicker">Nguyên mẫu redesign · số liệu mẫu</p>' +
      '<h1 id="sess-h">Trace theo phiên</h1></div></header>' +
      '<article class="proto-card proto-strip" aria-label="Tóm tắt phiên">' +
      "<div><strong class=\"num\">" + fmtInt(s.totalSessions) + "</strong><span>tổng phiên (mỗi phiên một lần)</span></div>" +
      "<div><strong class=\"num\">" + fmtInt(s.groupSessions) + "</strong><span>phiên nhóm</span></div>" +
      "<div><strong class=\"num\">" + fmtInt(s.dmSessions) + "</strong><span>phiên 1 bot</span></div>" +
      "<div><strong class=\"num\">" + fmtInt(s.turns) + "</strong><span>tổng lượt</span></div></article>" +
      '<p class="proto-note">' + esc(COUNT_NOTE) + "</p>" +
      '<div class="proto-chips" role="group" aria-label="Lọc theo bot">' + chipHtml + "</div>" +
      '<ul class="proto-session-list">' + rows + "</ul></section>";
  }

  /* ---------------- view D: bots ---------------- */

  function renderBots() {
    var s = STATS;
    var botIds = BOT_IDS.slice().sort(function (a, b) { return s.perBot[b].tokens - s.perBot[a].tokens; });
    var cards = botIds.map(function (id) {
      var b = BOTS[id], pb = s.perBot[id];
      var split = '<span class="proto-split" role="img" aria-label="Input ' + fmtInt(pb.prompt) + ", Cache " + fmtInt(pb.cached) + ", Output " + fmtInt(pb.completion) + '">' +
        '<span style="width:' + ((pb.prompt - pb.cached) / (pb.prompt + pb.completion) * 100).toFixed(1) + '%;background:var(--color-chart-input)"></span>' +
        '<span style="width:' + (pb.cached / (pb.prompt + pb.completion) * 100).toFixed(1) + '%;background:var(--color-chart-cache)"></span>' +
        '<span style="width:' + (pb.completion / (pb.prompt + pb.completion) * 100).toFixed(1) + '%;background:var(--color-accent)"></span></span>';
      var sessLinks = pb.sessions.map(function (sid) {
        var sn = sessionById(sid);
        return '<li><a href="#session/' + sid + '">' + esc(sn.title) + "</a></li>";
      }).join("");
      return '<article class="proto-card proto-bot">' +
        '<div class="proto-bot-head">' + pebble(id, "3.5rem") +
        "<div><h2>" + esc(b.name) + "</h2><p>" + esc(b.role) + "</p></div></div>" +
        '<dl class="proto-bot-stats">' +
        "<div><dt>Phiên tham gia</dt><dd class=\"num\">" + fmtInt(pb.participations) + "</dd></div>" +
        "<div><dt>Lượt đóng góp</dt><dd class=\"num\">" + fmtInt(pb.turns) + "</dd></div>" +
        "<div><dt>Lần gọi model</dt><dd class=\"num\">" + fmtInt(pb.requests) + "</dd></div>" +
        "<div><dt>Token</dt><dd class=\"num\">" + fmtInt(pb.tokens) + "</dd></div>" +
        "<div><dt>Chi phí đã ghi nhận</dt><dd class=\"num\">" + (pb.pricedTurns ? fmtCost(pb.costKnown) : "Chưa ghi nhận") + "</dd></div>" +
        (pb.errors ? "<div><dt>Lượt lỗi</dt><dd class=\"num\">" + fmtInt(pb.errors) + "</dd></div>" : "") +
        "</dl>" +
        '<p class="proto-micro">“Phiên tham gia” đếm số phiên có ' + esc(b.name) + " — không cộng vào tổng số phiên.</p>" +
        '<div class="proto-split-wrap"><p class="proto-row-meta">Vào ' + fmtInt(pb.prompt) + " · Cache " + fmtInt(pb.cached) + " · Ra " + fmtInt(pb.completion) + "</p>" + split + "</div>" +
        '<h3 class="proto-h3">Các phiên ' + esc(b.name) + " tham gia</h3>" +
        '<ul class="proto-links">' + sessLinks + "</ul></article>";
    }).join("");
    return '<section aria-labelledby="bots-h">' +
      '<header class="proto-heading"><div><p class="proto-kicker">Nguyên mẫu redesign · số liệu mẫu</p>' +
      '<h1 id="bots-h">Trace theo bot</h1></div></header>' +
      '<p class="proto-note">Xếp theo token giảm dần. Token và chi phí mỗi bot chỉ tính từ các phần botBreakdown của bot đó; ' +
      "tỉ trọng token tính trên tổng " + fmtInt(s.tokens) + " token. " + esc(COUNT_NOTE) + "</p>" +
      '<div class="proto-bot-grid">' + cards + "</div></section>";
  }

  /* ---------------- view E: session detail ---------------- */

  function stepHtml(n, st) {
    var cls = st.type === "tool" ? "proto-step is-tool" : "proto-step";
    return '<li class="' + cls + '"><span class="proto-step-num num">' + String(n).padStart(2, "0") + "</span>" +
      '<div><p class="proto-step-title">' + esc(st.name) +
      (st.isError ? ' <span class="proto-chip-status is-err">lỗi</span>' : "") + "</p>" +
      '<p class="proto-row-meta">' + esc(st.type) + " · " + fmtDur(st.latencyMs) + "</p>" +
      (st.detail ? '<p class="proto-step-note">' + esc(st.detail) + "</p>" : "") + "</div></li>";
  }

  function turnCard(sn, t) {
    var failed = t.status === "failed";
    var bots = [];
    t.botBreakdown.forEach(function (p) { if (bots.indexOf(p.botId) < 0) { bots.push(p.botId); } });
    var av = bots.length === 1 ? pebble(bots[0], "2.25rem") : avatarFor({ bots: bots, turns: [t], id: sn.id }, "2.25rem");
    var steps = t.steps.map(function (st, i) { return stepHtml(i + 1, st); }).join("");
    var io = (t.user ? '<p class="proto-io-label">Input</p><pre class="proto-code">' + esc(t.user) + "</pre>" : "") +
      (t.assistant ? '<p class="proto-io-label">Output</p><pre class="proto-code">' + esc(t.assistant) + "</pre>" : "");
    return '<article class="proto-card proto-turn">' +
      '<header class="proto-turn-head">' + av +
      "<div><h3>Lượt " + t.index + "</h3>" +
      '<p class="proto-row-meta">' + esc(t.model) + " · " + fmtDateTime(t.startedAt) + " · " + fmtDur(t.latencyMs) +
      " · " + fmtInt(t.requests) + " lần gọi · " + fmtInt(t.totalTokens) + " token · " +
      (t.costUsd == null ? "chưa ghi nhận" : fmtCost(t.costUsd)) + "</p></div>" +
      statusChip(failed) + "</header>" +
      (failed && t.error ? '<p class="proto-error">' + esc(t.error) + "</p>" : "") +
      "<details><summary>Xem các bước &amp; dữ liệu</summary><div class=\"proto-turn-body\">" +
      '<p class="proto-row-meta">Vào ' + fmtInt(t.promptTokens) + " (Cache " + fmtInt(t.cachedTokens) + ") · Ra " + fmtInt(t.completionTokens) + "</p>" +
      '<ol class="proto-steps">' + steps + "</ol>" + io + "</div></details></article>";
  }

  function renderSessionDetail(id) {
    var sn = sessionById(id);
    var main = document.getElementById("proto-view");
    if (!sn) {
      main.innerHTML = '<section><a class="proto-back" href="#sessions">‹ Phiên</a>' +
        '<article class="proto-card"><p>Không có phiên “' + esc(id) + "”. Mời quay lại danh sách.</p></article></section>";
      setNav("sessions");
      return;
    }
    var failed = sessionHasError(sn);
    var toks = 0, reqs = 0;
    sn.turns.forEach(function (t) { toks += t.totalTokens; reqs += t.requests; });
    var perBotHere = {};
    sn.bots.forEach(function (id) { perBotHere[id] = { tokens: 0, cost: 0, priced: true }; });
    sn.turns.forEach(function (t) {
      t.botBreakdown.forEach(function (p) {
        perBotHere[p.botId].tokens += p.promptTokens + p.completionTokens;
        if (typeof p.costUsd === "number") { perBotHere[p.botId].cost += p.costUsd; }
        else { perBotHere[p.botId].priced = false; }
      });
    });
    var chips = sn.bots.map(function (id) {
      var h = perBotHere[id];
      return '<span class="proto-member">' + pebble(id, "1.75rem") + "<span><b>" + esc(BOTS[id].name) + "</b> · " +
        fmtInt(h.tokens) + " token · " + (h.priced ? fmtCost(Math.round(h.cost * 1e6) / 1e6) : "chưa ghi nhận") + "</span></span>";
    }).join("");
    var turns = sn.turns.map(function (t) { return turnCard(sn, t); }).join("");
    main.innerHTML = '<section aria-labelledby="detail-h"><a class="proto-back" href="#sessions">‹ Phiên</a>' +
      '<header class="proto-detail-head">' + avatarFor(sn, "3.5rem") +
      "<div><p class=\"proto-kicker\">Chi tiết phiên mẫu</p><h1 id=\"detail-h\">" + esc(sn.title) + "</h1>" +
      '<p class="proto-row-meta"><span class="proto-id">ID: ' + esc(sn.id) + "</span> · " +
      (sn.kind === "group" ? "Nhóm " + sn.bots.length + " bot" : "Trò chuyện 1 bot") + " · " +
      fmtDateTime(sn.startedAt) + " → " + fmtDateTime(sn.turns[sn.turns.length - 1].endedAt) + "</p></div>" +
      statusChip(failed) + "</header>" +
      '<div class="proto-members">' + chips + "</div>" +
      '<article class="proto-card proto-strip">' +
      "<div><strong class=\"num\">" + fmtInt(sn.turns.length) + "</strong><span>lượt</span></div>" +
      "<div><strong class=\"num\">" + fmtInt(reqs) + "</strong><span>lần gọi model</span></div>" +
      "<div><strong class=\"num\">" + fmtInt(toks) + "</strong><span>token</span></div>" +
      "<div><strong class=\"num\">" + esc(costLabel(sn)) + "</strong><span>chi phí</span></div></article>" +
      '<div class="proto-turns">' + turns + "</div></section>";
    setNav(sn.kind === "group" ? "sessions" : "sessions");
  }

  /* ---------------- router ---------------- */

  var NAV = [
    { route: "usage", label: "Sử dụng token" },
    { route: "cost", label: "Chi phí" },
    { route: "sessions", label: "Trace theo phiên" },
    { route: "bots", label: "Trace theo bot" }
  ];

  function setNav(route) {
    var links = document.querySelectorAll(".proto-nav a");
    links.forEach(function (a) {
      var on = a.getAttribute("data-route") === route;
      if (on) { a.setAttribute("aria-current", "page"); }
      else { a.removeAttribute("aria-current"); }
    });
  }

  function render() {
    var hash = location.hash || "#usage";
    var main = document.getElementById("proto-view");
    if (hash.indexOf("#session/") === 0) {
      renderSessionDetail(hash.slice("#session/".length));
      return;
    }
    var route = hash.slice(1);
    if (route !== "usage" && route !== "cost" && route !== "sessions" && route !== "bots") { route = "usage"; }
    setNav(route);
    if (route === "usage") { main.innerHTML = renderUsage(); }
    else if (route === "cost") { main.innerHTML = renderCost(); }
    else if (route === "sessions") { main.innerHTML = renderSessions(); }
    else { main.innerHTML = renderBots(); }
    var h = main.querySelector("h1");
    if (h) { document.title = "Thyca Trace mẫu — " + h.textContent; }
  }

  document.addEventListener("click", function (e) {
    var chip = e.target.closest ? e.target.closest(".proto-chip[data-bot]") : null;
    if (chip) {
      sessionFilter = chip.getAttribute("data-bot");
      render();
    }
  });
  window.addEventListener("hashchange", render);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
