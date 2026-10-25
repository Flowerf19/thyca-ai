/* Static prototype scoping only. Never reads APIs or user data.
 * A bot scope includes that bot's contributions in BOTH private and group chat.
 * Session membership is not a license to attribute the whole group's metrics.
 */
(function () {
  "use strict";

  function roundCost(value) { return Math.round(value * 1e9) / 1e9; }
  function numeric(value) { return typeof value === "number" && Number.isFinite(value); }
  function metrics(parts) {
    var result = { requests: 0, promptTokens: 0, cachedTokens: 0, completionTokens: 0, totalTokens: 0, costUsd: null, knownCostUsd: null };
    var complete = parts.length > 0;
    parts.forEach(function (part) {
      result.requests += part.requests;
      result.promptTokens += part.promptTokens;
      result.cachedTokens += part.cachedTokens;
      result.completionTokens += part.completionTokens;
      if (numeric(part.costUsd)) { result.knownCostUsd = (result.knownCostUsd || 0) + part.costUsd; }
      else { complete = false; }
    });
    result.totalTokens = result.promptTokens + result.completionTokens;
    if (result.knownCostUsd !== null) { result.knownCostUsd = roundCost(result.knownCostUsd); }
    if (complete) { result.costUsd = result.knownCostUsd; }
    return result;
  }

  function blankTotals() {
    return { turns: 0, requests: 0, prompt: 0, cached: 0, completion: 0, tokens: 0, costKnown: null, pricedTurns: 0 };
  }
  function accumulate(target, turn) {
    target.turns++;
    target.requests += turn.requests;
    target.prompt += turn.promptTokens;
    target.cached += turn.cachedTokens;
    target.completion += turn.completionTokens;
    target.tokens += turn.totalTokens;
    if (numeric(turn.knownCostUsd)) { target.costKnown = roundCost((target.costKnown || 0) + turn.knownCostUsd); }
    if (numeric(turn.costUsd)) { target.pricedTurns++; }
  }

  function build(botId) {
    botId = botId || "";
    if (botId && !Object.prototype.hasOwnProperty.call(window.PROTO_BOTS, botId)) { return null; }
    var sessions = window.PROTO_SESSIONS.filter(function (session) {
      return !botId || session.bots.indexOf(botId) !== -1;
    }).map(function (session) {
      var turns = session.turns.map(function (turn) {
        var parts = turn.botBreakdown.filter(function (part) { return !botId || part.botId === botId; });
        if (!parts.length) { return null; }
        // Keep original indices and unmodified execution context for Trace detail.
        return Object.assign({}, turn, metrics(parts), { botBreakdown: parts, sourceTurn: turn });
      }).filter(Boolean);
      return Object.assign({}, session, { turns: turns, sourceSession: session });
    });
    var stats = Object.assign(blankTotals(), {
      totalSessions: sessions.length, groupSessions: 0, dmSessions: 0,
      perBot: {}, perModel: {}, daily: {}, dayKeys: [],
      unpricedTurns: [], failedTurns: [], rangeStart: null, rangeEnd: null,
      averageCostUsd: null, averageTurns: 0
    });
    Object.keys(window.PROTO_BOTS).forEach(function (id) {
      stats.perBot[id] = Object.assign(blankTotals(), { participations: 0, errors: 0, sessions: [] });
    });
    sessions.forEach(function (session) {
      if (session.kind === "group") { stats.groupSessions++; } else { stats.dmSessions++; }
      session.bots.filter(function (id) { return !botId || id === botId; }).forEach(function (id) {
        stats.perBot[id].participations++;
        stats.perBot[id].sessions.push(session.id);
      });
      session.turns.forEach(function (turn) {
        accumulate(stats, turn);
        if (!numeric(turn.costUsd)) { stats.unpricedTurns.push({ session: session, turn: turn }); }
        if (turn.status === "failed") { stats.failedTurns.push({ session: session, turn: turn }); }
        if (numeric(turn.costUsd) && turn.status !== "failed") {
          stats.averageTurns++;
          stats.averageCostUsd = roundCost((stats.averageCostUsd || 0) + turn.costUsd);
        }
        if (!stats.perModel[turn.model]) { stats.perModel[turn.model] = blankTotals(); }
        accumulate(stats.perModel[turn.model], turn);
        // ISO mock dates carry the intended calendar date; no viewer-zone drift.
        var key = turn.startedAt.slice(0, 10);
        if (!stats.daily[key]) { stats.daily[key] = { input: 0, cache: 0, output: 0, cost: null, turns: 0 }; }
        var day = stats.daily[key];
        day.input += turn.promptTokens - turn.cachedTokens;
        day.cache += turn.cachedTokens;
        day.output += turn.completionTokens;
        day.turns++;
        if (numeric(turn.knownCostUsd)) { day.cost = roundCost((day.cost || 0) + turn.knownCostUsd); }
        if (!stats.rangeStart || key < stats.rangeStart) { stats.rangeStart = key; }
        if (!stats.rangeEnd || key > stats.rangeEnd) { stats.rangeEnd = key; }
        // Combine duplicate parts before counting a bot's contributed turns.
        var ids = Array.from(new Set(turn.botBreakdown.map(function (part) { return part.botId; })));
        ids.forEach(function (id) {
          var own = metrics(turn.botBreakdown.filter(function (part) { return part.botId === id; }));
          accumulate(stats.perBot[id], own);
          if (turn.status === "failed") { stats.perBot[id].errors++; }
        });
      });
    });
    // Fixed complete day axis spans the mock snapshot, including empty bot scopes.
    var allDays = [];
    window.PROTO_SESSIONS.forEach(function (session) {
      session.turns.forEach(function (turn) { allDays.push(turn.startedAt.slice(0, 10)); });
    });
    allDays.sort();
    if (allDays.length) {
      var cursor = Date.parse(allDays[0] + "T00:00:00Z");
      var end = Date.parse(allDays[allDays.length - 1] + "T00:00:00Z");
      for (; cursor <= end; cursor += 86400000) {
        var date = new Date(cursor).toISOString().slice(0, 10);
        stats.dayKeys.push(date);
        if (!stats.daily[date]) { stats.daily[date] = { input: 0, cache: 0, output: 0, cost: null, turns: 0 }; }
      }
    }
    return { botId: botId, sessions: sessions, stats: stats };
  }
  window.PROTO_SCOPE = { build: build, metrics: metrics };
})();
