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
CARDS = ROOT / "thyca" / "webui" / "pages" / "chat" / "team-cards.js"
SIDEBAR_SRC = ROOT / "thyca" / "webui" / "pages" / "chat" / "team-sidebar.js"
STRIP_SRC = ROOT / "thyca" / "webui" / "pages" / "chat" / "team-strip.js"
INDEX_HTML = ROOT / "thyca" / "webui" / "index.html"
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


def test_guarded_stages_recheck_owner_before_every_stage(node: str) -> None:
    """Race-guard sequencing used by hub.afterTeamSession (adopt id →
    refreshSessions → loadSession): the owner check runs before the first
    stage and after every await, and a stale owner aborts before later
    stages run. Fails if any checkpoint is removed — e.g. without the
    per-stage check the stale run below would reach "load" and return True.
    (Real DOM timing across tabs is still review-only; this pins the pure
    sequencing contract the wiring test below attaches to app.js.)"""
    value = _run(
        node,
        "const freshLog = [];"
        " const fresh = await api.runGuardedStages(() => true, ["
        " async () => { freshLog.push('adopt'); },"
        " async () => { freshLog.push('refresh'); },"
        " async () => { freshLog.push('load'); }]);"
        " const entryLog = [];"
        " const entry = await api.runGuardedStages(() => false,"
        " [async () => { entryLog.push('adopt'); }]);"
        " const staleLog = [];"
        " let owner = true;"
        " const stale = await api.runGuardedStages(() => owner, ["
        " async () => { staleLog.push('adopt'); },"
        " async () => { await Promise.resolve(); owner = false;"
        " staleLog.push('refresh'); },"
        " async () => { staleLog.push('load'); }]);"
        " const fallback = await api.runGuardedStages(undefined,"
        " [async () => {}]);"
        " return [fresh, freshLog, entry, entryLog,"
        " stale, staleLog, fallback];",
    )
    assert value[0] is True
    assert value[1] == ["adopt", "refresh", "load"]
    assert value[2] is False
    assert value[3] == []
    assert value[4] is False
    assert value[5] == ["adopt", "refresh"]  # stale switch mid-refresh: no load
    assert value[6] is True  # guard-free callers (createShared) run everything


def test_after_team_session_guard_wiring() -> None:
    """Wiring pin (DOM-bound, not node-runnable — labeled honestly): the hub
    entry takes the resolveMain ownership guard with entry/post-refresh/
    pre-load checkpoints via runGuardedStages, both resolveMain handoffs
    pass `owned`, and createShared keeps its guard-free call. Fails if the
    guard parameter, the staged checkpoints, or either handoff is removed."""
    import re

    app = (ROOT / "thyca" / "webui" / "pages" / "chat" / "app.js").read_text(encoding="utf-8")
    sidebar = (ROOT / "thyca" / "webui" / "pages" / "chat" / "team-sidebar.js").read_text(encoding="utf-8")
    helper = TEAM.read_text(encoding="utf-8")
    assert "afterTeamSession: async (detail, guard = () => true)" in app
    assert "runGuardedStages(guard," in app
    assert re.search(r"export async function runGuardedStages\(guard, stages\)", helper)
    assert "if (!owned()) return false;" in helper
    assert "hub.afterTeamSession(detail, owned)" in sidebar
    assert "hub.afterTeamSession(created, owned)" in sidebar
    # createShared keeps the guard-free call: exactly one remains.
    assert sidebar.count("hub.afterTeamSession(detail);") == 1


_OFFSET_EXPR = r"calc\(var\(--chat-brand-stripe\) \+ var\(--chat-brand-gap\)\)"


def _live_copy_offset() -> str:
    """The body text column: .live-copy padding-inline-start expression."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    match = re.search(r"\.live-copy\s*\{[^}]*padding-inline-start:\s*([^;]+);", css)
    assert match, ".live-copy offset rule missing"
    return match.group(1).strip()


def test_team_brand_header_needs_no_offset_rule() -> None:
    """The team header IS the legacy .chat-brand flex block (stripe +
    gap + body), so the name column == body column structurally — the old
    .team-message-head manual padding is gone and no team rule may
    re-introduce an inline offset on the brand header."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    assert not re.search(r"\.team-message-head", css), "parallel head CSS is gone"
    assert not re.search(r"\.team-message\s*>\s*\.chat-brand\s*\{[^}]*padding-inline-start", css)
    sidebar = SIDEBAR_SRC.read_text(encoding="utf-8")
    assert "team-message-head" not in sidebar
    cards = CARDS.read_text(encoding="utf-8")
    assert "chatBrandHeader" in cards


