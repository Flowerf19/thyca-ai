"""Node tests for Team v1 frontend pure helpers.

Covers thyca/webui/pages/chat/team-data.js (tint fallback, bot normalize,
session filter, mention filter, exchange split, delegation tree, bot cost,
candidate queue) plus the re-exports through cost-data.js / trace-data.js.
DOM-free: team-data.js has no DOM or fetch, so it imports cleanly under node.
Mirrors the eval style of tests/test_webui_format.py.
"""
from __future__ import annotations

import json
import re
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
KIT_CSS = ROOT / "thyca" / "webui" / "shared" / "css" / "kit.css"
TOKENS_CSS = ROOT / "thyca" / "webui" / "shared" / "css" / "tokens.css"
AVATAR = ROOT / "thyca" / "webui" / "shared" / "js" / "bot-avatar.js"
AVATAR_CSS = ROOT / "thyca" / "webui" / "shared" / "css" / "bot-avatar.css"
CHAT_BOT = ROOT / "thyca" / "webui" / "shared" / "js" / "chat-bot.js"
TRANSCRIPT = ROOT / "thyca" / "webui" / "pages" / "chat" / "transcript.js"
LIVE = ROOT / "thyca" / "webui" / "pages" / "chat" / "live-status.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _run(node: str, body: str, via: str = "team") -> object:
    module = {"team": TEAM, "mention": MENTION, "cost": COST_DATA,
              "trace": TRACE_DATA, "unread": UNREAD, "avatar": AVATAR,
              "chatbot": CHAT_BOT}[via]
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


