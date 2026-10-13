"""Node tests for Team v1 frontend pure helpers.

Covers thyca/webui/pages/chat/team-data.js (tint fallback, bot normalize,
session filter, mention filter, exchange split, delegation tree, bot cost,
candidate queue) plus the re-exports through cost-data.js / trace-data.js.
DOM-free: team-data.js has no DOM or fetch, so it imports cleanly under node.
Mirrors the eval style of tests/test_webui_format.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT / "thyca" / "webui" / "pages" / "chat" / "team-data.js"
MENTION = ROOT / "thyca" / "webui" / "pages" / "chat" / "team-mention.js"
UNREAD = ROOT / "thyca" / "webui" / "pages" / "chat" / "team-unread.js"
COST_DATA = ROOT / "thyca" / "webui" / "pages" / "dashboard" / "cost-data.js"
TRACE_DATA = ROOT / "thyca" / "webui" / "pages" / "dashboard" / "trace-data.js"
CHAT_CSS = ROOT / "thyca" / "webui" / "pages" / "chat" / "chat.css"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _run(node: str, body: str, via: str = "team") -> object:
    module = {"team": TEAM, "mention": MENTION, "cost": COST_DATA,
              "trace": TRACE_DATA, "unread": UNREAD}[via]
    source = (
        f"import * as api from '{module.as_posix()}';\n"
        f"const main = async () => {{ {body} }};\n"
        "console.log(JSON.stringify(await main()));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_thyca_tint_pinned_and_card_tint_wins(node: str) -> None:
    value = _run(node, "return [api.tintForBot('thyca', 'botanical'), api.tintForBot('other', 'ink')];")
    assert value == ["accent", "ink"]


def test_unknown_tint_falls_back_deterministically(node: str) -> None:
    value = _run(
        node,
        "return api.tintForBot('my-bot', 'not-a-token') === api.tintForBot('my-bot', '')"
        " && api.BOT_TINTS.includes(api.tintForBot('my-bot', 'not-a-token'));",
    )
    assert value is True


def test_normalize_bots_tolerates_missing_backend(node: str) -> None:
    value = _run(
        node,
        "return [api.normalizeBots(null), api.normalizeBots({}),"
        " api.normalizeBots({bots: [{id: 'thyca'}, {id: ''}, null]})];",
    )
    assert value[0] == []
    assert value[1] == []
    assert [bot["id"] for bot in value[2]] == ["thyca"]
    assert value[2][0]["tint"] == "accent"
    assert value[2][0]["status"] == "unknown"


def test_sessions_for_bot_legacy_sessions_belong_to_thyca(node: str) -> None:
    sessions = [
        {"id": "s1"},
        {"id": "s2", "bots": ["thyca", "b2"]},
        {"id": "s3", "bots": ["b2"]},
    ]
    value = _run(
        node,
        f"const sessions = {json.dumps(sessions)};"
        " return [api.sessionsForBot(sessions, 'thyca').map(s => s.id),"
        " api.sessionsForBot(sessions, 'b2').map(s => s.id)];",
    )
    assert value == [["s1", "s2"], ["s2", "s3"]]


def test_mention_candidates_case_insensitive(node: str) -> None:
    roster = [{"id": "thyca-designer", "name": "Designer"}, {"id": "thyca-coder", "name": "Coder"}]
    value = _run(
        node,
        f"const roster = {json.dumps(roster)};"
        " return [api.mentionCandidates(roster, 'DES').map(b => b.id),"
        " api.mentionCandidates(roster, '').map(b => b.id)];",
    )
    assert value == [["thyca-designer"], ["thyca-designer", "thyca-coder"]]


def test_split_exchange_groups_bus_events(node: str) -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "meta": {"kind": "step", "bot_id": "b1"}},
        {"role": "assistant", "meta": {"kind": "status", "bot_id": "b2"}},
        {"role": "assistant", "content": "done", "meta": {"bot_id": "b1"}},
    ]
    value = _run(
        node,
        f"return api.splitExchange({json.dumps(messages)}).map(r => r.type);",
    )
    assert value == ["message", "exchange", "message"]


def test_delegation_tree_parents_by_span(node: str) -> None:
    messages = [
        {"meta": {"bot_id": "a", "span_id": "s1"}},
        {"meta": {"bot_id": "b", "span_id": "s2", "parent_span_id": "s1"}},
        {"content": "plain"},
    ]
    value = _run(
        node,
        f"const roots = api.buildDelegationTree({json.dumps(messages)});"
        " return roots.map(n => [n.botId, n.children.map(c => c.botId)]);",
    )
    assert value == [["a", ["b"]], ["", []]]


def test_aggregate_bot_cost_null_is_unpriced(node: str) -> None:
    rows = [
        {"bot_id": "a", "cost_usd": 0.1, "requests": 2},
        {"bot_id": "a", "cost_usd": None},
        {"cost_usd": 0.0},
    ]
    value = _run(node, f"return api.aggregateBotCost({json.dumps(rows)});")
    by_bot = {group["botId"]: group for group in value}
    assert by_bot["a"]["pricedTurns"] == 1
    assert by_bot["a"]["costUsd"] == pytest.approx(0.1)
    assert by_bot[""]["pricedTurns"] == 1  # explicit 0 stays priced


def test_aggregate_bot_cost_uses_breakdown_when_present(node: str) -> None:
    rows = [
        # Delegated turn: row.bot_id is the first bot (thyca) but helper
        # did most of the work — breakdown must win over row.bot_id.
        {"bot_id": "thyca", "requests": 3, "cost_usd": 0.03,
         "bot_breakdown": [
             {"bot_id": "thyca", "requests": 1, "cost_usd": 0.01},
             {"bot_id": "helper", "requests": 2, "cost_usd": 0.02},
         ]},
        # No breakdown: legacy fallback to row.bot_id + turn totals.
        {"bot_id": "helper", "requests": 1, "cost_usd": 0.005},
        # Empty breakdown also falls back; unpriced stays unpriced.
        {"bot_id": "helper", "requests": 1, "cost_usd": None, "bot_breakdown": []},
        # Breakdown entry without bot_id lands in the unattributed bucket.
        {"requests": 9, "cost_usd": 9,
         "bot_breakdown": [{"requests": 1, "cost_usd": None}]},
    ]
    value = _run(node, f"return api.aggregateBotCost({json.dumps(rows)});")
    by_bot = {group["botId"]: group for group in value}
    assert by_bot["thyca"]["turns"] == 1
    assert by_bot["thyca"]["requests"] == 1
    assert by_bot["thyca"]["costUsd"] == pytest.approx(0.01)
    assert by_bot["helper"]["turns"] == 3
    assert by_bot["helper"]["requests"] == 2 + 1 + 1
    assert by_bot["helper"]["costUsd"] == pytest.approx(0.025)
    assert by_bot["helper"]["pricedTurns"] == 2
    assert by_bot[""]["requests"] == 1
    assert by_bot[""]["costUsd"] is None
    # Requests/cost across groups reproduce the breakdown+fallback sums,
    # never the delegated row's whole-turn totals counted for thyca alone.
    assert sum(g["requests"] for g in value) == 1 + 2 + 1 + 1 + 1


def test_cost_and_trace_data_reexport_team_helpers(node: str) -> None:
    cost = _run(node, "return api.aggregateBotCost([{bot_id: 'b', cost_usd: 1}])[0].botId;", via="cost")
    assert cost == "b"
    tree = _run(node, "return api.buildDelegationTree([{meta: {bot_id: 'a', span_id: 's'}}]).length;", via="trace")
    assert tree == 1


def test_normalize_bots_keeps_card_fields(node: str) -> None:
    """H2: real card fields survive normalizeBots; absent fields stay empty
    (the drawer renders an honest note) — never invented."""
    value = _run(
        node,
        "return api.normalizeBots({bots: ["
        " {id: 'b1', abilities: ['code'], limits: ['no rm'],"
        " model: 'm1', bash: 'ask', skills: ['s1'], schedule: 'daily'},"
        " {id: 'b2', model: {id: 'm2', thinking: 'high'}},"
        "]});",
    )
    full, bare = value
    assert full["abilities"] == ["code"]
    assert full["limits"] == ["no rm"]
    assert full["model"] == "m1"
    assert full["bash"] == "ask"
    assert full["skills"] == ["s1"]
    assert full["schedule"] == "daily"
    assert bare["model"] == "m2"
    assert bare["abilities"] == []
    assert bare["limits"] == []
    assert bare["skills"] == []
    assert bare["bash"] == ""
    assert bare["schedule"] == ""


def test_verify_shared_session(node: str) -> None:
    """M3: only a detail covering every requested bot opens as shared."""
    value = _run(
        node,
        "return [api.verifySharedSession({bots: ['a', 'b']}, ['a', 'b']),"
        " api.verifySharedSession({id: 's1'}, ['a', 'b']),"
        " api.verifySharedSession({bots: ['a']}, ['a', 'b']),"
        " api.verifySharedSession(null, ['a'])];",
    )
    assert value[0] == {"ok": True, "missing": []}
    assert value[1]["ok"] is False
    assert value[1]["missing"] == ["a", "b"]
    assert value[2] == {"ok": False, "missing": ["b"]}
    assert value[3]["ok"] is False


def test_mention_query_requires_boundary(node: str) -> None:
    """M2: emails must not open the popup; start/whitespace @ must."""
    value = _run(
        node,
        "return [api.mentionQueryAt('a@gmail.com', 11),"
        " api.mentionQueryAt('@thy', 4),"
        " api.mentionQueryAt('nhờ @cod', 8),"
        " api.mentionQueryAt('a@gmail @b', 10)];",
        via="mention",
    )
    assert value[0] is None
    assert value[1] == {"query": "thy", "start": 0}
    assert value[2] == {"query": "cod", "start": 4}
    assert value[3] == {"query": "b", "start": 8}


def test_delegation_tree_clusters_same_trace(node: str) -> None:
    """H3: same-trace roots cluster; untraced roots stay last in order."""
    messages = [
        {"meta": {"bot_id": "a", "trace_id": "t1", "span_id": "s1"}},
        {"content": "plain"},
        {"meta": {"bot_id": "b", "trace_id": "t1", "span_id": "s2"}},
        {"meta": {"bot_id": "c", "trace_id": "t2", "span_id": "s3"}},
    ]
    value = _run(
        node,
        f"return api.buildDelegationTree({json.dumps(messages)})"
        ".map(n => [n.botId, n.traceId]);",
    )
    assert value == [["a", "t1"], ["b", "t1"], ["c", "t2"], ["", ""]]


def test_hidden_attribute_beats_flex_display() -> None:
    """H1/L3: scoped [hidden] rules outrank the flex display shorthands."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.sessions\[hidden\]\s*\{[^}]*display:\s*none", css)
    assert re.search(
        r"\.pick-shell\.is-picking\s+\.sessions\[hidden\]\s*\{[^}]*display:\s*none\s*!important",
        css,
    )
    assert re.search(r"\.team-strip\[hidden\]\s*\{[^}]*display:\s*none", css)