def test_team_decision_handoff_align_with_body_column() -> None:
    """Decision/handoff lines are direct article children with no box of
    their own: same text-column offset as .live-copy."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    expected = _live_copy_offset()
    for name in ("team-decision", "team-handoff"):
        match = re.search(
            rf"\.team-message\s*>\s*\.{name}\b[^{{]*\{{[^}}]*"
            r"padding-inline-start:\s*([^;]+);",
            css,
        )
        assert match, f".team-message > .{name} offset rule missing"
        assert match.group(1).strip() == expected


def test_team_card_box_starts_at_body_column() -> None:
    """Question/error cards are boxes: the whole box (margin, not padding)
    starts at the body text column so its border aligns with body text."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    match = re.search(
        r"\.team-message\s*>\s*\.team-card\s*\{[^}]*"
        r"margin-inline-start:\s*([^;]+);",
        css,
    )
    assert match, ".team-message > .team-card box-offset rule missing"
    assert match.group(1).strip() == _live_copy_offset()


def test_team_footer_not_double_padded() -> None:
    """The footer already aligns via the shared .message-assistant rule;
    no team-scoped footer padding may stack on top of it."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    shared = re.findall(r"\.message-assistant\s*>\s*footer\s*\{", css)
    assert len(shared) == 1, shared
    assert _live_copy_offset() in re.search(
        r"\.message-assistant\s*>\s*footer\s*\{[^}]*\}", css
    ).group(0)
    assert not re.search(
        r"\.team-message[^{]*footer[^{]*\{[^}]*padding-inline-start", css
    ), "team footer must not add a second inline padding"


def test_team_exchange_summary_stays_full_width() -> None:
    """The collapsed exchange renders at conversation root (never inside a
    team article) as a centered rule line: intentionally full-width, so its
    summary takes no text-column offset. Its rows keep their own tint bar
    and already align name with body."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    summary = re.search(r"\.team-exchange\s*>\s*summary\s*\{[^}]*\}", css)
    assert summary, ".team-exchange > summary rule missing"
    assert "padding-inline-start" not in summary.group(0)
    assert re.search(
        r"\.team-exchange-row\s*\{(?=[^}]*border-inline-start:)(?=[^}]*padding-inline-start:)[^}]*\}",
        css,
    ), ".team-exchange-row must keep its own bar + inset"