def _strip(node: str, body: str) -> object:
    """Run node with the fake-DOM prelude + team-strip.js (DOM is touched
    only inside its functions, so the import is clean)."""
    source = (
        _FAKE_DOM
        + f"import * as strip from '{STRIP_SRC.as_posix()}';\n"
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


def _transcript(node: str, body: str) -> object:
    """Run node with the fake-DOM prelude + transcript.js (which imports
    cleanly: DOM is touched only inside its functions)."""
    source = (
        _FAKE_DOM
        + f"import * as transcript from '{TRANSCRIPT.as_posix()}';\n"
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


def _live(node: str, body: str) -> object:
    """Run node with the fake-DOM prelude + live-status.js."""
    source = (
        _FAKE_DOM
        + f"import * as live from '{LIVE.as_posix()}';\n"
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


def _sidebar(node: str, body: str) -> object:
    """Run node with the fake-DOM prelude + team-sidebar.js. The body
    stubs document/fetch/localStorage, then drives initTeamSidebar."""
    source = (
        _FAKE_DOM
        + f"import * as sidebar from '{SIDEBAR_SRC.as_posix()}';\n"
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
        " quill: !!a.querySelector('.chat-brand-avatar'),"
        " status: a.querySelector('.chat-brand-status')"
        " && a.querySelector('.chat-brand-status').textContent,"
        " headTint: head && head._style['--chat-brand-accent'],"
        " legacyHead: !!a.querySelector('.team-message-head'),"
        " legacyName: !!a.querySelector('.team-message-name'),"
        " lineTag: a.querySelector('.chat-brand-line')"
        " && a.querySelector('.chat-brand-line').tagName,"
        "};")
    assert "team-message" in value["article"]
    assert value["bot"] == "coder"
    assert value["stripe"] is True
    assert value["lockup"] is True
    assert value["name"] == "Coder"
    assert value["quill"] is True
    assert value["status"] == "đang code"
    assert value["headTint"] == (
        "var(--avatar-tint, var(--team-tint, var(--color-accent)))")
    assert value["head"] == "chat-brand avatar-variant-single-flag"
    assert value["legacyHead"] is False
    assert value["legacyName"] is False
    assert value["lineTag"] == "P"  # no reasoning → plain status line


def test_team_brand_thyca_keeps_ink_and_default_status(node: str) -> None:
    """Thyca rows keep the shared accent var (no inline color) and read
    the legacy default status when the backend sends no state."""
    value = _cards(node,
        "const a = cards.teamArticleFor({role: 'assistant', content: 'Ừ',"
        " meta: {bot_id: 'thyca'}});"
        " const h2 = a.querySelector('h2');"
        " return [h2 && h2.textContent,"
        " h2 && h2._style.color,"
        " a.querySelector('.chat-brand-status')"
        " && a.querySelector('.chat-brand-status').textContent,"
        " a.querySelector('.chat-brand')._style['--chat-brand-accent'],"
        " a.querySelector('.chat-brand').className];")
    assert value[0] == "Thyca"
    assert value[1] is None  # no inline color → shared accent var wins
    assert value[2] == "đã viết"
    assert value[3] == (
        "var(--avatar-tint, var(--team-tint, var(--color-accent)))")
    assert value[4] == "chat-brand avatar-variant-star"


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


def test_unified_sessions_merge_private_and_shared(node: str) -> None:
    """One list per bot: private sessions and shared sessions merged,
    newest first, with no split. The same shared session appears in every
    participating bot's list; Thyca's pre-team (legacy) sessions are simply
    Thyca's sessions in that one list.

    SUPERSEDED expectation: this test previously asserted the DM is in the
    list. Per the user decision behind this fix, the DM row is excluded —
    the bot row itself opens the DM (/api/bots/<id>/dm), so a DM row under
    it duplicated the bot row (the user saw "two Thyca entries" and asked
    why Thyca has 2 sessions). Shared, private non-DM, and legacy non-DM
    rows stay exactly as before."""
    sessions = [
        {"id": "dm", "bots": ["thyca"], "dm": "thyca",
         "updated_at": "2026-06-01T00:00:00Z"},
        {"id": "old", "bots": ["thyca"], "updated_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "bots": ["thyca"], "updated_at": "2026-05-01T00:00:00Z"},
        {"id": "shared", "bots": ["thyca", "b2"],
         "updated_at": "2026-07-02T00:00:00Z"},
        {"id": "other", "bots": ["b2"], "updated_at": "2026-07-01T00:00:00Z"},
        # Legacy pre-team room: no bots list at all, still thyca's own.
        {"id": "legacy", "updated_at": "2026-03-01T00:00:00Z"},
    ]
    value = _run(node,
        f"const sessions = {json.dumps(sessions)};"
        " return [api.unifiedSessionsForBot(sessions, 'thyca').map(s => s.id),"
        " api.unifiedSessionsForBot(sessions, 'b2').map(s => s.id),"
        " api.unifiedSessionsForBot(sessions, 'nobody'),"
        " api.unifiedSessionsForBot(sessions, '')];")
    # Solo + legacy + shared together in ONE list, newest first; the
    # dm-marked DM (\"dm\") is excluded — the bot row is its entry.
    assert value[0] == ["shared", "new", "legacy", "old"]
    # The same shared session is in b2's one list too, with b2's private one.
    assert value[1] == ["shared", "other"]
    assert value[2] == []
    assert value[3] == []


def test_unified_sessions_dm_only_bot_lists_nothing(node: str) -> None:
    """A bot whose only session is its DM yields an empty unified list:
    the bot row is the DM entry, so there is nothing left to list."""
    sessions = [
        {"id": "dm", "bots": ["thyca"], "dm": "thyca",
         "updated_at": "2026-06-01T00:00:00Z"},
    ]
    value = _run(node,
        f"return api.unifiedSessionsForBot({json.dumps(sessions)}, 'thyca');")
    assert value == []


def test_sidebar_agents_primary_no_level_two() -> None:
    """Agents replace sessions as the primary list: legacy row classes,
    no level-2 picker screen, no intermediate labels or toggles."""
    src = SIDEBAR_SRC.read_text(encoding="utf-8")
    # Legacy anatomy reused for agent rows.
    assert '"session-row"' in src
    # Bot/session rows subtype the legacy item (presentation pass,
    # 2026-10-14): same anatomy, team-bot-item / team-session-item.
    assert '"session-item team-bot-item"' in src
    assert '"session-item team-session-item"' in src
    assert '"session-name"' in src
    assert '"Đội"' in src
    # The level-2 session-picker screen is gone entirely.
    for gone in ("Tất cả phiên", "Cửa sổ chính", "Phiên khác",
                 "team-nav-back", "level === 2", "level = 2",
                 "is-main", "sharedToggleButton", "allSessionsButton",
                 "openTeam", "showLegacy", "showTeamView",
                 "partitionBotSessions"):
        assert gone not in src, gone
    # The split sections are gone: no separate shared section, no collapsed
    # history group, no shared-only empty text, no chung/riêng labels.
    for gone in ("Lịch sử phiên", "Chưa có phiên chung.",
                 "team-shared-list", "team-history", "historyForBot",
                 "historyGroup", "roomRow"):
        assert gone not in src, gone
    html = INDEX_HTML.read_text(encoding="utf-8")
    # No section heading for a separate shared list (the create flow's
    # picker/aria wording for a new shared session is not a section).
    for gone in ("<h2>Phiên chung</h2>", 'id="team-shared-list"', 'id="team-history"'):
        assert gone not in html, gone
    # The separate "Phiên" sessions section is gone entirely: no heading,
    # no container, no second list — the bot list is the only sidebar list.
    for gone in ("<h2>Phiên</h2>", 'id="team-sessions"',
                 'id="team-sessions-heading"', 'id="team-session-list"'):
        assert gone not in html, gone
    assert 'id="team-session-list"' not in src
    # Single unified list that replaces them.
    for kept in ("team-cluster", "currentTeamBot",
                 "unifiedSessionsForBot", "unifiedRow",
                 "team-shared-create"):
        assert kept in src, kept
    assert 'id="team-bot-list"' in html
    # Participant annotation only for multi-bot sessions, absent for solo.
    assert "names.length > 1" in src
    # Participant names ride the single muted time line now (supersedes
    # the mockup .stack third row, presentation pass 2026-10-14).
    assert "names.length > 1 ? names.join(" in src
    assert 'className = "stack"' not in src


def test_sidebar_sessions_render_under_active_bot_row(node: str) -> None:
    """Structural order: ONE list (#team-bot-list) — bot rows first
    (the active bot's solo sessions in its cluster), then group chats
    at bot level. A group row keeps its participant annotation."""
    value = _sidebar(node,
        "FakeNode.prototype.replaceChildren = function (...nodes)"
        " { this.children = [...nodes]; };"
        " FakeNode.prototype.remove = function () {};"
        " Object.defineProperty(FakeNode.prototype, 'childElementCount'," 
        "  { get() { return this.children.length; } });"
        " FakeNode.prototype.removeAttribute = function (k)"
        " { delete this.attributes[k]; };"
        " FakeNode.prototype.querySelector = function (sel) {"
        "  if (!sel.startsWith('#')) return null;"
        "  const find = (el) => { for (const c of el.children || []) {"
        "   if (c && c.id === sel.slice(1)) return c;"
        "   const r = c && c.children ? find(c) : null;"
        "   if (r) return r; } return null; };"
        "  return find(this); };"
        " const section = new FakeNode('section');"
        " section.id = 'team-nav'; section.hidden = true;"
        " const title = new FakeNode('h2'); title.id = 'team-nav-title';"
        " const botNav = new FakeNode('nav'); botNav.id = 'team-bot-list';"
        " const actions = new FakeNode('div');"
        " actions.id = 'team-nav-actions'; actions.hidden = true;"
        " const createBtn = new FakeNode('button');"
        " createBtn.id = 'team-shared-create';"
        " section.append(title, botNav, actions);"
        " const registry = { '#team-nav': section,"
        "  '#team-bot-list': botNav, '#team-nav-actions': actions,"
        "  '#team-shared-create': createBtn };"
        " document.querySelector = (sel) => registry[sel] || null;"
        " document.addEventListener = () => {};"
        " globalThis.window = { addEventListener: () => {} };"
        " globalThis.localStorage = { getItem: () => null,"
        "  setItem: () => {} };"
        " globalThis.fetch = async () => ({ ok: true, status: 200,"
        "  json: async () => ({ bots: ["
        "   {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
        "   {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) });"
        " await sidebar.initTeamSidebar({ state: { activeId: 'shared',"
        "  detail: { id: 'shared', bots: ['thyca', 'coder'] },"
        "  sessions: ["
        "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
        "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2},"
        "   {id: 'shared', title: 'Phiên chung', bots: ['thyca', 'coder'],"
        "    updated_at: '2026-07-02T00:00:00Z', turns: 3},"
        "   {id: 'coder-only', title: 'Riêng Coder', bots: ['coder'],"
        "    updated_at: '2026-07-01T00:00:00Z', turns: 1}] },"
        " loadSession: async () => {}, afterTeamSession: async () => {},"
        " teamRefresh: async () => {} });"
        " const flat = [];"
        " for (const item of botNav.children) {"
        "  if (item.className === 'session-row') { flat.push(item); continue; }"
        "  for (const row of item.children) {"
        "   if (row.className === 'team-cluster-sessions')"
        "    flat.push(...row.children); else flat.push(row); } }"
        " return flat.map((row) => ({"
        "  bot: (row.children[0] && row.children[0].dataset.botId) || '',"
        "  sid: (row.children[0] && row.children[0].dataset.sessionId) || '',"
        "  text: row.textContent }));")
    # Pinned letters lead: Thyca is pinned by design so its cluster tops
    # the list even though the group (07-02) and Coder (07-01) have newer
    # activity than Thyca's DM (06-01). Within the open cluster the bot
    # row is followed by its flap (Đổi tên/Xóa/+ Phiên mới). Coder's
    # private session is NOT in Thyca's cluster. The DM session ("dm")
    # renders NO row: the Thyca bot row itself is the DM entry.
    assert [(row["bot"], row["sid"]) for row in value] == [
        ("thyca", ""), ("", ""), ("", "shared"), ("coder", "")]
    # Shared annotation still present on the group row's subline.
    assert "Coder" in value[2]["text"] and "Thyca" in value[2]["text"]
    assert "+ Phiên mới" in value[1]["text"]


def test_sidebar_bot_with_only_dm_renders_zero_session_rows(node: str) -> None:
    """Only-DM bot: expanding it renders zero session rows and no empty
    text (clean), while the bot row still aggregates the DM's unread dot
    (botUnread runs over sessionsForBot, DM included — unaffected by the
    unified-list exclusion)."""
    value = _sidebar(node,
        "FakeNode.prototype.replaceChildren = function (...nodes)"
        " { this.children = [...nodes]; };"
        " FakeNode.prototype.remove = function () {};"
        " Object.defineProperty(FakeNode.prototype, 'childElementCount'," 
        "  { get() { return this.children.length; } });"
        " FakeNode.prototype.removeAttribute = function (k)"
        " { delete this.attributes[k]; };"
        " FakeNode.prototype.querySelector = function (sel) {"
        "  if (!sel.startsWith('#')) return null;"
        "  const find = (el) => { for (const c of el.children || []) {"
        "   if (c && c.id === sel.slice(1)) return c;"
        "   const r = c && c.children ? find(c) : null;"
        "   if (r) return r; } return null; };"
        "  return find(this); };"
        " const section = new FakeNode('section');"
        " section.id = 'team-nav'; section.hidden = true;"
        " const title = new FakeNode('h2'); title.id = 'team-nav-title';"
        " const botNav = new FakeNode('nav'); botNav.id = 'team-bot-list';"
        " const actions = new FakeNode('div');"
        " actions.id = 'team-nav-actions'; actions.hidden = true;"
        " const createBtn = new FakeNode('button');"
        " createBtn.id = 'team-shared-create';"
        " section.append(title, botNav, actions);"
        " const registry = { '#team-nav': section,"
        "  '#team-bot-list': botNav, '#team-nav-actions': actions,"
        "  '#team-shared-create': createBtn };"
        " document.querySelector = (sel) => registry[sel] || null;"
        " document.addEventListener = () => {};"
        " globalThis.window = { addEventListener: () => {} };"
        " globalThis.localStorage = { getItem: (k) => k === 'thyca.team.seen.v1'"
        "  ? JSON.stringify({dm: {ts: '2026-01-01T00:00:00Z', count: 1}}) : null,"
        "  setItem: () => {} };"
        " globalThis.fetch = async () => ({ ok: true, status: 200,"
        "  json: async () => ({ bots: ["
        "   {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
        "   {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) });"
        " await sidebar.initTeamSidebar({ state: { activeId: 'dm',"
        "  detail: { id: 'dm', bots: ['thyca'] },"
        "  sessions: ["
        "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
        "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2}] },"
        " loadSession: async () => {}, afterTeamSession: async () => {},"
        " teamRefresh: async () => {} });"
        " const flat = [];"
        " for (const item of botNav.children) {"
        "  if (item.className === 'session-row') { flat.push(item); continue; }"
        "  for (const row of item.children) {"
        "   if (row.className === 'team-cluster-sessions')"
        "    flat.push(...row.children); else flat.push(row); } }"
        " return flat.map((row) => ({"
        "  bot: (row.children[0] && row.children[0].dataset.botId) || '',"
        "  sid: (row.children[0] && row.children[0].dataset.sessionId) || '',"
        "  text: row.textContent,"
        "  kids: (row.children[0] ? row.children[0].children : [])"
        "   .map((c) => c.className) }));")
    # Exactly one Thyca entry (the bot row); zero session rows under it.
    assert [(row["bot"], row["sid"]) for row in value] == [
        ("thyca", ""), ("", ""), ("coder", "")]
    # No empty-state text for the only-DM bot.
    assert all("Chưa có phiên." not in row["text"] for row in value)
    # The DM's unread still dots the Thyca bot row.
    assert any("team-unread" in cls for cls in value[0]["kids"])


def test_sidebar_picker_hidden_unless_picking() -> None:
    """No empty picker pill: the actions box carries [hidden] unless the
    user is actively picking bots for a shared room."""
    src = SIDEBAR_SRC.read_text(encoding="utf-8")
    assert "actions.hidden = !sharedOpen" in src
    assert "team-shared-picker" in src


def test_strip_in_site_header_shows_single_bot() -> None:
    """#team-strip lives in .site-header (never in .chat-canvas) and
    renders even a single-bot room. Supersession: this replaces the
    earlier <2-bot hide, by user decision — a 1-bot DM hid the strip
    and left the header with no bot name at all. The strip now hides
    only when there are truly no participants."""
    import re

    html = INDEX_HTML.read_text(encoding="utf-8")
    header = re.search(r'<header class="site-header">(.*?)</header>', html, re.S).group(1)
    assert 'id="team-strip"' in header
    canvas = html.split('class="chat-canvas"', 1)[1]
    assert 'id="team-strip"' not in canvas
    strip = STRIP_SRC.read_text(encoding="utf-8")
    assert "names.length < 2" not in strip
    assert "names.length === 0" in strip


def test_chat_page_hides_version_line_other_pages_keep_it() -> None:
    """User decision: the date · Thyca · version line is hidden on the
    chat page only (it rendered clipped at the bottom-right of the
    shell). The element is removed from index.html rather than hidden
    by CSS; other pages keep their .header-line in .site-header, and
    navigation.js already guards #header-datetime / #header-edition
    with existence checks, so its absence cannot error."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "header-line" not in html
    assert 'id="header-datetime"' not in html
    assert 'id="header-edition"' not in html
    for other in ("memories.html", "dashboard.html", "settings.html",
                  "provider.html", "profile.html"):
        page = (INDEX_HTML.parent / other).read_text(encoding="utf-8")
        assert 'class="header-line"' in page, other
        assert 'id="header-datetime"' in page, other
        assert 'id="header-edition"' in page, other
    navigation = (
        INDEX_HTML.parent / "shared" / "js" / "navigation.js"
    ).read_text(encoding="utf-8")
    assert "if (headerTime)" in navigation
    assert "if (headerEdition)" in navigation
    # The dead chat-scoped bottom-right positioning must not linger.
    css = CHAT_CSS.read_text(encoding="utf-8")
    assert ".chat-shell .site-header .header-line" not in css


def test_strip_single_bot_dm_renders_name_and_hides_when_empty(node: str) -> None:
    """A 1-bot DM renders that bot's name in its tint (busy per current
    rules); an explicitly empty roster is the only hidden case."""
    value = _strip(node,
        "FakeNode.prototype.replaceChildren = function (...nodes)"
        " { this.children = [...nodes]; };"
        " FakeNode.prototype.remove = function () {};"
        " Object.defineProperty(FakeNode.prototype, 'childElementCount'," 
        "  { get() { return this.children.length; } });"
        " const el = new FakeNode('p');"
        " document.querySelector = (sel) => sel === '#team-strip' ? el : null;"
        " strip.initTeamStrip({"
        " state: { detail: { bots: ['thyca'],"
        " running: true, failedBots: [] } },"
        " teamRoster: () => ["
        " {id: 'thyca', name: 'Thyca', tint: 'accent', status: 'idle'}],"
        " openBotDrawer: () => {} });"
        " strip.renderTeamStrip();"
        " const single = { hidden: el.hidden,"
        "  names: el.children.map((c) => ({ text: c.textContent,"
        "  cls: c.className, tint: c._style['--team-tint'],"
        "  kids: c.children.length })) };"
        " strip.initTeamStrip({"
        " state: { detail: { bots: [], running: false, failedBots: [] } },"
        " teamRoster: () => [], openBotDrawer: () => {} });"
        " strip.renderTeamStrip();"
        " return { single, emptyHidden: el.hidden,"
        "  emptyCount: el.children.length };")
    assert value["single"]["hidden"] is False
    assert value["single"]["names"] == [{
        "text": "Thyca", "cls": "team-strip-name is-busy",
        "tint": "var(--color-accent)", "kids": 0}]
    assert value["emptyHidden"] is True
    assert value["emptyCount"] == 0


def test_strip_names_tinted_plain_text_busy_and_error(node: str) -> None:
    """Strip spec: each bot name carries its own tint, a working bot gets
    .is-busy, an errored bot gets .is-error (never busy), and every name
    is rendered as plain text content (no markup inside the name)."""
    value = _strip(node,
        "FakeNode.prototype.replaceChildren = function (...nodes)"
        " { this.children = [...nodes]; };"
        " FakeNode.prototype.remove = function () {};"
        " Object.defineProperty(FakeNode.prototype, 'childElementCount'," 
        "  { get() { return this.children.length; } });"
        " const el = new FakeNode('p');"
        " document.querySelector = (sel) => sel === '#team-strip' ? el : null;"
        " strip.initTeamStrip({"
        " state: { detail: { bots: ['thyca', 'coder', 'ghost'],"
        " running: false, failedBots: [] } },"
        " teamRoster: () => ["
        " {id: 'thyca', name: 'Thyca', tint: 'accent', status: 'idle'},"
        " {id: 'coder', name: 'Coder', tint: 'botanical', status: 'running'},"
        " {id: 'ghost', name: 'Ghost', tint: 'ink', status: 'error'}],"
        " openBotDrawer: () => {} });"
        " strip.renderTeamStrip();"
        " return el.children.map((c) => ({ tag: c.tagName,"
        " text: c.textContent, cls: c.className,"
        " tint: c._style['--team-tint'], kids: c.children.length }));")
    assert [c["text"] for c in value] == ["Thyca", "Coder", "Ghost"]
    assert [c["tint"] for c in value] == [
        "var(--color-accent)", "var(--color-botanical)", "var(--color-ink)"]
    assert all(c["kids"] == 0 for c in value)  # plain text, no inner markup
    assert "is-busy" in value[1]["cls"].split()
    assert "is-busy" not in value[0]["cls"].split()
    assert "is-error" in value[2]["cls"].split()
    assert "is-busy" not in value[2]["cls"].split()


def test_strip_css_matches_r9_spec() -> None:
    """r9 strip type + busy sweep: 0.8rem reading, upright, per-bot tint as
    plain text; .is-busy is 600 with the sweep THROUGH the text
    (background-clip: text) at 2.4s, off under prefers-reduced-motion;
    .is-error is danger red with no animation. Tokens only."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    container = re.search(
        r"\.chat-shell \.site-header \.team-strip\s*\{[^}]*\}", css)
    assert container, ".team-strip container rule missing"
    assert "font-size: 0.8rem" in container.group(0)
    assert "font-family: var(--font-reading)" in container.group(0)
    assert "font-style: normal" in container.group(0)
    assert "italic" not in container.group(0)
    name = re.search(r"\.team-strip-name\s*\{[^}]*\}", css)
    assert name, ".team-strip-name rule missing"
    assert "background: transparent" in name.group(0)
    assert "color: var(--team-tint" in name.group(0)
    assert "border" not in name.group(0)  # plain text, not a boxy button
    busy = re.search(r"\.team-strip-name\.is-busy\s*\{[^}]*\}", css)
    assert busy, ".team-strip-name.is-busy rule missing"
    assert "font-weight: 600" in busy.group(0)
    assert "background-clip: text" in busy.group(0)
    assert "animation: team-shimmer 2.4s" in busy.group(0)
    assert re.search(
        r"@keyframes team-shimmer\s*\{[^}]*background-position", css)
    assert not re.search(r"\.team-strip-name\.is-running", css)
    assert not re.search(r"\.team-strip-name\.is-busy::after", css)
    error = re.search(r"\.team-strip-name\.is-error\s*\{[^}]*\}", css)
    assert error, ".team-strip-name.is-error rule missing"
    assert "var(--color-danger" in error.group(0)
    assert "animation: none" in error.group(0)
    reduced = re.search(
        r"@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*"
        r"\.team-strip-name\.is-busy\s*\{[^}]*animation:\s*none",
        css)
    assert reduced, "busy sweep must stop under prefers-reduced-motion"


def test_team_nav_reuses_legacy_session_shell() -> None:
    """#team-nav carries the legacy .sessions class (layout, heading
    padding, mobile pick pattern from kit.css); shared creation reuses
    .sessions-heading + .new-session instead of parallel chrome."""
    import re

    html = INDEX_HTML.read_text(encoding="utf-8")
    assert re.search(r'<section class="sessions" id="team-nav"', html)
    assert 'id="team-nav-back"' not in html
    assert 'id="team-bot-list"' in html
    assert 'id="team-session-list"' not in html
    assert 'id="team-sessions"' not in html
    assert 'id="team-shared-list"' not in html
    assert 'id="team-history"' not in html
    assert re.search(r'<div id="team-nav-actions" hidden>', html)
    # Shared creation keeps its entry point without a "Phiên" heading: the
    # legacy .new-session "+" sits in the bot list's (Đội) .sessions-heading
    # area, before the one bot list.
    assert re.search(
        r'<button class="new-session" id="team-shared-create"', html)
    assert html.index('id="team-nav-title"') < html.index('id="team-shared-create"')
    assert html.index('id="team-shared-create"') < html.index('id="team-bot-list"')


def test_team_css_audit_deleted_vs_kept() -> None:
    """Second-pass audit pin: parallel styles deleted, irreducible
    minimum kept (each kept rule has no legacy equivalent)."""
    css = CHAT_CSS.read_text(encoding="utf-8")
    # team-bot-item returned deliberately in the 2026-10-14 presentation
    # pass (avatar slot / paused state on the bot item); the mockup
    # .stack line left in the same pass (names ride the time line).
    deleted = ("team-bot-name", "team-bot-status",
               "team-actions", "team-secondary-title", "team-unavailable",
               "team-shared-title", "team-heading", "team-nav-back",
               "team-history", "session-row.is-main", "session-item .stack")
    for gone in deleted:
        assert gone not in css, gone
    kept = (".team-bot-dot",  # picker tint dot (compact context)
            ".team-avatar",  # avatar slot (initial on tint)
            ".team-cluster",  # bot + its sessions as one cluster
            ".team-unread",  # no legacy unread badge
            ".team-strip[hidden]",  # flex outranks hidden (legacy pattern)
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


def test_chat_title_head_has_no_solid_background_or_fade() -> None:
    """Session title is seamless with the shell paper texture.

    Supersedes test_chat_title_head_sticky_keeps_fade (r9 round 6 sticky +
    ::after fade). That fix assumed messages scroll under the title, but
    .chat-title-head is a flex sibling above .conversation-scroll inside
    .chat-canvas, so messages can never slide under it; the solid
    var(--color-paper-chat) band only hid the .notebook-shell texture and
    truncated botanical motifs at the head's bottom edge (reported as the
    leaf being cut). Pin the regression away: no solid background, no
    fade pseudo-element, no sticky on the head. The base rule (flex:none +
    padding at the top of chat.css) must remain."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    heads = re.findall(r"\.chat-title-head\s*\{[^}]*\}", css)
    assert heads, ".chat-title-head rule missing"
    assert any("flex: none" in block for block in heads), heads
    for block in heads:
        assert "background" not in block, block
        assert "position: sticky" not in block, block
        assert "var(--color-paper-chat)" not in block, block
    assert not re.search(r"\.chat-title-head::after", css), (
        ".chat-title-head::after fade must stay removed: it painted a "
        "solid-to-transparent band over the shell texture"
    )


def test_sidebar_avatar_slot_present() -> None:
    """Avatar design port (2026-10-14): SVG note avatars in the legacy
    icon column, composite team avatar for shared sessions."""
    src = SIDEBAR_SRC.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")
    for kept in ("team-avatar", "botMark", "botMarkGroup",
                 "avatarSlot", "sessionIcon", "is-paused"):
        assert kept in src or kept in css, kept
    assert "team-cluster-sessions" in css
    assert "shared/css/bot-avatar.css" in html


def test_normalize_bots_keeps_avatar_and_agent_status(node: str) -> None:
    value = _run(
        node,
        "return api.normalizeBots({bots: [{id: 'coder', avatar: 'dotted',"
        " agent_status: {state: 'thinking'}, system: false}]});",
    )
    assert value[0]["avatar"] == "dotted"
    assert value[0]["agentStatus"]["state"] == "thinking"


def test_bot_avatar_variants_states_and_team(node: str) -> None:
    value = _run(
        node,
        "const single = api.botMark({id: 'coder', avatar: 'single-flag', tint: 'botanical'});"
        " const err = api.botMark({id: 'x', agentStatus: {state: 'error'}});"
        " const run = api.botMark({id: 'r', agentStatus: {state: 'running'}});"
        " const team = api.botMarkGroup(["
        "  {id: 'a', tint: 'accent'}, {id: 'b', tint: 'ink'}]);"
        " return [single.includes('mk-wax'), single.includes('data-state=\"idle\"'),"
        "  single.includes('wax-body'), err.includes('data-state=\"error\"'),"
        "  run.includes('data-state=\"writing\"'), team.includes('gm-stack'),"
        "  api.avatarVariantFor({id: 'thyca-demo'}),"
        "  api.avatarStateFor({status: 'paused', agentStatus: {state: 'running'}})];",
        via="avatar",
    )
    assert value == [True, True, True, True, True, True, "dotted", "offline"]


def test_bot_avatar_css_has_no_design_hex() -> None:
    import re

    css = AVATAR_CSS.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), (
        "avatar CSS must resolve colours through shared tokens")
    assert "botLookAround" in css and "botBounce" in css


def test_chat_surfaces_and_imagery_unchanged() -> None:
    """v5 chrome reuses shared palette, distinct surface and artwork tokens;
    chat may place the shell but must not define a page-local theme.
    Canvas/title transparency and the shell texture remain preserved."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    kit = KIT_CSS.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for absent in ("--bg-paper", "--bg-card", "--border-paper",
                   "--text-main", "--text-muted", "--status-ring"):
        assert absent not in css, absent
    assert not re.search(r"--(?:color|note|background)-[\w-]+\s*:", css), (
        "shared palette/artwork tokens belong in tokens.css, not chat.css")
    for block in re.findall(
        r"\.chat-shell(?:\.is-rail)?(?: \.(?:sidebar|workspace))?\s*\{[^}]*\}", css
    ):
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|(?:rgba?|hsla?|oklch)\(|url\(", block), block
    selected = re.search(r"#team-bot-list \.session-item\.is-active\s*\{[^}]*\}", css)
    assert selected and "background-color: transparent" in selected.group(0)
    glow = re.search(
        r"#team-bot-list \.session-item\.is-active::before\s*\{[^}]*\}", css)
    assert glow and "var(--team-tint)" in glow.group(0)
    mention = re.search(r"\.mention-item\s*\{[^}]*\}", css)
    assert mention and "color: var(--chat-brand-ink)" in mention.group(0)
    canvas = re.findall(r"\.chat-canvas\s*\{[^}]*\}", css)
    assert canvas, ".chat-canvas rule missing"
    assert any("background: transparent" in block for block in canvas), canvas
    sidebar = re.findall(r"\.sidebar\s*\{[^}]*\}", kit)
    workspace = re.findall(r"\.workspace\s*\{[^}]*\}", kit)
    assert any("background: transparent" in block for block in sidebar), sidebar
    assert any("background: transparent" in block for block in workspace), workspace
    shell = re.search(r"\.notebook-shell\s*\{[^}]*\}", kit)
    assert shell and 'url("../../images/thyca-bg-paper.png")' in shell.group(0), shell
    tokens = TOKENS_CSS.read_text(encoding="utf-8")
    assert "--chat-brand-ink: #3d2a1f" in tokens
    blocks = re.findall(r"body\s*\{[^}]*\}", tokens)
    assert any("background-image" in block for block in blocks), (
        "outer desk gradient on body must be preserved")


def test_chat_avatar_variant_tint_map() -> None:
    """Every avatar variant maps to its reference note colour through a
    chat-scoped --avatar-tint, leaving card tint metadata untouched."""
    css = CHAT_CSS.read_text(encoding="utf-8")
    expected = {
        "star": "note-do",
        "hollow": "note-re",
        "single-flag": "note-mi",
        "double-flag": "note-fa",
        "dotted": "note-sol",
        "fermata": "note-la",
        "plain": "note-si",
    }
    for variant, note in expected.items():
        rule = f".chat-shell .avatar-variant-{variant}"
        assert rule in css, rule
        assert f"--avatar-tint: var(--{note})" in css, (variant, note)
    assert ".bot-drawer .avatar-variant-star" in css


def test_avatar_reference_tokens_and_css() -> None:
    """Shared tokens carry the Vintage Ink palette; avatar CSS resolves
    halo/offline/badge/flag/stem colours through them."""
    tokens = TOKENS_CSS.read_text(encoding="utf-8")
    for token in ("--note-do: #B8734B", "--note-re: #9E6B60",
                  "--note-mi: #C8A986", "--note-fa: #A5894E",
                  "--note-sol: #84796A", "--note-la: #8A5A40",
                  "--note-si: #7C5841", "--note-star: #BF8F34",
                  "--avatar-eye: #2B211B", "--avatar-eye-offline: #786D61",
                  "--status-offline: #B5A898",
                  "--avatar-star-offline: #BDB2A6",
                  "--avatar-hollow: #F7EDE2",
                  "--avatar-hollow-offline: #DFD5C8",
                  "--avatar-think: #8C7662", "--status-done: #4A8759",
                  "--status-waiting: #3E6B94", "--status-error: #B84239",
                  "--avatar-halo: rgba(190, 85, 48, 0.10)",
                  "--avatar-halo-ring: rgba(180, 90, 50, 0.42)",
                  "--avatar-crescent: rgba(31, 20, 14, 0.14)",
                  "--avatar-stamp-ink: #9E4020"):
        assert token in tokens, token
    css = AVATAR_CSS.read_text(encoding="utf-8")
    assert "fill: var(--avatar-tint, var(--team-tint, var(--color-accent)))" in css
    assert "stroke: var(--avatar-tint, var(--team-tint, var(--color-accent)))" in css
    star = re.search(r"\.avatar-star\s*\{[^}]*\}", css)
    assert star and "fill: var(--note-star)" in star.group(0), (
        "the tiny gold star is separate from Thyca's note-do body tint")
    assert "fill: var(--avatar-halo)" in css
    assert "stroke: var(--avatar-halo-ring)" in css
    assert "stroke-dasharray: 4 3.5" in css
    assert "fill: var(--avatar-crescent)" in css
    assert "fill: var(--avatar-hollow-offline)" in css
    assert "var(--status-offline)" in css
    assert "var(--avatar-eye-offline)" in css
    assert "var(--avatar-stamp-ink)" in css
    assert "mix-blend-mode: multiply" in css
    assert "isolation: isolate" in css
    assert "stroke-width: 1.1" in css and "stroke-width: 2.6" in css
    assert "stroke-width: 1.6" in css and "stroke-width: 1.4" in css
    assert "stroke-width: 1.35" in css and "stroke-width: 3.6" in css
    assert re.search(r"\.bot-team-pair \.avatar-team-stem \{\s*stroke-width: 3;", css)
    assert re.search(r"\.bot-team-pair \.avatar-beam \{\s*stroke-width: 4;", css)
    assert "var(--font-reading)" in css


def test_avatar_wax_mark_contract(node: str) -> None:
    """Wax-seal marks (thyca-bot-design.html): seeded blob per bot id,
    gold note glyph, state via data-state, selection via data-selected;
    team marks reuse the first member's seal."""
    value = _run(
        node,
        "const a = api.botMark({id: 'coder'});"
        " const a2 = api.botMark({id: 'coder'});"
        " const b = api.botMark({id: 'designer'});"
        " const sel = api.botMark({id: 'coder'}, {selected: true});"
        " const err = api.botMark({id: 'x', agentStatus: {state: 'error'}});"
        " const run = api.botMark({id: 'y', agentStatus: {state: 'running'}});"
        " const idle = api.botMark({id: 'z'});"
        " const team = api.botMarkGroup([{id: 'coder'}, {id: 'designer'}, {id: 'writer'}, {id: 'research'}]);"
        " const empty = api.botMarkGroup([]);"
        " const strip = (m) => m.replace(/wx\\d+/g, 'wx');"
        " return [a.includes('mk mk-wax'), a.includes('wax-body'),"
        "  a.includes('glyph'), a.includes('aria-hidden'),"
        "  strip(a) === strip(a2), strip(a) !== strip(b),"
        "  sel.includes('data-selected'),"
        "  !a.includes('data-selected=\"true\"'),"
        "  err.includes('data-state=\"error\"'),"
        "  run.includes('data-state=\"writing\"'),"
        "  idle.includes('data-state=\"idle\"'),"
        "  team.includes('gm-stack'), team.includes('+2'),"
        "  (team.match(/gm-slot/g) || []).length === 2,"
        "  empty.includes('mk-wax')];",
        via="avatar",
    )
    assert value == [True] * 15





def test_avatar_reduced_motion_disables_all_animations() -> None:
    """The reduced-motion block covers every animation the avatar can run."""
    import re

    css = AVATAR_CSS.read_text(encoding="utf-8")
    block = re.search(
        r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*)\}\s*$",
        css, re.S)
    assert block, "reduced-motion media rule missing"
    body = block.group(1)
    for selector in ("eye-gaze", "eyes-open", "node-bounce-target",
                     "floating-note", "think-dot-1", "think-dot-2",
                     "think-dot-3"):
        assert selector in body, selector
    assert "animation: none" in body
    assert "haloRotate" not in css and "selected-ring-spin" not in css


def test_avatar_ring_uses_existing_surface_token() -> None:
    """Badge ring and stamp fill ride the shared surface token so the
    cream ring matches the current paper on every page."""
    import re

    css = AVATAR_CSS.read_text(encoding="utf-8")
    chat = CHAT_CSS.read_text(encoding="utf-8")
    badge = re.findall(r"\.avatar-badge circle\s*\{[^}]*\}", css)
    extra = re.findall(r"\.avatar-extra circle\s*\{[^}]*\}", css)
    assert badge and "stroke: var(--color-bg-surface)" in badge[0], badge
    assert extra and "fill: var(--color-bg-surface)" in extra[0], extra
    assert "--status-ring" not in css and "--status-ring" not in chat


def test_sidebar_rows_avatar_name_layout() -> None:
    """Bot/session rows: avatar in a fixed first column on the name line,
    title and metadata share the text column with ellipsis pinned, and
    the compact v5 rail shows names in 92px slots inside a 112px rail.
    Expanded/compact SVG fallback artwork stays 48/56px; enhanced 3D
    slots may independently grow to 64/86px."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    src = SIDEBAR_SRC.read_text(encoding="utf-8")

    row = re.search(r"#team-bot-list \.session-item\s*\{([^}]*)\}", css)
    assert row, "scoped row grid missing"
    assert "grid-template-columns: auto minmax(0, 1fr) auto" in row.group(1)
    assert "column-gap: 0.75rem" in row.group(1)
    name = re.search(
        r"#team-bot-list \.session-item \.session-name\s*\{([^}]*)\}", css)
    assert name, "scoped name rule missing"
    for decl in ("grid-column: 2", "grid-row: 1", "min-width: 0",
                 "overflow: hidden", "text-overflow: ellipsis",
                 "white-space: nowrap"):
        assert decl in name.group(1), decl
    meta = re.search(r"#team-bot-list \.session-item time\s*\{([^}]*)\}", css)
    assert meta, "scoped metadata rule missing"
    for decl in ("grid-column: 2", "grid-row: 2", "justify-self: stretch",
                 "min-width: 0", "overflow: hidden", "text-overflow: ellipsis",
                 "white-space: nowrap"):
        assert decl in meta.group(1), decl
    sub = re.search(
        r"#team-bot-list \.team-session-item:not\(\.team-group-item\)"
        r"\s*\{([^}]*)\}", css)
    assert sub, "solo session row rule missing"
    assert "grid-template-columns: minmax(0, 1fr) auto" in sub.group(1)
    subname = re.search(
        r"#team-bot-list \.team-session-item:not\(\.team-group-item\)"
        r" \.session-name\s*\{([^}]*)\}", css)
    assert subname and "grid-column: 1" in subname.group(1)
    submeta = re.search(
        r"#team-bot-list \.team-session-item:not\(\.team-group-item\)"
        r" time\s*\{([^}]*)\}", css)
    assert submeta and "grid-column: 1" in submeta.group(1)
    subunread = re.search(
        r"#team-bot-list \.team-session-item:not\(\.team-group-item\)"
        r" \.team-unread\s*\{([^}]*)\}", css)
    assert subunread and "grid-column: 2" in subunread.group(1)
    group = re.search(
        r"#team-bot-list \.team-group-item\s*\{[^}]*"
        r"grid-template-columns: auto minmax\(0, 1fr\) auto[^}]*\}", css)
    assert group, "group row must keep the three-column grid"

    avatar = re.search(r"^\.team-avatar\s*\{([^}]*)\}", css, re.M)
    assert avatar and "grid-row: 1 / 3;" in avatar.group(1)
    
    assert "width: 2.75rem" in avatar.group(1)
    mini = re.search(r"\.team-avatar\.is-mini\s*\{([^}]*)\}", css)
    assert mini and "width: 2.5rem" in mini.group(1)
    groupav = re.search(r"\.team-avatar\.is-group\s*\{([^}]*)\}", css)
    assert groupav and "width: 2.75rem" in groupav.group(1)

    assert "botMark(bot, { selected })" in src
    assert "avatarSlot(members[0], true, selected)" in src, (
        "single-member session icon must forward selected for the halo")
    assert "botMarkGroup(members, { selected })" in src
    assert "if (group) button.append(sessionIcon(session, selected));" in src, (
        "solo subordinate sessions render no avatar slot")
    assert 'className = "team-new-session"' in src
    assert '"+ Phiên mới"' in src
    newbtn = re.search(r"\.team-new-session\s*\{([^}]*)\}", css)
    assert newbtn, "new-session button styling missing"

_TEAM_NAV_SETUP = """
FakeNode.prototype.replaceChildren = function (...nodes)
 { this.children = [...nodes]; };
FakeNode.prototype.remove = function () {};
Object.defineProperty(FakeNode.prototype, 'childElementCount',
 { get() { return this.children.length; } });
FakeNode.prototype.removeAttribute = function (k)
 { delete this.attributes[k]; };
FakeNode.prototype.after = function () {};
FakeNode.prototype.addEventListener = function (type, fn) {
  (this._handlers || (this._handlers = {}))[type] = fn;
};
FakeNode.prototype.querySelector = function (sel) {
  if (!sel.startsWith('#')) return null;
  const find = (el) => { for (const c of el.children || []) {
   if (c && c.id === sel.slice(1)) return c;
   const r = c && c.children ? find(c) : null;
   if (r) return r; } return null; };
  return find(this); };
const nav = new FakeNode('nav'); nav.id = 'session-list';
const section = new FakeNode('section');
section.id = 'team-nav'; section.hidden = true;
const title = new FakeNode('h2'); title.id = 'team-nav-title';
const botNav = new FakeNode('nav'); botNav.id = 'team-bot-list';
const actions = new FakeNode('div');
actions.id = 'team-nav-actions'; actions.hidden = true;
const sharedBtn = new FakeNode('button');
sharedBtn.id = 'team-shared-create';
section.append(title, botNav, actions);
const registry = { '#team-nav': section,
 '#team-bot-list': botNav, '#team-nav-actions': actions,
 '#team-shared-create': sharedBtn };
document.querySelector = (sel) => registry[sel] || null;
document.addEventListener = () => {};
document.hidden = false;
globalThis.matchMedia = () => ({ matches: false });
globalThis.window = { addEventListener: () => {} };
const writes = [];
globalThis.localStorage = {
  getItem: (k) => null,
  setItem: (k, v) => { writes.push(k); },
};
const subOf = (botId) => {
  for (const item of botNav.children) {
    const head = item.children && item.children[0];
    const headBtn = head && head.children && head.children[0];
    if (!headBtn || headBtn.dataset.botId !== botId) continue;
    return item.children.find(
      (c) => c.className === 'team-cluster-sessions') || null;
  }
  return null;
};
const findRow = (sid) => {
  const out = [];
  const walk = (el) => { for (const c of el.children || []) {
    if (c.dataset && c.dataset.sessionId === sid) out.push(c);
    if (c.children) walk(c); } };
  walk(botNav);
  return out[0] || null;
};
const flapOf = (botId) => {
  for (const item of botNav.children) {
    const head = item.children && item.children[0];
    const headBtn = head && head.children && head.children[0];
    if (!headBtn || headBtn.dataset.botId !== botId) continue;
    return item.children.find(
      (c) => c.className === 'team-bot-flap') || null;
  }
  return null;
};
const newBtn = (botId) => {
  const flap = flapOf(botId);
  return flap ? flap.children.find(
    (c) => c.className === 'team-new-session') || null : null;
};
const tick = () => new Promise((r) => setTimeout(r, 5));
"""


def test_solo_subordinate_session_rows_have_no_avatar(node: str) -> None:
    """A solo subordinate session row is name + metadata only — no
    session-icon or avatar slot — while the top-level group row keeps
    its composite team avatar in the icon column."""
    value = _sidebar(node,
        _TEAM_NAV_SETUP
        + "globalThis.fetch = async () => ({ ok: true, status: 200,"
        "  json: async () => ({ bots: ["
        "   {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
        "   {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) });"
        " await sidebar.initTeamSidebar({ state: { activeId: 'dm',"
        "  detail: { id: 'dm', bots: ['thyca'] },"
        "  sessions: ["
        "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
        "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2},"
        "   {id: 'side', title: 'Phiên phụ', bots: ['thyca'],"
        "    updated_at: '2026-06-02T00:00:00Z', turns: 1},"
        "   {id: 'shared', title: 'Phiên chung', bots: ['thyca', 'coder'],"
        "    updated_at: '2026-07-02T00:00:00Z', turns: 3}] },"
        " loadSession: async () => {}, afterTeamSession: async () => {},"
        " teamRefresh: async () => {} });"
        " const sub = subOf('thyca');"
        " const sideBtn = findRow('side');"
        " const groupBtn = findRow('shared');"
        " return {"
        "  subKids: sub ? sub.children.map((c) => c.className) : null,"
        "  sideKids: sideBtn.children.map((c) => c.tagName + ':' + c.className),"
        "  groupKids: groupBtn.children.map((c) => c.tagName + ':' + c.className),"
        "  groupTeam: (groupBtn.children[0]._html || '').includes('mk-wax')};")
    # "+ Phiên mới" lives on the open flap now; the sheet holds sessions only.
    assert value["subKids"] == ["session-row"]
    assert value["sideKids"] == ["SPAN:session-name", "TIME:"]
    assert value["groupKids"][0] == "SPAN:team-avatar is-group"
    assert value["groupTeam"] is True


def test_new_session_button_renders_when_no_sessions(node: str) -> None:
    """The selected bot's open flap holds just the create button —
    even with zero subordinate sessions — labelled for that bot."""
    value = _sidebar(node,
        _TEAM_NAV_SETUP
        + "globalThis.fetch = async () => ({ ok: true, status: 200,"
        "  json: async () => ({ bots: ["
        "   {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
        "   {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) });"
        " await sidebar.initTeamSidebar({ state: { activeId: 'dm',"
        "  detail: { id: 'dm', bots: ['thyca'] },"
        "  sessions: ["
        "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
        "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2}] },"
        " loadSession: async () => {}, afterTeamSession: async () => {},"
        " teamRefresh: async () => {} });"
        " const flap = flapOf('thyca');"
        " const btn = newBtn('thyca');"
        " return { subKids: flap.children.map((c) => c.className),"
        "  type: btn.type, tag: btn.tagName,"
        "  label: btn.getAttribute('aria-label'), text: btn.textContent,"
        "  disabled: !!btn.disabled,"
        "  coderBtn: !!newBtn('coder') };")
    assert value["subKids"] == ["team-new-session"]
    assert value["tag"] == "BUTTON" and value["type"] == "button"
    assert value["label"] == "Tạo phiên mới với Thyca"
    assert "+ Phiên mới" in value["text"]
    assert value["disabled"] is False
    assert value["coderBtn"] is False


def test_new_session_create_posts_and_navigates(node: str) -> None:
    """Clicking the create button POSTs exactly {bots:[selected]} to
    /api/sessions, hands the fresh solo detail to hub.afterTeamSession,
    and never writes the fixed-DM memory."""
    value = _sidebar(node,
        _TEAM_NAV_SETUP
        + "const posts = [];"
        " const handed = [];"
        " globalThis.fetch = async (url, opts) => {"
        "  if (opts && opts.method === 'POST') {"
        "   posts.push({ url, body: JSON.parse(opts.body) });"
        "   return { ok: true, status: 200,"
        "    json: async () => ({ id: 'side-9', bots: ['thyca'],"
        "     title: 'Phiên mới' }) }; }"
        "  return { ok: true, status: 200,"
        "   json: async () => ({ bots: ["
        "    {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
        "    {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) }; };"
        " await sidebar.initTeamSidebar({ state: { activeId: 'dm',"
        "  detail: { id: 'dm', bots: ['thyca'] },"
        "  sessions: ["
        "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
        "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2}] },"
        " loadSession: async () => {},"
        " afterTeamSession: async (d, guard) => {"
        "  handed.push({ id: d.id, ok: guard() }); },"
        " teamRefresh: async () => {} });"
        " newBtn('thyca')._handlers.click();"
        " await tick(); await tick();"
        " const btn = newBtn('thyca');"
        " return { posts, handed, writes,"
        "  reenabled: btn ? !btn.disabled : null,"
        "  text: btn && btn.textContent,"
        "  errors: flapOf('thyca').children"
        "   .filter((c) => String(c.className).includes('is-error')).length };")
    assert value["posts"] == [
        {"url": "/api/sessions", "body": {"bots": ["thyca"]}}]
    assert value["handed"] == [{"id": "side-9", "ok": True}]
    assert "thyca.team.dm.v1" not in value["writes"], value["writes"]
    assert value["reenabled"] is True
    assert "+ Phiên mới" in value["text"]
    assert value["errors"] == 0


def test_new_session_create_rejects_invalid_detail(node: str) -> None:
    """A POST answer that is not exactly a fresh solo for the requested
    bot — wrong bot, missing id, dm-marked, or multi-bot — is refused:
    honest error shown, no afterTeamSession handoff."""
    cases = [
        "{ id: 'x', bots: ['coder'] }",
        "{ bots: ['thyca'] }",
        "{ id: 'x', bots: ['thyca'], dm: 'thyca' }",
        "{ id: 'x', bots: ['thyca', 'coder'] }",
    ]
    for detail in cases:
        value = _sidebar(node,
            _TEAM_NAV_SETUP
            + "const handed = [];"
            f" globalThis.fetch = async (url, opts) => {{"
            "  if (opts && opts.method === 'POST') {"
            f"   return {{ ok: true, status: 200, json: async () => ({detail}) }}; }}"
            "  return { ok: true, status: 200,"
            "   json: async () => ({ bots: ["
            "    {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
            "    {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) }; };"
            " await sidebar.initTeamSidebar({ state: { activeId: 'dm',"
            "  detail: { id: 'dm', bots: ['thyca'] },"
            "  sessions: ["
            "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
            "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2}] },"
            " loadSession: async () => {},"
            " afterTeamSession: async (d) => { handed.push(d.id); },"
            " teamRefresh: async () => {} });"
            " newBtn('thyca')._handlers.click();"
            " await tick(); await tick();"
            " const flap = flapOf('thyca');"
            " const err = flap.children.find((c) =>"
            "  String(c.className).includes('is-error'));"
            " const btn = newBtn('thyca');"
            " return { handed, error: err ? err.textContent : '',"
            "  reenabled: btn ? !btn.disabled : null };")
        assert value["handed"] == [], detail
        assert "chưa mở phiên" in value["error"], detail
        assert value["reenabled"] is True, detail


def test_new_session_create_failure_shows_error(node: str) -> None:
    """A failed POST surfaces the backend message scoped to the bot and
    the button is usable again."""
    value = _sidebar(node,
        _TEAM_NAV_SETUP
        + "globalThis.fetch = async (url, opts) => {"
        "  if (opts && opts.method === 'POST') {"
        "   return { ok: false, status: 500,"
        "    json: async () => ({ error: 'server nổ' }) }; }"
        "  return { ok: true, status: 200,"
        "   json: async () => ({ bots: ["
        "    {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
        "    {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) }; };"
        " await sidebar.initTeamSidebar({ state: { activeId: 'dm',"
        "  detail: { id: 'dm', bots: ['thyca'] },"
        "  sessions: ["
        "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
        "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2}] },"
        " loadSession: async () => {}, afterTeamSession: async () => {},"
        " teamRefresh: async () => {} });"
        " newBtn('thyca')._handlers.click();"
        " await tick(); await tick();"
        " const flap = flapOf('thyca');"
        " const err = flap.children.find((c) =>"
        "  String(c.className).includes('is-error'));"
        " const btn = newBtn('thyca');"
        " return { error: err ? err.textContent : '',"
        "  role: err ? err.getAttribute('role') : '',"
        "  order: flap.children.map((c) => c.className),"
        "  reenabled: btn ? !btn.disabled : null };")
    assert value["error"] == "server nổ"
    assert value["role"] == "alert"
    # Sealed-letter spec: "+ Phiên mới" is the flap's only line; its
    # error note sits above it.
    assert "is-error" in value["order"][0]
    assert value["order"][-1] == "team-new-session"
    assert value["reenabled"] is True


def test_new_session_pending_blocks_duplicates_and_stale(node: str) -> None:
    """While a create is in flight the button is disabled and duplicate
    clicks do not POST again; switching bots mid-flight makes the late
    resolution stale — no navigation, pending clears."""
    value = _sidebar(node,
        _TEAM_NAV_SETUP
        + "const posts = [];"
        " const handed = [];"
        " let resolvePost = null;"
        " globalThis.fetch = async (url, opts) => {"
        "  if (opts && opts.method === 'POST') {"
        "   posts.push({ url, body: JSON.parse(opts.body) });"
        "   return await new Promise((r) => { resolvePost = r; }); }"
        "  if (String(url).includes('/dm')) {"
        "   return { ok: false, status: 404, json: async () => null }; }"
        "  return { ok: true, status: 200,"
        "   json: async () => ({ bots: ["
        "    {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
        "    {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) }; };"
        " await sidebar.initTeamSidebar({ state: { activeId: 'dm',"
        "  detail: { id: 'dm', bots: ['thyca'] },"
        "  sessions: ["
        "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
        "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2},"
        "   {id: 'coder-dm', title: 'Riêng Coder', bots: ['coder'],"
        "    dm: 'coder', updated_at: '2026-06-03T00:00:00Z', turns: 1}] },"
        " loadSession: async () => {},"
        " afterTeamSession: async (d) => { handed.push(d.id); },"
        " teamRefresh: async () => {} });"
        " const btn1 = newBtn('thyca');"
        " btn1._handlers.click();"
        " await tick();"
        " const pending = newBtn('thyca');"
        " const midState = { disabled: !!pending.disabled,"
        "  text: pending.textContent };"
        " pending._handlers.click();"
        " await tick();"
        " const postsAfterDup = posts.length;"
        " const coderRow = botNav.children.map((c) =>"
        "  c.children && c.children[0]).filter(Boolean).find((r) =>"
        "  r.children[0] && r.children[0].dataset"
        "  && r.children[0].dataset.botId === 'coder');"
        " coderRow.children[0]._handlers.click();"
        " await tick(); await tick();"
        " resolvePost({ ok: true, status: 200,"
        "  json: async () => ({ id: 'stale', bots: ['thyca'] }) });"
        " await tick(); await tick();"
        # The clicked bot's envelope opened at once (paper before the
        # DM resolves) — fold back to thyca to reach its create line.
        " const thycaRow = botNav.children.map((c) =>"
        "  c.children && c.children[0]).filter(Boolean).find((r) =>"
        "  r.children[0] && r.children[0].dataset"
        "  && r.children[0].dataset.botId === 'thyca');"
        " thycaRow.children[0]._handlers.click();"
        " await tick(); await tick();"
        " const btn2 = newBtn('thyca');"
        " return { posts, handed, midState, postsAfterDup,"
        "  thycaActive: botNav.children.map((c) =>"
        "   c.children && c.children[0]).filter(Boolean).some((r) =>"
        "   r.children[0] && r.children[0].dataset"
        "   && r.children[0].dataset.botId === 'thyca'"
        "   && String(r.children[0].className).includes('is-active')),"
        "  reenabled: btn2 ? !btn2.disabled : null };")
    assert value["posts"] == [
        {"url": "/api/sessions", "body": {"bots": ["thyca"]}}]
    assert value["postsAfterDup"] == 1
    assert value["midState"]["disabled"] is True
    assert "Đang tạo" in value["midState"]["text"]
    assert value["handed"] == []
    assert value["thycaActive"] is True
    assert value["reenabled"] is True


def test_new_session_stale_after_clicking_session_row(node: str) -> None:
    """Clicking an existing subordinate session while a create is in
    flight invalidates the create (token bump): the late POST answer
    cannot navigate away from the session the user chose, and the
    button settles back."""
    value = _sidebar(node,
        _TEAM_NAV_SETUP
        + "const posts = [];"
        " const handed = [];"
        " const loaded = [];"
        " let resolvePost = null;"
        " globalThis.fetch = async (url, opts) => {"
        "  if (opts && opts.method === 'POST') {"
        "   posts.push({ url, body: JSON.parse(opts.body) });"
        "   return await new Promise((r) => { resolvePost = r; }); }"
        "  return { ok: true, status: 200,"
        "   json: async () => ({ bots: ["
        "    {id: 'thyca', name: 'Thyca', role: 'chính', status: 'idle'},"
        "    {id: 'coder', name: 'Coder', role: 'code', status: 'idle'}] }) }; };"
        " await sidebar.initTeamSidebar({ state: { activeId: 'dm',"
        "  detail: { id: 'dm', bots: ['thyca'] },"
        "  sessions: ["
        "   {id: 'dm', title: 'Riêng Thyca', bots: ['thyca'],"
        "    dm: 'thyca', updated_at: '2026-06-01T00:00:00Z', turns: 2},"
        "   {id: 'side', title: 'Phiên phụ', bots: ['thyca'],"
        "    updated_at: '2026-06-02T00:00:00Z', turns: 1}] },"
        " loadSession: async (id) => { loaded.push(id); },"
        " afterTeamSession: async (d) => { handed.push(d.id); },"
        " teamRefresh: async () => {} });"
        " newBtn('thyca')._handlers.click();"
        " await tick();"
        " findRow('side')._handlers.click();"
        " await tick();"
        " resolvePost({ ok: true, status: 200,"
        "  json: async () => ({ id: 'created', bots: ['thyca'] }) });"
        " await tick(); await tick();"
        " const btn = newBtn('thyca');"
        " return { posts, handed, loaded,"
        "  reenabled: btn ? !btn.disabled : null };")
    assert value["posts"] == [
        {"url": "/api/sessions", "body": {"bots": ["thyca"]}}]
    assert value["loaded"] == ["side"]
    assert value["handed"] == []
    assert value["reenabled"] is True


def test_is_exchange_requires_parent_for_traced_message(node: str) -> None:
    """A traced root message (kind message, no parent span) is the room's
    visible reply — only nested bus traffic (truthy parent_span_id)
    collapses; traced help_request keeps its existing semantics."""
    value = _run(
        node,
        "const m = (meta) => ({ role: 'assistant', content: 'x', meta });"
        " return ["
        " api.isExchangeMessage(m({kind: 'message', trace_id: 't'})),"
        " api.isExchangeMessage(m({kind: 'message', trace_id: 't',"
        "   parent_span_id: null})),"
        " api.isExchangeMessage(m({kind: 'message', trace_id: 't',"
        "   parent_span_id: ''})),"
        " api.isExchangeMessage(m({kind: 'message', trace_id: 't',"
        "   parent_span_id: 's1'})),"
        " api.isExchangeMessage(m({kind: 'help_request', trace_id: 't'})),"
        " api.isExchangeMessage(m({kind: 'help_request'})),"
        " api.isExchangeMessage(m({kind: 'message'})),"
        " api.isExchangeMessage(m({kind: 'delegate'})),"
        " api.isExchangeMessage(m({kind: 'step', trace_id: 't'}))];",
    )
    assert value == [False, False, False, True, True, False, False, True, True]


def test_chat_bot_for_turn_mentions_paused_and_unknown(node: str) -> None:
    """chatBotForTurn mirrors the backend responder rule: an @mention of
    an active participant wins, paused bots are skipped, solo resolves
    its only bot, and unknown ids surface their own id — never Thyca."""
    value = _run(
        node,
        "api.setChatBotRoster([{id:'thyca',name:'Thyca',status:'active'},"
        " {id:'designer',name:'Designer',status:'active'},"
        " {id:'muse',name:'Muse',status:'paused'}]);"
        " const detail = {bots:['thyca','designer','muse'], messages:["
        "  {role:'user', content:'@muse vẽ giúp'}]};"
        " return ["
        " api.chatBotForTurn(detail).id,"
        " api.chatBotForTurn({bots:['thyca','designer']},"
        "  '@designer làm đi').id,"
        " api.chatBotForTurn({bots:['designer']}).id,"
        " api.chatBotForTurn({bots:['unknown-bot']}).name,"
        " api.chatBotForTurn({bots:['thyca','designer'], messages:["
        "  {role:'user', content:'@designer cũ'},"
        "  {role:'assistant', content:'ok'},"
        "  {role:'user', content:'làm tiếp'}]}).id,"
        " api.chatBotForTurn({}).id];",
        via="chatbot",
    )
    assert value == ["thyca", "designer", "designer",
                     "unknown-bot", "thyca", "thyca"]


def test_chat_bot_for_message_roster_and_meta_overrides(node: str) -> None:
    """chatBotForMessage resolves the roster profile for meta.bot_id,
    honours valid bot_name/bot_tint/bot_avatar overrides, and falls back
    only through the caller context before defaulting to Thyca."""
    value = _run(
        node,
        "api.setChatBotRoster([{id:'designer',name:'Designer',"
        " tint:'botanical',avatar:'hollow',status:'active'}]);"
        " const r = api.chatBotForMessage({meta:{bot_id:'designer'}});"
        " const o = api.chatBotForMessage({meta:{bot_id:'designer',"
        "  bot_name:'D',bot_tint:'ink',bot_avatar:'fermata'}});"
        " const u = api.chatBotForMessage({meta:{bot_id:'ghost'}});"
        " const l = api.chatBotForMessage({content:'x'});"
        " const f = api.chatBotForMessage({content:'x'},"
        "  {id:'solo',name:'Solo'});"
        " return [r.name, r.tint, r.avatar, o.name, o.tint, o.avatar,"
        "  u.name, l.id, f.id];",
        via="chatbot",
    )
    assert value == ["Designer", "botanical", "hollow",
                     "D", "ink", "fermata", "ghost", "thyca", "solo"]


def test_root_traced_message_stays_visible_as_team_card(node: str) -> None:
    """The backend's finalized reply is a root traced message (kind
    message + trace_id + parent_span_id null): it renders the bot's own
    team card — roster name and avatar, content visible — while nested
    step/message traffic still collapses behind the exchange details."""
    value = _transcript(node,
        f"const cb = await import('{CHAT_BOT.as_posix()}');\n"
        "FakeNode.prototype.replaceChildren = function (...nodes)"
        " { this.children = [...nodes]; };"
        "cb.setChatBotRoster([{id:'designer',name:'Designer',"
        " tint:'botanical',avatar:'hollow',status:'active'},"
        " {id:'thyca',name:'Thyca',status:'active'}]);"
        " const root = new FakeNode('div');"
        " transcript.renderConversation(root, ["
        "  {role:'assistant',content:'xong nhé',"
        "   meta:{kind:'message',bot_id:'designer',trace_id:'t1',"
        "    parent_span_id:null}},"
        "  {role:'assistant',content:'bus msg',"
        "   meta:{kind:'message',bot_id:'designer',trace_id:'t1',"
        "    parent_span_id:'s9'}},"
        "  {role:'assistant',content:'step row',"
        "   meta:{kind:'step',bot_id:'coder'}}], {});"
        " const cards = root.children.map((c) => String(c.className));"
        " const card = root.children.find((c) =>"
        "  String(c.className).includes('team-message'));"
        " const av = card && card.querySelector('.chat-brand-avatar');"
        " const head = card && card.querySelector('.chat-brand');"
        " return {cards,"
        "  name: card && card.querySelector('h2').textContent,"
        "  hollow: head && head.className.includes('avatar-variant-hollow'),"
        "  wax: av && av._html.includes('mk-wax'),"
        "  quill: !!(card && card.querySelector('.chat-brand-quill')),"
        "  content: card && card.querySelector('.markdown-body')"
        "   && card.querySelector('.markdown-body')._html.includes('xong nhé'),"
        "  exchange: !!root.querySelector('.team-exchange')};")
    assert any("team-message" in cls for cls in value["cards"])
    assert value["name"] == "Designer"
    assert value["hollow"] is True
    assert value["wax"] is True
    assert value["quill"] is False
    assert value["content"] is True
    assert value["exchange"] is True


def test_legacy_aggregation_splits_on_bot_change(node: str) -> None:
    """Reasoning/tool-only bot rows keep the legacy aggregation, but a
    change of bot identity splits the card; a solo-context fallback
    attributes unidentified rows to that bot, never in a multi room."""
    value = _transcript(node,
        f"const cb = await import('{CHAT_BOT.as_posix()}');\n"
        "FakeNode.prototype.replaceChildren = function (...nodes)"
        " { this.children = [...nodes]; };"
        "cb.setChatBotRoster([{id:'designer',name:'Designer',"
        " status:'active'},{id:'thyca',name:'Thyca',status:'active'}]);"
        " const root = new FakeNode('div');"
        " transcript.renderConversation(root, ["
        "  {role:'assistant',reasoning:'think a',content:'one'},"
        "  {role:'assistant',reasoning:'think b',"
        "   meta:{bot_id:'designer'}}], {});"
        " const multi = root.querySelectorAll('.message-assistant')"
        "  .map((c) => c.querySelector('h2').textContent);"
        " const solo = new FakeNode('div');"
        " transcript.renderConversation(solo, ["
        "  {role:'assistant',content:'hi'}],"
        "  {bot:{id:'designer',name:'Designer'}});"
        " const shared = new FakeNode('div');"
        " transcript.renderConversation(shared, ["
        "  {role:'assistant',content:'hi'}], {bot:null});"
        " return [multi, solo.querySelector('h2').textContent,"
        "  shared.querySelector('h2').textContent];")
    assert value[0] == ["Thyca", "Designer"]
    assert value[1] == "Designer"
    assert value[2] == "Thyca"


def test_live_status_names_and_avatars_the_responding_bot(node: str) -> None:
    """The live card carries the responding bot's name in the header and
    aria-label and renders that bot's avatar; state updates regenerate
    the same bot's avatar (busy → thinking, idle → done)."""
    value = _live(node,
        f"const cb = await import('{CHAT_BOT.as_posix()}');\n"
        "cb.setChatBotRoster([{id:'designer',name:'Designer',"
        " avatar:'hollow',status:'active'}]);"
        " const root = new FakeNode('div');"
        " const l = live.createLiveStatus(root, null,"
        "  {id:'designer',name:'Designer',avatar:'hollow'});"
        " const av1 = l.brand.querySelector('.chat-brand-avatar');"
        " const state1 = av1 && av1._html.includes('data-state=\"thinking\"');"
        " live.updateLiveStatus(l, {type:'turn.completed'});"
        " const av2 = l.brand.querySelector('.chat-brand-avatar');"
        " const head = l.brand;"
        " return {aria: l.article.getAttribute('aria-label'),"
        "  name: l.brand.querySelector('h2').textContent,"
        "  thinking: state1,"
        "  done: av2 && av2._html.includes('data-state=\"done\"'),"
        "  hollow: head.className.includes('avatar-variant-hollow'),"
        "  badge: av2 && av2._html.includes('mk-wax')};")
    assert value["aria"] == "Designer đang trả lời"
    assert value["name"] == "Designer"
    assert value["thinking"] is True
    assert value["done"] is True
    assert value["hollow"] is True
    assert value["badge"] is True


def test_team_article_resolves_profile_without_opts(node: str) -> None:
    """A direct teamArticleFor call (no opts.bot — e.g. an expanded
    exchange row) still renders the real bot: roster name, avatar variant
    on the header, avatar markup, and the shared accent var — not a
    Thyca header wearing a Designer name."""
    value = _cards(node,
        "const cb = await import('" + CHAT_BOT.as_posix() + "');"
        " cb.setChatBotRoster([{id:'designer',name:'Designer',"
        " tint:'botanical',avatar:'hollow',status:'active'}]);"
        " const a = cards.teamArticleFor({role:'assistant',content:'x',"
        "  meta:{bot_id:'designer'}});"
        " const b = cards.teamArticleFor({role:'assistant',content:'y',"
        "  meta:{bot_id:'designer',bot_avatar:'fermata'}});"
        " const headA = a.querySelector('.chat-brand');"
        " const avA = a.querySelector('.chat-brand-avatar');"
        " return {"
        "  name: a.querySelector('h2') && a.querySelector('h2').textContent,"
        "  headCls: headA && headA.className,"
        "  accent: headA && headA._style['--chat-brand-accent'],"
        "  hollow: avA && avA._html.includes('mk-wax'),"
        "  bCls: b.querySelector('.chat-brand').className,"
        "  bFermata: b.querySelector('.chat-brand-avatar')"
        "   ._html.includes('mk-wax'),"
        "  teamTint: a._style['--team-tint']};")
    assert value["name"] == "Designer"
    assert value["headCls"] == "chat-brand avatar-variant-hollow"
    assert value["accent"] == (
        "var(--avatar-tint, var(--team-tint, var(--color-accent)))")
    assert value["hollow"] is True
    assert value["bCls"] == "chat-brand avatar-variant-fermata"
    assert value["bFermata"] is True
    assert value["teamTint"] == "var(--color-success-ink)"


def test_composer_shared_history_keeps_legacy_fallback() -> None:
    """The optimistic transcript must not attribute old meta-less rows to
    the newly mentioned bot: only a solo session may pass a bot fallback
    to renderConversation; the live card still resolves the current text."""
    src = (ROOT / "thyca" / "webui" / "pages" / "chat" / "composer.js")\
        .read_text(encoding="utf-8")
    i = src.find("renderConversation(el.messageList, optimistic")
    assert i != -1
    segment = src[i:i + 400]
    assert "state.detail.bots.length === 1" in segment
    assert "chatBotForTurn(state.detail, text)" in segment
    live_i = src.find("createLiveStatus(el.messageList")
    assert live_i != -1
    assert "chatBotForTurn(state.detail, text)" in src[live_i:live_i + 200]