def test_pending_candidates_only_pending(node: str) -> None:
    payload = {"candidates": [
        {"id": 1, "bot_id": "a", "text": "t", "status": "pending"},
        {"id": 2, "status": "approved"},
        {"id": 3, "status": "pending"},
    ]}
    value = _run(node, f"return api.pendingCandidates({json.dumps(payload)}).map(c => c.id);")
    assert value == ["1", "3"]


def test_main_session_resolves_remembered_then_newest(node: str) -> None:
    sessions = [
        {"id": "old", "bots": ["b1"], "updated_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "bots": ["b1"], "updated_at": "2026-05-01T00:00:00Z"},
        {"id": "shared", "bots": ["b1", "b2"], "updated_at": "2026-06-01T00:00:00Z"},
        {"id": "legacy", "updated_at": "2026-07-01T00:00:00Z"},
    ]
    value = _run(
        node,
        f"const sessions = {json.dumps(sessions)};"
        " return [api.findMainSession(sessions, 'b1', 'old')?.id,"
        " api.findMainSession(sessions, 'b1', 'gone')?.id,"
        " api.findMainSession(sessions, 'b1', '')?.id,"
        " api.findMainSession(sessions, 'nobody', '')];",
    )
    assert value == ["old", "new", "new", None]


def test_main_session_partition_pins_first(node: str) -> None:
    sessions = [
        {"id": "shared", "bots": ["b1", "b2"]},
        {"id": "solo-old", "bots": ["b1"]},
        {"id": "solo-new", "bots": ["b1"]},
    ]
    value = _run(
        node,
        f"const sessions = {json.dumps(sessions)};"
        " const parts = api.partitionBotSessions(sessions, 'b1', 'solo-new');"
        " return [parts.main.id, parts.others.map(s => s.id)];",
    )
    assert value == ["solo-new", ["shared", "solo-old"]]
    stale = _run(
        node,
        f"const parts = api.partitionBotSessions({json.dumps(sessions)}, 'b1', 'gone');"
        " return [parts.main, parts.others.map(s => s.id)];",
    )
    assert stale == [None, ["shared", "solo-old", "solo-new"]]


def test_main_session_detail_guard(node: str) -> None:
    value = _run(
        node,
        "return [api.isMainSessionDetail({bots: ['b1']}, 'b1'),"
        " api.isMainSessionDetail({bots: ['b1', 'b2']}, 'b1'),"
        " api.isMainSessionDetail({bots: ['thyca']}, 'b1'),"
        " api.isMainSessionDetail({}, 'b1'),"
        " api.isSoloSession({bots: ['b1']}, 'b1'),"
        " api.isSoloSession({bots: ['b1', 'b2']}, 'b1'),"
        " api.isSoloSession({}, 'thyca')];",
    )
    assert value == [True, False, False, False, True, False, False]


def test_bot_attribution_skips_user_and_sidecars(node: str) -> None:
    messages = [
        {"role": "assistant", "ts": "2026-01-02T00:00:00Z",
         "meta": {"bot_id": "b1", "kind": "llm"}},
        {"role": "assistant", "ts": "2026-01-03T00:00:00Z",
         "meta": {"bot_id": "b1", "kind": "naming"}},
        {"role": "assistant", "ts": "2026-01-04T00:00:00Z",
         "meta": {"bot_id": "b1", "kind": "compaction"}},
        {"role": "assistant", "ts": "2026-01-05T00:00:00Z",
         "meta": {"kind": "llm"}},
        {"role": "user", "ts": "2026-01-06T00:00:00Z",
         "meta": {"bot_id": "b1"}},
        {"role": "assistant", "meta": {"bot_id": "b1"}},
    ]
    value = _run(
        node,
        f"const messages = {json.dumps(messages)};"
        " return [api.botUnreadCount(messages, '2026-01-01T00:00:00Z'),"
        " api.botUnreadCount(messages, '2026-01-02T00:00:00Z'),"
        " api.isBotAttributed(messages[0]),"
        " api.isBotAttributed(messages[1]),"
        " api.isBotAttributed(messages[4])];",
    )
    # Only the kind=llm bot row with a stamp counts; sidecars, unstamped
    # rows, unattributed rows and user rows never count.
    assert value == [1, 0, True, False, False]


def test_summarize_unread_baselines_stale_clears(node: str) -> None:
    summary = {"id": "s1", "updated_at": "2026-02-01T00:00:00Z", "message_count": 4}
    value = _run(
        node,
        f"const summary = {json.dumps(summary)};"
        " return [api.summarizeUnread(null, summary),"
        " api.summarizeUnread({ts: '2026-02-01T00:00:00Z', count: 4}, summary),"
        " api.summarizeUnread({ts: '2026-01-01T00:00:00Z', count: 2}, summary),"
        " api.summarizeUnread({ts: '2026-01-01T00:00:00Z', count: 9},"
        " {id: 's1', updated_at: '2026-01-01T00:00:00Z', message_count: 9}),"
        " api.summarizeUnread({ts: '2026-02-01T00:00:00Z', count: 4}, summary,"
        " [{role: 'assistant', ts: '2026-03-01T00:00:00Z',"
        " meta: {bot_id: 'b1', kind: 'llm'}}]),"
        " api.summarizeUnread({ts: '2026-03-01T00:00:00Z', count: 5}, summary,"
        " [{role: 'assistant', ts: '2026-03-01T00:00:00Z',"
        " meta: {bot_id: 'b1', kind: 'llm'}}])];",
    )
    assert value[0] == {"unread": False, "count": 0, "fresh": True}
    assert value[1] == {"unread": False, "count": 0, "fresh": False}
    assert value[2]["unread"] is True and value[2]["count"] == 0  # stale dot
    assert value[3]["unread"] is False  # same stamp+count: cleared
    assert value[4] == {"unread": True, "count": 1, "fresh": False}
    assert value[5]["unread"] is False  # seen covers the bot row


def test_team_dom_modules_have_no_literal_colors() -> None:
    """FE-6: no new literal colors — team CSS/JS reference tokens only."""
    import re

    paths = [
        ROOT / "thyca" / "webui" / "pages" / "chat" / name
        for name in ("team-data.js", "team-sidebar.js", "team-mention.js", "team-strip.js",
                     "team-cards.js", "team-drawer.js", "team-unread.js")
    ] + [
        ROOT / "thyca" / "webui" / "pages" / "dashboard" / name
        for name in ("cost-bots.js", "trace-delegation.js")
    ] + [ROOT / "thyca" / "webui" / "pages" / "memories" / "team-pending.js"]
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|oklch\(|rgba?\(")
    offenders = {str(path): literal.findall(path.read_text(encoding="utf-8")) for path in paths}
    assert all(not hits for hits in offenders.values()), offenders


def test_find_main_session_prefers_dm_marker(node: str) -> None:
    """CBE-5: the dm-marked session wins over newer unmarked solos; another
    bot's marker is ignored."""
    sessions = [
        {"id": "solo-new", "bots": ["b1"], "dm": None,
         "updated_at": "2026-06-01T00:00:00Z"},
        {"id": "dm", "bots": ["b1"], "dm": "b1",
         "updated_at": "2026-01-01T00:00:00Z"},
        {"id": "other-dm", "bots": ["b2"], "dm": "b2",
         "updated_at": "2026-07-01T00:00:00Z"},
        {"id": "shared", "bots": ["b1", "b2"], "dm": None,
         "updated_at": "2026-08-01T00:00:00Z"},
    ]
    value = _run(
        node,
        f"const sessions = {json.dumps(sessions)};"
        " return [api.findMainSession(sessions, 'b1', '')?.id,"
        " api.findMainSession(sessions, 'b1', 'solo-new')?.id,"
        " api.findMainSession(sessions, 'b1', 'dm')?.id,"
        " api.findMainSession(sessions, 'b2', '')?.id,"
        " api.findMainSession(sessions, 'nobody', '')];",
    )
    assert value == ["dm", "dm", "dm", "other-dm", None]


def test_find_main_session_legacy_without_dm_field(node: str) -> None:
    """Pre-CBE-5 payloads carry no dm key at all → newest-solo fallback."""
    sessions = [
        {"id": "old", "bots": ["b1"], "updated_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "bots": ["b1"], "updated_at": "2026-05-01T00:00:00Z"},
    ]
    value = _run(
        node,
        f"const sessions = {json.dumps(sessions)};"
        " return [api.findMainSession(sessions, 'b1', '')?.id,"
        " api.findMainSession(sessions, 'b1', 'old')?.id];",
    )
    assert value == ["new", "old"]


def test_is_dm_detail_requires_marker_and_solo(node: str) -> None:
    """The DM endpoint guard: marker + solo participants must both name the
    bot. Legacy POST shapes (no marker) never pass."""
    value = _run(
        node,
        "return [api.isDmDetail({id: 's', dm: 'b1', bots: ['b1']}, 'b1'),"
        " api.isDmDetail({id: 's', dm: null, bots: ['b1']}, 'b1'),"
        " api.isDmDetail({id: 's', bots: ['b1']}, 'b1'),"
        " api.isDmDetail({id: 's', dm: 'b2', bots: ['b2']}, 'b1'),"
        " api.isDmDetail({id: 's', dm: 'b1', bots: ['b1', 'b2']}, 'b1'),"
        " api.isDmDetail(null, 'b1'),"
        " api.isDmDetail({dm: 'b1', bots: ['b1']}, '')];",
    )
    assert value == [True, False, False, False, False, False, False]


def test_summarize_unread_ignores_count_drift(node: str) -> None:
    """Conservative list-only signal: count drift with no newer stamp
    (sidecars/compaction the transcript hides) never dots; a strictly
    newer stamp still dots."""
    value = _run(
        node,
        "return [api.summarizeUnread({ts: '2026-02-01T00:00:00Z', count: 2},"
        " {id: 's1', updated_at: '2026-02-01T00:00:00Z', message_count: 9}),"
        " api.summarizeUnread({ts: '2026-01-01T00:00:00Z', count: 2},"
        " {id: 's1', updated_at: '2026-02-01T00:00:00Z', message_count: 2})];",
    )
    assert value[0] == {"unread": False, "count": 0, "fresh": False}
    assert value[1] == {"unread": True, "count": 0, "fresh": False}


_STORAGE_STUB = (
    "globalThis.localStorage = { _m: {},"
    " getItem(k) { return Object.hasOwn(this._m, k) ? this._m[k] : null; },"
    " setItem(k, v) { this._m[k] = String(v); },"
    " removeItem(k) { delete this._m[k]; } };"
)


def test_team_unread_dm_remember_forget_roundtrip(node: str) -> None:
    """forgetDmSession is wired (sidebar prunes deleted DMs): it drops only
    the bot whose remembered id matches."""
    value = _run(
        node,
        _STORAGE_STUB
        + " api.rememberDm('b1', 's1');"
        " api.rememberDm('b2', 's2');"
        " const kept = [api.getRememberedDm('b1'), api.getRememberedDm('b2')];"
        " api.forgetDmSession('s1');"
        " return [kept, api.getRememberedDm('b1'), api.getRememberedDm('b2')];",
        via="unread",
    )
    assert value == [["s1", "s2"], "", "s2"]


def test_team_unread_readmap_rejects_arrays_and_corrupt(node: str) -> None:
    """Array/corrupt storage reads as empty instead of indexable entries."""
    value = _run(
        node,
        _STORAGE_STUB
        + " globalThis.localStorage.setItem(api.SEEN_KEY, '[]');"
        " const arrayGuard = api.getSeen('s1');"
        " globalThis.localStorage.setItem(api.SEEN_KEY, '\"just-a-string\"');"
        " const stringGuard = api.getSeen('s1');"
        " globalThis.localStorage.setItem(api.SEEN_KEY, 'not-json{{{');"
        " const corruptGuard = api.getSeen('s1');"
        " const base = api.markSeen('s9',"
        " {updated_at: '2026-02-01T00:00:00Z', message_count: 3});"
        " return [arrayGuard, stringGuard, corruptGuard, base,"
        " api.getSeen('s9'), api.getRememberedDm('b1')];",
        via="unread",
    )
    assert value[0] is None
    assert value[1] is None
    assert value[2] is None
    assert value[3] == {"ts": "2026-02-01T00:00:00Z", "count": 3}
    assert value[4] == value[3]
    assert value[5] == ""