def test_team_alignment_leaves_legacy_rules_untouched() -> None:
    """Legacy (non-team) selectors keep exactly one offset each and no
    team selector leaks into them: plain sessions render byte-identical."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    expected = _live_copy_offset()
    for selector in (
        r"\.live-copy",
        r"\.message-assistant\s*>\s*footer",
        r"\.usage-row",
        r"\.thinking-note",
        r"\.reply-live",
    ):
        blocks = re.findall(selector + r"\s*\{[^}]*\}", css)
        hits = [block for block in blocks if "padding-inline-start" in block]
        assert len(hits) == 1, (selector, len(hits))
        assert expected in hits[0], selector
    # Every rule carrying the text-column offset belongs to a team scope or
    # to one of the five legacy selectors above — nothing else may shift.
    for selector_text in re.findall(
        r"([^{}]+)\{[^}]*padding-inline-start:\s*" + _OFFSET_EXPR + r"[^}]*\}",
        css,
    ):
        lowered = selector_text.lower()
        assert "team" in lowered or any(
            legacy in selector_text
            for legacy in (
                ".live-copy",
                ".message-assistant",
                ".usage-row",
                ".thinking-note",
                ".reply-live",
            )
        ), selector_text


# --- Second pass: old-UI anatomy (agents-primary sidebar, legacy brand) ---

_FAKE_DOM = """
class FakeNode {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this._style = {};
    this.style = {
      setProperty: (k, v) => { this._style[k] = String(v); },
      getPropertyValue: (k) => this._style[k] || "",
    };
    this.className = "";
    this._text = "";
    this._html = "";
    this.hidden = false;
    this.id = "";
    this.type = "";
    this.value = "";
    this.checked = false;
    this.dateTime = "";
  }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() {
    return this._text + this.children.map((c) =>
      (c && typeof c.textContent === "string") ? c.textContent : "").join("");
  }
  set innerHTML(v) { this.children = []; this._text = ""; this._html = String(v); }
  get innerHTML() { return this._html; }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return Object.hasOwn(this.attributes, k) ? this.attributes[k] : null; }
  get classList() {
    const el = this;
    const list = () => String(el.className || "").split(/\s+/).filter(Boolean);
    return {
      add: (...cs) => { el.className = [...new Set([...list(), ...cs])].join(" "); },
      remove: (...cs) => { el.className = list().filter((c) => !cs.includes(c)).join(" "); },
      toggle: (c, force) => {
        const has = list().includes(c);
        const on = force === undefined ? !has : !!force;
        if (on && !has) el.classList.add(c);
        if (!on && has) el.classList.remove(c);
        return on;
      },
      contains: (c) => list().includes(c),
    };
  }
  addEventListener() {}
  append(...nodes) {
    for (const n of nodes) this.children.push(typeof n === "string" ? { textContent: n } : n);
  }
  appendChild(n) { this.append(n); return n; }
  insertBefore(n, ref) {
    const i = this.children.indexOf(ref);
    if (i < 0) this.children.push(n); else this.children.splice(i, 0, n);
    return n;
  }
  querySelector(sel) { return queryAll(this, sel)[0] || null; }
  querySelectorAll(sel) { return queryAll(this, sel); }
}
function matchesPart(el, part) {
  if (!el || !el.tagName) return false;
  if (part.startsWith(".")) return String(el.className || "").split(/\\s+/).includes(part.slice(1));
  return String(el.tagName).toLowerCase() === part.toLowerCase();
}
function queryAll(root, sel) {
  const out = [];
  const walk = (el) => {
    for (const child of el.children || []) {
      if (child && child.tagName && matchesPart(child, sel)) out.push(child);
      if (child && child.children) walk(child);
    }
  };
  walk(root);
  return out;
}
globalThis.document = {
  createElement: (tag) => new FakeNode(tag),
  createElementNS: (ns, tag) => new FakeNode(tag),
  createTextNode: (text) => ({ textContent: String(text) }),
};
"""


def _cards(node: str, body: str) -> object:
    """Run node with the fake-DOM prelude + team-cards.js (which imports
    cleanly: markdown/format/team-data/chat-thinking/chat-view touch the DOM
    only inside functions)."""
    source = (
        _FAKE_DOM
        + f"import * as cards from '{CARDS.as_posix()}';\n"
        + f"const main = async () => {{ {body} }};\n"
        + "console.log(JSON.stringify(await main()));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_team_brand_reuses_legacy_chat_brand(node: str) -> None:
    """The bot header is the legacy .chat-brand anatomy — stripe, lockup
    with h2 name, quill, italic status line — with per-bot tint through the
    existing --chat-brand-accent variable. No .team-message-head remains."""
    value = _cards(node,
        "const a = cards.teamArticleFor({role: 'assistant', content: 'Chào',"
        " meta: {bot_id: 'coder', bot_name: 'Coder', bot_state: 'đang code',"
        " bot_tint: 'botanical'}});"
        " const head = a.querySelector('.chat-brand');"
        " return {"
        " article: a.className,"
        " head: head && head.className,"
        " bot: head && head.dataset.bot,"
        " stripe: !!a.querySelector('.chat-brand-stripe'),"
        " lockup: !!a.querySelector('.chat-brand-lockup'),"
        " name: a.querySelector('h2') && a.querySelector('h2').textContent,"
        " quill: !!a.querySelector('.chat-brand-quill'),"
        " status: a.querySelector('.chat-brand-status')"
        " && a.querySelector('.chat-brand-status').textContent,"
        " headTint: head && head._style['--chat-brand-accent'],"
        " legacyHead: !!a.querySelector('.team-message-head'),"
        " legacyName: !!a.querySelector('.team-message-name'),"
        " lineTag: a.querySelector('.chat-brand-line')"
        " && a.querySelector('.chat-brand-line').tagName,"
        "};")
    assert "team-message" in value["article"]
    assert value["head"] == "chat-brand"
    assert value["bot"] == "coder"
    assert value["stripe"] is True
    assert value["lockup"] is True
    assert value["name"] == "Coder"
    assert value["quill"] is True
    assert value["status"] == "đang code"
    assert value["headTint"] == "var(--color-botanical)"
    assert value["legacyHead"] is False
    assert value["legacyName"] is False
    assert value["lineTag"] == "P"  # no reasoning → plain status line


def test_team_brand_thyca_keeps_ink_and_default_status(node: str) -> None:
    """Thyca rows keep the legacy --chat-brand-ink name (no inline color)
    and read the legacy default status when the backend sends no state."""
    value = _cards(node,
        "const a = cards.teamArticleFor({role: 'assistant', content: 'Ừ',"
        " meta: {bot_id: 'thyca'}});"
        " const h2 = a.querySelector('h2');"
        " return [h2 && h2.textContent,"
        " h2 && h2._style.color,"
        " a.querySelector('.chat-brand-status')"
        " && a.querySelector('.chat-brand-status').textContent,"
        " a.querySelector('.chat-brand')._style['--chat-brand-accent']];")
    assert value[0] == "Thyca"
    assert value[1] is None  # no inline color → legacy ink rule wins
    assert value[2] == "đã viết"
    assert value[3] == "var(--color-accent)"


def test_team_brand_status_line_toggles_thinking(node: str) -> None:
    """Reasoning on a bot message renders the thinking note behind an
    expandable (button) status line; without reasoning the line stays a
    plain paragraph."""
    value = _cards(node,
        "const withThought = cards.teamArticleFor({role: 'assistant',"
        " content: 'Xong', reasoning: 'vì sao', meta: {bot_id: 'coder'}});"
        " const plain = cards.teamArticleFor({role: 'assistant',"
        " content: 'Xong', meta: {bot_id: 'coder'}});"
        " return [!!withThought.querySelector('.thinking-note'),"
        " withThought.querySelector('.chat-brand-line').tagName,"
        " !!plain.querySelector('.thinking-note'),"
        " plain.querySelector('.chat-brand-line').tagName];")
    assert value == [True, "BUTTON", False, "P"]


def test_team_brand_no_warm_glow_and_no_article_bar() -> None:
    """r9 removed the ::before wash for team; the stripe inside the header
    carries the tint, so no article border bar and no parallel head CSS."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    assert re.search(
        r"\.team-message\s+\.chat-brand-body::before\s*\{[^}]*content:\s*none",
        css,
    ), "wash-kill rule missing"
    for gone in ("team-message-head", "team-message-name", "team-message-state"):
        assert gone not in css, gone
    assert not re.search(r"\.team-message\s*\{[^}]*border-inline-start", css)


