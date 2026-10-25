/* Run: node thyca/webui/prototype-trace/scope.test.js */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = { window: {} };
vm.createContext(context);
for (const name of ["data.js", "scope.js"]) {
  vm.runInContext(fs.readFileSync(__dirname + "/" + name, "utf8"), context);
}
const api = context.window.PROTO_SCOPE;
const snapshot = JSON.stringify(context.window.PROTO_SESSIONS);
const all = api.build("");
assert.equal(all.stats.totalSessions, 8);
assert.equal(all.stats.groupSessions, 5);
assert.equal(all.stats.dmSessions, 3);
assert.equal(all.stats.tokens, 77270);
assert.equal(all.stats.turns, 26);
assert.equal(all.stats.requests, 43);
assert.equal(all.stats.pricedTurns, 25);
assert.equal(all.stats.costKnown, 0.115371);
let tokens = 0, calls = 0, cost = 0, participations = 0;
for (const id of Object.keys(context.window.PROTO_BOTS)) {
  const scope = api.build(id);
  const own = all.stats.perBot[id];
  assert.equal(scope.stats.tokens, own.tokens, id + " owns only its tokens");
  assert.equal(scope.stats.requests, own.requests, id + " owns only its calls");
  assert.equal(scope.stats.costKnown, own.costKnown, id + " owns only its cost");
  assert.equal(scope.stats.turns, own.turns, id + " contributed turns");
  assert.equal(scope.stats.totalSessions, own.participations);
  for (const session of scope.sessions) {
    assert.ok(session.sourceSession.bots.includes(id));
    for (const turn of session.turns) {
      assert.ok(turn.botBreakdown.every(part => part.botId === id));
      assert.equal(turn.index, turn.sourceTurn.index);
    }
  }
  tokens += scope.stats.tokens;
  calls += scope.stats.requests;
  cost += scope.stats.costKnown;
  participations += scope.stats.totalSessions;
}
assert.equal(tokens, all.stats.tokens);
assert.equal(calls, all.stats.requests);
assert.ok(Math.abs(cost - all.stats.costKnown) < 1e-9);
assert.equal(participations, 20);
assert.equal(api.build("not-a-bot"), null);
assert.equal(JSON.stringify(context.window.PROTO_SESSIONS), snapshot, "scoping never mutates mock data");

// Adversarial shared-turn fixture: a priced zero is real, unknown is not zero.
context.window.PROTO_BOTS = { a: {}, b: {}, idle: {} };
const part = (id, price) => ({ botId: id, requests: 1, promptTokens: 100, cachedTokens: 25, completionTokens: 20, costUsd: price });
context.window.PROTO_SESSIONS = [{ id: "group", bots: ["a", "b", "idle"], kind: "group", turns: [{
  index: 7, model: "model", status: "completed", startedAt: "2026-10-10T00:15:00+07:00",
  botBreakdown: [part("a", 0), part("b", null)]
}] }];
const mixed = api.build("");
assert.equal(mixed.stats.totalSessions, 1, "one shared session, not three");
assert.equal(mixed.stats.tokens, 240);
assert.equal(mixed.stats.pricedTurns, 0);
assert.equal(mixed.stats.costKnown, 0, "known zero stays zero despite missing other price");
assert.equal(mixed.stats.daily["2026-10-10"].input, 150, "cache subtracted once");
assert.equal(mixed.stats.dayKeys.length, 1);
assert.equal(api.build("a").stats.pricedTurns, 1, "another bot's null price does not taint a's price");
assert.equal(api.build("a").stats.costKnown, 0);
assert.equal(api.build("b").stats.costKnown, null);
assert.equal(api.build("b").stats.pricedTurns, 0);
assert.equal(api.build("idle").stats.totalSessions, 1, "membership is participation even without contribution");
assert.equal(api.build("idle").stats.turns, 0);
assert.equal(api.build("idle").stats.tokens, 0);
assert.equal(api.build("idle").stats.costKnown, null);

// Mixed known/unknown parts of the SAME bot preserve recorded cost and count once.
context.window.PROTO_SESSIONS[0].turns[0].botBreakdown = [part("a", 0.01), part("a", null)];
const partial = api.build("a");
assert.equal(partial.stats.costKnown, 0.01);
assert.equal(partial.stats.pricedTurns, 0);
assert.equal(partial.stats.turns, 1);
assert.equal(partial.stats.perBot.a.turns, 1);
assert.equal(partial.stats.averageTurns, 0);
console.log("PASS: 8 unique sessions; 20 participations; 77,270 tokens; 43 model calls; $0.115371 recorded. Bot scoping, null/zero/partial costs, no mutation, stable indices and empty contributions verified.");