def test_team_status_label_defaults_to_legacy(node: str) -> None:
    value = _run(node,
        "return [api.teamStatusLabel({bot_state: ' đang chạy '}),"
        " api.teamStatusLabel({}), api.teamStatusLabel(null),"
        " api.teamStatusLabel({bot_state: '   '})];")
    assert value == ["đang chạy", "đã viết", "đã viết", "đã viết"]


def test_session_meta_line_mirrors_legacy(node: str) -> None:
    value = _run(node,
        "return [api.sessionMetaLine('Hôm qua', 10),"
        " api.sessionMetaLine('Hôm qua', 0),"
        " api.sessionMetaLine('Hôm qua', null),"
        " api.sessionMetaLine('', 3)];")
    assert value == ["Hôm qua · 10 lượt", "Hôm qua", "Hôm qua", "3 lượt"]


def test_room_participant_names_follow_roster(node: str) -> None:
    session = {"bots": ["thyca", "coder", "ghost"]}
    roster = [{"id": "thyca", "name": "Thyca"}, {"id": "coder", "name": "Coder"}]
    value = _run(node,
        f"return api.roomParticipantNames({json.dumps(session)}, {json.dumps(roster)});")
    assert value == ["Thyca", "Coder", "ghost"]
    assert _run(node, "return api.roomParticipantNames({}, []);") == []


def test_history_for_bot_excludes_dm_and_shared(node: str) -> None:
    sessions = [
        {"id": "dm", "bots": ["thyca"], "dm": "thyca",
         "updated_at": "2026-06-01T00:00:00Z"},
        {"id": "old", "bots": ["thyca"], "updated_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "bots": ["thyca"], "updated_at": "2026-05-01T00:00:00Z"},
        {"id": "shared", "bots": ["thyca", "b2"],
         "updated_at": "2026-07-01T00:00:00Z"},
        {"id": "other", "bots": ["b2"], "updated_at": "2026-07-01T00:00:00Z"},
        # Legacy pre-team room: no bots list at all, still thyca's own.
        {"id": "legacy", "updated_at": "2026-03-01T00:00:00Z"},
    ]
    value = _run(node,
        f"const sessions = {json.dumps(sessions)};"
        " return [api.historyForBot(sessions, 'thyca', 'dm').map(s => s.id),"
        " api.historyForBot(sessions, 'b2', '').map(s => s.id),"
        " api.historyForBot(sessions, 'nobody', ''),"
        " api.historyForBot(sessions, '', '')];")
    assert value[0] == ["new", "legacy", "old"]  # newest first, DM + shared out
    assert value[1] == ["other"]  # other bots: only when any exist
    assert value[2] == []
    assert value[3] == []


def test_sidebar_agents_primary_no_level_two() -> None:
    """Agents replace sessions as the primary list: legacy row classes,
    no level-2 picker screen, no intermediate labels or toggles."""
    src = SIDEBAR_SRC.read_text(encoding="utf-8")
    # Legacy anatomy reused for agent rows.
    assert '"session-row"' in src
    assert '"session-item"' in src
    assert '"session-name"' in src
    assert '"Đội"' in src
    # The level-2 session-picker screen is gone entirely.
    for gone in ("Tất cả phiên", "Cửa sổ chính", "Phiên khác",
                 "team-nav-back", "level === 2", "level = 2",
                 "is-main", "sharedToggleButton", "allSessionsButton",
                 "openTeam", "showLegacy", "showTeamView",
                 "partitionBotSessions"):
        assert gone not in src, gone
    # Single-level structure that replaces it.
    for kept in ("Phiên chung", "Lịch sử phiên", "session-participants",
                 "currentTeamBot", "historyForBot", "team-shared-create"):
        assert kept in src, kept


def test_sidebar_picker_hidden_unless_picking() -> None:
    """No empty picker pill: the actions box carries [hidden] unless the
    user is actively picking bots for a shared room."""
    src = SIDEBAR_SRC.read_text(encoding="utf-8")
    assert "actions.hidden = !sharedOpen" in src
    assert "team-shared-picker" in src


def test_strip_in_site_header_hidden_under_two_bots() -> None:
    """#team-strip lives in .site-header before the header-line (never in
    .chat-canvas), and hides entirely for rooms with fewer than 2 bots."""
    import re

    html = INDEX_HTML.read_text(encoding="utf-8")
    header = re.search(r'<header class="site-header">(.*?)</header>', html, re.S).group(1)
    assert 'id="team-strip"' in header
    assert header.index('id="team-strip"') < header.index("header-line")
    canvas = html.split('class="chat-canvas"', 1)[1]
    assert 'id="team-strip"' not in canvas
    strip = STRIP_SRC.read_text(encoding="utf-8")
    assert "names.length < 2" in strip


def test_team_nav_reuses_legacy_session_shell() -> None:
    """#team-nav carries the legacy .sessions class (layout, heading
    padding, mobile pick pattern from kit.css); the shared section reuses
    .sessions-heading + .new-session instead of parallel chrome."""
    import re

    html = INDEX_HTML.read_text(encoding="utf-8")
    assert re.search(r'<section class="sessions" id="team-nav"', html)
    assert 'id="team-nav-back"' not in html
    assert 'id="team-bot-list"' in html
    assert 'id="team-shared-list"' in html
    assert 'id="team-history"' in html
    assert re.search(r'<div id="team-nav-actions" hidden>', html)
    assert re.search(
        r'<button class="new-session" id="team-shared-create"', html)


def test_team_css_audit_deleted_vs_kept() -> None:
    """Second-pass audit pin: parallel styles deleted, irreducible
    minimum kept (each kept rule has no legacy equivalent)."""
    css = CHAT_CSS.read_text(encoding="utf-8")
    deleted = ("team-bot-item", "team-bot-name", "team-bot-status",
               "team-actions", "team-secondary-title", "team-unavailable",
               "team-shared-title", "team-heading", "team-nav-back",
               "session-row.is-main")
    for gone in deleted:
        assert gone not in css, gone
    kept = (".team-bot-dot",  # no legacy tint dot
            ".team-unread",  # no legacy unread badge
            ".team-strip[hidden]",  # flex outranks hidden (legacy pattern)
            ".session-participants",  # no legacy third line
            ".team-history",  # no legacy collapsible group
            ".team-shared-picker",  # no legacy checkbox picker card
            ".team-shared-option",
            ".team-card",  # question/error cards
            ".team-exchange",  # inter-bot collapse
            ".team-decision",
            ".mention-popup",  # @ popup
            ".bot-drawer",  # read-only profile
            ".composer-team-hint",
            )
    for keep in kept:
        assert keep in css, keep
