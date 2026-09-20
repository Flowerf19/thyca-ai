"""Trace execution journal + deep-link time helpers — webui/backend/trace-data.js.

executionStepsFromDetail is the flat journal behind the Trace screen: real
message order, tool calls kept per call (id preserved, never merged by name)
and nested only under the assistant round that issued them.
traceTimeFormatter/formatTraceTimestamp render timestamps in the IANA zone
from /api/config instead of the viewer's zone.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRACE_DATA = ROOT / "thyca" / "webui" / "backend" / "trace-data.js"
TRACE_JS = ROOT / "thyca" / "webui" / "trace.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ executionStepsFromDetail, traceTimeFormatter, formatTraceTimestamp,"
        f" collectTracePages, formatStepPayload, groupTraceTurns }}"
        f" from '{TRACE_DATA.as_posix()}';\n"
        f"console.log(JSON.stringify({expression}));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def _eval_async(node: str, expression: str) -> object:
    """_eval for promise expressions; errors serialize as {message} strings."""
    source = (
        f"import {{ executionStepsFromDetail, traceTimeFormatter, formatTraceTimestamp,"
        f" collectTracePages, formatStepPayload, groupTraceTurns }}"
        f" from '{TRACE_DATA.as_posix()}';\n"
        f"Promise.resolve({expression})"
        f".then((v) => console.log(JSON.stringify(v)));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_steps_follow_real_message_order(node: str) -> None:
    detail = {
        "messages": [
            {"role": "user", "content": "go", "ts": "2026-09-08T10:00:00"},
            {
                "role": "assistant",
                "content": "thinking hard",
                "ts": "2026-09-08T10:00:01",
                "meta": {"latency_ms": 40},
                "tool_calls": [
                    {"id": "c1", "name": "read", "arguments": {"path": "a.md"}},
                    {"id": "c2", "name": "bash", "arguments": {"command": "ls"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "out1",
             "meta": {"latency_ms": 12}},
            {"role": "tool", "tool_call_id": "c2", "content": "out2",
             "meta": {"latency_ms": 7}},
            {"role": "assistant", "content": "done", "ts": "2026-09-08T10:00:02",
             "meta": {"latency_ms": 5}},
        ]
    }
    steps = _eval(node, f"executionStepsFromDetail({json.dumps(detail)})")
    assert [step["type"] for step in steps] == [
        "input", "thinking", "tool", "tool", "output",
    ]
    assert steps[0] == {"type": "input", "ts": "2026-09-08T10:00:00", "content": "go"}
    assert steps[2] == {
        "type": "tool", "ts": "2026-09-08T10:00:01", "id": "c1", "name": "read",
        "skill": None, "parseError": None, "isError": False,
        "arguments": {"path": "a.md"},
        "output": "out1", "latencyMs": 12,
    }
    assert steps[3]["id"] == "c2"
    assert steps[3]["latencyMs"] == 7
    assert steps[4] == {
        "type": "output", "ts": "2026-09-08T10:00:02",
        "content": "done", "latencyMs": 5,
    }


def test_same_name_calls_stay_separate_per_round(node: str) -> None:
    """Group-by-name would lose the real execution order; each call keeps its id."""
    detail = {
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "b1", "name": "bash", "arguments": {"command": "ls"}},
                {"id": "b2", "name": "bash", "arguments": {"command": "pwd"}},
            ]},
            {"role": "tool", "tool_call_id": "b1", "content": "ls-out"},
            {"role": "tool", "tool_call_id": "b2", "content": "pwd-out"},
        ]
    }
    steps = _eval(node, f"executionStepsFromDetail({json.dumps(detail)})")
    assert [(step["type"], step.get("id")) for step in steps] == [
        ("thinking", None), ("tool", "b1"), ("tool", "b2"),
    ]
    assert [step["arguments"] for step in steps[1:]] == [
        {"command": "ls"}, {"command": "pwd"},
    ]


def test_naming_messages_and_tool_rows_are_skipped(node: str) -> None:
    detail = {
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "Đặt tên", "meta": {"kind": "naming"}},
            {"role": "tool", "tool_call_id": "c1", "content": "orphan"},
            {"role": "assistant", "content": 42, "tool_calls": []},
        ]
    }
    steps = _eval(node, f"executionStepsFromDetail({json.dumps(detail)})")
    # The naming assistant and stray tool rows are not steps; a non-string
    # assistant content degrades to "" instead of crashing.
    assert [step["type"] for step in steps] == ["input", "output"]
    assert steps[1]["content"] == ""
    assert steps[1]["ts"] is None


def test_parse_error_kept_and_missing_result_is_not_invented(node: str) -> None:
    detail = {
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "name": "bash", "arguments": "{bad json",
                 "parse_error": "invalid json"},
                {"id": "c2", "name": "read"},
            ]},
            {"role": "tool", "tool_call_id": "c2", "content": "",
             "meta": {"latency_ms": 0}},
        ]
    }
    steps = _eval(node, f"executionStepsFromDetail({json.dumps(detail)})")
    assert steps[1]["parseError"] == "invalid json"
    assert steps[1]["output"] is None
    assert steps[1]["isError"] is False
    assert steps[1]["latencyMs"] is None
    # Empty result content is still a recorded (but empty) result; zero latency
    # is real. meta.is_error is the only trusted failure flag.
    assert steps[2]["output"] == ""
    assert steps[2]["latencyMs"] == 0
    assert steps[2]["isError"] is False


def test_recorded_tool_error_flag_maps_to_iserror(node: str) -> None:
    """agent/observe.py writes meta.is_error on failed tool calls — keep it."""
    detail = {
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "name": "bash"},
                {"id": "c2", "name": "read"},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "boom",
             "meta": {"is_error": True, "latency_ms": 9}},
            {"role": "tool", "tool_call_id": "c2", "content": "ok"},
        ]
    }
    steps = _eval(node, f"executionStepsFromDetail({json.dumps(detail)})")
    assert steps[1]["isError"] is True
    assert steps[1]["output"] == "boom"
    assert steps[2]["isError"] is False


def test_format_step_payload_keeps_literal_and_empty(node: str) -> None:
    """null means 'omit the block'; a literal '—' or empty string is a real
    payload value and must render verbatim, never collapse to nothing."""
    assert _eval(node, "formatStepPayload(null)") is None
    assert _eval(node, "formatStepPayload('—')") == "—"
    assert _eval(node, "formatStepPayload('')") == ""
    assert _eval(node, "formatStepPayload({limit: 3})") == "limit: 3"
    assert _eval(node, "formatStepPayload({})") == "—"


def test_collect_trace_pages_reads_past_first_page(node: str) -> None:
    """520 rows with limit 200: three pages, all kept, complete window."""
    rows = [
        {"session_id": f"s{i // 100}", "turn_index": i, "started_at": "2026-09-08T10:00:00"}
        for i in range(520)
    ]
    script = (
        f"collectTracePages(({{ limit, offset }}) => "
        f"Promise.resolve({{ traces: {json.dumps(rows)}.slice(offset, offset + limit), total: 520 }}), "
        f"{{ limit: 200 }})"
        ".then((r) => ({ ...r, error: r.error ?? null }))"
    )
    result = _eval_async(node, script)
    assert result["complete"] is True
    assert result["error"] is None
    assert len(result["rows"]) == 520
    assert [r["turn_index"] for r in result["rows"]][:3] == [0, 1, 2]


def test_collect_trace_pages_dedupes_overlapping_windows(node: str) -> None:
    """The 200-session file window can shift between pages; overlapping rows
    dedupe on (session_id, turn_index) instead of double-counting."""
    rows = [{"session_id": "s", "turn_index": i} for i in range(10)]
    # Page 2 repeats rows 5..9 before the new ones.
    pages = [rows[0:5], rows[5:10] + rows[5:10]]
    script = (
        f"(ps => collectTracePages(({{ offset }}) => "
        f"Promise.resolve({{ traces: ps.shift(), total: 10 }}), {{ limit: 5 }}))"
        f"({json.dumps(pages)})"
        ".then((r) => ({ ...r, error: r.error ?? null }))"
    )
    result = _eval_async(node, script)
    assert len(result["rows"]) == 10
    assert result["complete"] is True


def test_collect_trace_pages_reports_failure_midway(node: str) -> None:
    script = (
        "collectTracePages(({ offset }) => offset === 0 "
        "? Promise.resolve({ traces: [{session_id: 's', turn_index: 0}], total: 500 }) "
        ": Promise.reject(new Error('backend died')))"
        ".then((r) => ({ complete: r.complete, rows: r.rows,"
        " error: r.error && { message: r.error.message } }))"
    )
    result = _eval_async(node, script)
    assert result["complete"] is False
    assert len(result["rows"]) == 1
    assert result["error"]["message"] == "backend died"


def test_collect_trace_pages_reports_non_progress(node: str) -> None:
    """A server that keeps re-serving the same page must stop with an error,
    not loop forever or claim success."""
    page = {"traces": [{"session_id": "s", "turn_index": 0}], "total": 500}
    script = (
        f"collectTracePages(() => Promise.resolve({json.dumps(page)}), {{ limit: 200 }})"
        ".then((r) => ({ complete: r.complete, rows: r.rows,"
        " error: r.error && { message: r.error.message } }))"
    )
    result = _eval_async(node, script)
    assert result["complete"] is False
    assert len(result["rows"]) == 1
    assert result["error"] is not None


def test_collect_trace_pages_reports_short_read(node: str) -> None:
    """API stops returning rows before reaching its own reported total."""
    script = (
        "collectTracePages(() => Promise.resolve({ traces: [], total: 500 }))"
        ".then((r) => ({ complete: r.complete, rows: r.rows,"
        " error: r.error && { message: r.error.message } }))"
    )
    result = _eval_async(node, script)
    assert result["complete"] is False
    assert result["rows"] == []
    assert "0/500" in result["error"]["message"]


def test_collect_trace_pages_reports_incomplete_when_dedup_hides_rows(node: str) -> None:
    """offset advances by RAW page rows while dedupe collapses overlaps: rows
    [0,1] then [1,2] with total 4 never read row 3, and the offset >= total
    shortcut must NOT report that window complete."""
    pages = [
        {"traces": [{"session_id": "s", "turn_index": 0},
                    {"session_id": "s", "turn_index": 1}], "total": 4},
        {"traces": [{"session_id": "s", "turn_index": 1},
                    {"session_id": "s", "turn_index": 2}], "total": 4},
    ]
    script = (
        f"(ps => collectTracePages(({{ offset }}) => "
        f"Promise.resolve(ps[offset / 2] ?? {{ traces: [], total: 4 }}), {{ limit: 2 }}))"
        f"({json.dumps(pages)})"
        ".then((r) => ({ complete: r.complete, rows: r.rows.map((x) => x.turn_index),"
        " error: r.error && { message: r.error.message } }))"
    )
    result = _eval_async(node, script)
    assert result["complete"] is False
    assert result["rows"] == [0, 1, 2]
    assert "3/4" in result["error"]["message"]


def test_collect_trace_pages_counts_skipped_rows_toward_total(node: str) -> None:
    """Rows without a session_id still count toward the server total, so a
    window whose only anomaly is such a row stays complete (skipped rows are
    added to the deduped rows before the completeness check)."""
    script = (
        "collectTracePages(() => Promise.resolve({ traces: ["
        "{session_id: 's', turn_index: 0}, {turn_index: 9},"
        "{session_id: 's', turn_index: 1}], total: 2 }))"
        ".then((r) => ({ complete: r.complete, rows: r.rows.length }))"
    )
    result = _eval_async(node, script)
    assert result["complete"] is True
    assert result["rows"] == 2


def test_empty_or_missing_messages(node: str) -> None:
    assert _eval(node, "executionStepsFromDetail(undefined)") == []
    assert _eval(node, "executionStepsFromDetail({})") == []


def test_timestamp_renders_in_configured_zone(node: str) -> None:
    script = (
        "(() => { const fmt = traceTimeFormatter('Asia/Ho_Chi_Minh');"
        " const utc = traceTimeFormatter('UTC');"
        " const paris = traceTimeFormatter('Europe/Paris');"
        " return ["
        "formatTraceTimestamp('2026-01-15T00:30:05Z', fmt),"
        "formatTraceTimestamp('2026-01-15T00:30:05Z', utc),"
        # CEST (DST, UTC+2) vs CET (UTC+1)
        "formatTraceTimestamp('2026-07-15T00:30:05Z', paris),"
        "formatTraceTimestamp('2026-01-15T00:30:05Z', paris)]; })()"
    )
    saigon, utc, paris_summer, paris_winter = _eval(node, script)
    assert saigon["ok"] is True
    assert saigon["text"] == "07:30:05"
    assert utc["text"] == "00:30:05"
    assert paris_summer["text"] == "02:30:05"
    assert paris_winter["text"] == "01:30:05"
    assert "15 thg" in saigon["title"] or "15/01" in saigon["title"]


def test_invalid_or_missing_zone_never_fakes_zoned_time(node: str) -> None:
    script = (
        "(() => { const bad = traceTimeFormatter('Not/AZone');"
        " const none = traceTimeFormatter('');"
        " return [bad, none,"
        "formatTraceTimestamp('2026-01-15T00:30:05Z', bad),"
        "formatTraceTimestamp('2026-01-15T00:30:05Z', none),"
        "formatTraceTimestamp('', none),"
        "formatTraceTimestamp('not-a-date', traceTimeFormatter('UTC'))]; })()"
    )
    bad, none, fallback, no_zone, empty, garbage = _eval(node, script)
    assert bad is None and none is None
    assert fallback["ok"] is False
    assert fallback["text"] == "00:30:05"
    assert fallback["title"] == "2026-01-15T00:30:05Z"
    assert no_zone["ok"] is False
    assert empty["text"] == "—"
    assert garbage["ok"] is False
    assert garbage["text"] == "not-a-date"


# --- Execution-journal pager (TASK-018): pure math runs in Node via the
# --- marker block; DOM paging is browser-test responsibility (TASK-019).


def _trace_pager_block():
    text = TRACE_JS.read_text(encoding="utf-8")
    block = text.split("// >>> journal-pager", 1)[1]
    block = block[block.index("\n") + 1:]  # skip the rest of the marker line
    return block.split("// <<< journal-pager", 1)[0]


def test_trace_pager_math_and_clamp(node):
    source = _trace_pager_block() + (
        "\nconsole.log(JSON.stringify(["
        "journalPageCount(13), journalPageCount(12), journalPageCount(0),"
        "journalClampPage(0, 2), journalClampPage(3, 2), journalClampPage(2, 2)]));\n"
    )
    result = subprocess.run([node, "-e", source], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [2, 1, 1, 1, 2, 2]


def test_step_paging_keeps_absolute_keys_and_open_steps():
    """Paging state lives on the per-turn Map entry (state.turns keyed
    `${sessionId}:${turn_index}`), so the page and every open disclosure
    survive re-renders of any other turn; the slice maps with the ABSOLUTE
    step index, so numbering continues across pages."""
    script = TRACE_JS.read_text(encoding="utf-8")
    assert "(step, offset) => stepEntry(step, start + offset, turnState)" in script
    assert ".slice(start, start + JOURNAL_PAGE_SIZE)" in script
    # Per-turn state map: each turn owns open/detail/pending/error/stepsPage,
    # keyed by the absolute `${sessionId}:${turn_index}` — paging one turn
    # never touches another turn's entry.
    assert "turns: new Map()," in script
    assert "function turnStateFor(key)" in script
    assert "const key = `${group.sessionId}:${summary.turn_index}`;" in script
    assert 'turnState = {' in script
    assert 'open: false,' in script
    assert 'stepsPage: 1,' in script
    assert 'stepOpen: new Set(),' in script
    # Page state clamps before render, only on the turn's own entry.
    assert "turnState.stepsPage = journalClampPage(turnState.stepsPage, pages)" in script
    # Paging mutates only the per-turn page counter, never the open flag.
    assert "turnState.stepsPage = journalClampPage(turnState.stepsPage + delta, pages)" in script
    # Open disclosures survive re-renders: state drives fold.open, and the
    # toggle handler writes back to the per-turn state.
    assert "fold.open = turnState.open;" in script
    assert "turnState.open = fold.open;" in script
    assert "if (turnState.open) fill();" in script
    # Pager reuses shared button styles.
    assert 'setAttribute("aria-label", "Trang trước")' in script
    assert 'setAttribute("aria-label", "Trang sau")' in script


def test_deeplink_turn_reveals_and_pager_stays_single():
    """TASK-027 regression pins: the deep-linked turn scrolls into view with
    its disclosure open (honoring prefers-reduced-motion), every render of the
    matching session re-asserts open + reveal from the per-turn state, and the
    steps pager lives on the turn state so re-renders can never accumulate
    .journal-pager nodes inside one disclosure."""
    script = TRACE_JS.read_text(encoding="utf-8")
    # Reveal honors prefers-reduced-motion (same pattern as app.js).
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in script
    assert 'behavior: reducedMotion ? "auto" : "smooth"' in script
    assert "entry.scrollIntoView(revealScrollOptions());" in script
    # The resolved deep link is recorded and re-applied on every render.
    assert "deepLink: null," in script
    assert "state.deepLink = { sessionId: group.sessionId, turnIndex: wanted };" in script
    assert "state.deepLink.sessionId === group.sessionId" in script
    assert "state.deepLink.turnIndex" in script
    assert "if (fold) fold.open = true;" in script
    # Reliability (Chrome finding): the reveal may run while #trace is still
    # hidden, where scrollIntoView is a no-op. state.deepLink survives until
    # the entry is rect-verified inside the viewport, retries come from
    # renders and a MutationObserver on the #trace [hidden] attribute, and a
    # bounded rAF monitor re-asserts the scroll while pending.
    assert "function settleDeepLinkReveal()" in script
    assert "settleDeepLinkReveal();" in script
    assert "state.deepLink = null; // confirmed: later renders stop re-asserting" in script
    assert 'attributeFilter: ["hidden"]' in script
    assert "if (!view.hidden) settleDeepLinkReveal();" in script
    assert "revealMonitor = requestAnimationFrame(tick);" in script
    assert "rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight" in script
    # One pager per turn, stored on the turn state and reattached on render.
    assert "if (!turnState.pager)" in script
    assert "renderStepsInto(turnState, turnState.pagerBody)" in script
    assert "body.append(turnState.pager.nav);" in script


# --- TASK-030: session grouping order + cost coverage (groupTraceTurns),
# --- and the boot lifecycle (loading status cleared on plain success).


def test_group_turns_orders_sessions_desc_and_turns_asc(node: str) -> None:
    """Session journal order: newest session first (ties by session id desc);
    inside a session, turns in real execution order (turn_index ascending)
    no matter which order the API returned the rows in."""
    rows = [
        {"session_id": "old", "turn_index": 0, "started_at": "2026-09-01T10:00:00"},
        {"session_id": "z-late", "turn_index": 0, "started_at": "2026-09-08T10:00:00"},
        {"session_id": "a-late", "turn_index": 0, "started_at": "2026-09-08T10:00:00"},
        {"session_id": "a-late", "turn_index": 2, "started_at": "2026-09-08T09:58:00"},
        {"session_id": "a-late", "turn_index": 1, "started_at": "2026-09-08T09:59:00"},
    ]
    groups = _eval(node, f"groupTraceTurns({json.dumps(rows)})")
    assert [g["sessionId"] for g in groups] == ["z-late", "a-late", "old"]
    a = groups[1]
    assert [t["turn_index"] for t in a["turns"]] == [0, 1, 2]
    # The group timestamp follows the newest turn of the session.
    assert a["startedAt"] == "2026-09-08T10:00:00"


def test_group_turns_cost_coverage_known_missing_and_zero(node: str) -> None:
    """costUsd sums priced turns only; pricedTurns lets the session list label
    a partial sum. All turns unpriced stays null (never $0) and a genuine
    zero stays 0."""
    rows = [
        {"session_id": "mix", "turn_index": 2, "started_at": "2026-09-08T10:02:00",
         "cost_usd": 0.00002},
        {"session_id": "mix", "turn_index": 0, "started_at": "2026-09-08T10:00:00",
         "cost_usd": None},
        {"session_id": "mix", "turn_index": 1, "started_at": "2026-09-08T10:01:00",
         "cost_usd": 0.00001},
        {"session_id": "unpriced", "turn_index": 0, "started_at": "2026-09-07T09:00:00",
         "cost_usd": None},
        {"session_id": "free", "turn_index": 0, "started_at": "2026-09-06T09:00:00",
         "cost_usd": 0},
        {"session_id": "free", "turn_index": 1, "started_at": "2026-09-06T09:01:00",
         "cost_usd": 0},
    ]
    groups = _eval(node, f"groupTraceTurns({json.dumps(rows)})")
    assert [g["sessionId"] for g in groups] == ["mix", "unpriced", "free"]
    mix = groups[0]
    assert [t["turn_index"] for t in mix["turns"]] == [0, 1, 2]
    assert mix["costUsd"] == pytest.approx(0.00003)
    assert mix["pricedTurns"] == 2
    assert groups[1]["costUsd"] is None
    assert groups[1]["pricedTurns"] == 0
    assert groups[2]["costUsd"] == 0
    assert groups[2]["pricedTurns"] == 2


# --- Out-of-order loads (generation guard), snapshot-refresh detail
# --- invalidation, unfiltered period option and older deep links.


@pytest.fixture(scope="module")
def generation_dom(node: str) -> dict:
    return _run_boot(node, "generation")


@pytest.fixture(scope="module")
def orphan_dom(node: str) -> dict:
    return _run_boot(node, "detail-orphan")


def test_reload_wins_over_late_boot_snapshot(generation_dom: dict) -> None:
    """The user changes the period while boot is still fetching its list: the
    reload (newer generation) renders its snapshot and the late boot response
    must be discarded instead of overwriting it."""
    dom = generation_dom
    assert "Reload-new" in dom["afterReloadText"]
    assert "Boot-only" not in dom["afterReloadText"]
    assert dom["afterReloadStatus"] == ""
    # The stale boot snapshot arrives afterwards and changes nothing.
    assert "Reload-new" in dom["afterLateBootText"]
    assert "Boot-only" not in dom["afterLateBootText"]
    assert dom["afterLateBootStatus"] == ""
    assert dom["afterLateBootNote"] == ""


def test_snapshot_refresh_discards_stale_detail_and_keeps_open_state(
    orphan_dom: dict,
) -> None:
    """An accepted fresh list snapshot invalidates cached turn details and
    orphans in-flight detail results, while disclosure/page state survives: a
    re-opened turn is still open and refetches for real."""
    dom = orphan_dom
    assert dom["orphanOpened"] == {"detailCalls": 1, "foldOpen": True}
    # The period reload fetched a fresh list (default range still selected).
    assert len(dom["orphanListUrls"]) == 2
    assert all("from=" in url for url in dom["orphanListUrls"])
    # The stale pre-refresh detail arrives and is discarded: no content, no
    # status/URL side effects; the body keeps its loading note.
    assert dom["orphanAfterArrival"]["detailCalls"] == 1
    assert "Đang tải" in dom["orphanAfterArrival"]["bodyText"]
    assert dom["orphanAfterArrival"]["statusText"] == ""
    assert dom["orphanAfterArrival"]["urlCount"] == 1
    assert dom["orphanReopen"]["foldOpen"] is True
    assert dom["orphanReopen"]["detailCalls"] == 2
    assert dom["orphanReopen"]["stepFolds"] == 4
    # The "all" period option reads the unfiltered API window (no from/to).
    assert len(dom["allPeriodListUrls"]) == 3
    assert "from=" not in dom["allPeriodListUrls"][-1]
    assert "to=" not in dom["allPeriodListUrls"][-1]


def test_explicit_deeplink_resolves_the_unfiltered_window(node: str) -> None:
    """An explicit ?session= deep link forces the period to the unfiltered API
    window (200 newest session files) so a session older than the default
    30-day range still resolves: no from/to on the list requests, the turn
    opens with its steps."""
    dom = _run_boot(node, "deeplink-old")
    assert dom["periodValue"] == "all"
    assert dom["listUrls"]
    assert all("from=" not in url and "to=" not in url for url in dom["listUrls"])
    assert dom["foldOpen"] is True
    # The steps scenario detail (31 steps) renders its first page of folds.
    assert dom["stepFolds"] == 12


def test_turn_diagnostics_line_renders_in_disclosure(steps_dom: dict) -> None:
    """Diagnostics dropped by the redesign, restored compactly at the top of
    the existing turn disclosure: recorded input/cache/output tokens and the
    assistant round count."""
    dom = steps_dom
    assert dom["turnDiag"] == "Vào: 120 · Cache: 40 · Ra: 30 · 2 vòng"


def test_step_meta_includes_recorded_timestamp(steps_dom: dict) -> None:
    """Every recorded step keeps its wall-clock time (UTC here) next to the
    duration on the existing meta line; the scenario ends on steps page 3."""
    dom = steps_dom
    assert len(dom["stepMeta"]) == 6
    assert all("10:00:0" in meta for meta in dom["stepMeta"])


def test_session_cost_label_marks_recorded_known_totals(node: str) -> None:
    """A non-null cost can be partial WITHIN a turn (the backend sums only the
    per-message cost_usd that was recorded), so a fully priced session sum is
    labelled recorded-known — never presented as a guaranteed fully priced
    total; partial and unpriced labels are unchanged."""
    boot = _run_boot(node, "success")
    assert "đã ghi nhận" in boot["sessionsText"]
    assert "một phần" in boot["sessionsText"]
    assert "chưa định giá" in boot["sessionsText"]


# Boot lifecycle: trace.js runs void boot() at import. With a minimal fake DOM
# (same pattern as test_webui_live_rounds.py) and a routed fetch mock, the
# real boot path executes end to end, so regressions are caught as behavior,
# not substring pins. SCENARIO selects how far the flow is driven:
# success / fetch-error / deeplink-missing stop after boot; "steps" opens a
# session, opens the turn disclosure and drives the step pager.
BOOT_HARNESS = r"""
const SCENARIO = process.env.SCENARIO || "success";
const tick = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

class Element {
  constructor(tag) {
    this.tag = tag || "div";
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.text = "";
    this.hidden = false;
    this.dataset = {};
    this.classes = new Set();
    this.listeners = new Map();
    this.classList = {
      add: (...names) => { for (const n of names) this.classes.add(n); },
      remove: (...names) => { for (const n of names) this.classes.delete(n); },
      contains: (name) => this.classes.has(name),
    };
  }
  set className(v) { this.classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classes].join(" "); }
  get isConnected() { return Boolean(this.root || this.parentNode?.isConnected); }
  append(...nodes) {
    for (const node of nodes) {
      if (node.parentNode) {
        node.parentNode.children = node.parentNode.children.filter((n) => n !== node);
      }
      node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    for (const node of this.children) node.parentNode = null;
    this.children = [];
    this.append(...nodes);
  }
  set textContent(value) { this.replaceChildren(); this.text = String(value); }
  get textContent() { return this.text + this.children.map((n) => n.textContent).join(""); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  querySelectorAll(selector) {
    // Matches tags ("pre"), classes (".foo"), tag+class ("details.fold"),
    // [data-turn-index="N"] and descendant chains thereof — enough for the
    // real selectors trace.js uses (openTurn, deepLinkEntry, step folds).
    const matchesOne = (node, sel) => {
      const attr = sel.match(/^\[data-turn-index="(.+)"\]$/);
      if (attr) return String(node.dataset.turnIndex) === attr[1];
      const [tag, ...classes] = sel.split(".");
      if (tag && node.tag !== tag) return false;
      return classes.every((c) => node.classes.has(c));
    };
    const parts = selector.split(/\s+/).filter(Boolean);
    const matchesChain = (node, chain) => {
      if (!matchesOne(node, chain[chain.length - 1])) return false;
      let ancestor = node.parentNode;
      for (let i = chain.length - 2; i >= 0; i -= 1) {
        while (ancestor && !matchesOne(ancestor, chain[i])) ancestor = ancestor.parentNode;
        if (!ancestor) return false;
        ancestor = ancestor.parentNode;
      }
      return true;
    };
    const walk = (node) => node.children.flatMap((child) => [
      ...(matchesChain(child, parts) ? [child] : []), ...walk(child),
    ]);
    return walk(this);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  emit(type) { for (const fn of this.listeners.get(type) || []) fn({ type }); }
  // TASK-033: pagers are attached OUTSIDE the lists via list.after(nav);
  // re-appending an already-attached node must MOVE it, never duplicate.
  after(...nodes) {
    const parent = this.parentNode;
    if (!parent) return;
    const at = parent.children.indexOf(this);
    for (const node of nodes) {
      if (node.parentNode) {
        node.parentNode.children = node.parentNode.children.filter((n) => n !== node);
      }
      node.parentNode = parent;
      parent.children.splice(at + 1, 0, node);
    }
  }
  // Deep-link reveal support (settleDeepLinkReveal → watchReveal).
  scrollIntoView() {}
  getBoundingClientRect() { return { width: 100, height: 40, top: 10, bottom: 50 }; }
}

const els = {};
// Siblings under one rendered root so list.after(pager) has a real parent,
// matching dashboard.html where each pager lands next to its <ul>.
const shell = new Element("body");
shell.root = true;
for (const id of [
  "trace-status", "trace-note", "trace-picker", "trace-sessions",
  "trace-session", "trace-session-title", "trace-turns", "trace-back",
  "copy-id", "copy-label", "trace", "trace-period",
]) { els[id] = new Element(); els[id].root = true; shell.append(els[id]); }
globalThis.document = {
  createElement: (tag) => new Element(tag),
  querySelector: (sel) => els[String(sel).replace(/^#/, "")] ?? null,
};
globalThis.matchMedia = () => ({ matches: false });
globalThis.window = { innerHeight: 800 };
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 16);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

// URL writes are recorded AND the query string is mirrored like a real
// browser: syncUrl/clearTraceUrl read location.search, so a path-bearing
// replaceState replaces it while a bare "#hash" leaves it alone (TASK: URL
// follows explicit navigation).
const historyCalls = [];
let currentSearch = SCENARIO === "deeplink-missing" ? "?session=ghost"
  : SCENARIO === "deeplink-page" ? "?session=s10&turn=24"
  : SCENARIO === "deeplink-old" ? "?session=s1&turn=0"
  : SCENARIO === "deeplink-bad-turn" ? "?session=s1&turn=99" : "";
globalThis.history = {
  replaceState: (...args) => {
    historyCalls.push(args.map((a) => String(a)));
    const url = String(args[2]);
    const query = url.match(/\?([^#]*)/);
    if (query) currentSearch = `?${query[1]}`;
    else if (url.startsWith("/")) currentSearch = "";
  },
};
globalThis.location = {
  get search() { return currentSearch; },
  set search(value) { currentSearch = String(value); },
  pathname: "/dashboard.html",
};

const traces = [
  { session_id: "s1", title: "Phiên một", turn_index: 1,
    started_at: "2026-09-08T10:01:00", latency_ms: 40, total_tokens: 30,
    cost_usd: 0.00002 },
  { session_id: "s1", title: "Phiên một", turn_index: 0,
    started_at: "2026-09-08T10:00:00", latency_ms: 30, total_tokens: 20,
    cost_usd: null },
  // Fully priced session: every turn recorded a cost, so the label is the
  // recorded-known sum (never presented as a guaranteed fully priced total).
  { session_id: "s2", title: "Phiên hai", turn_index: 0,
    started_at: "2026-09-07T09:01:00", latency_ms: 20, total_tokens: 10,
    cost_usd: 0.00001 },
  { session_id: "s2", title: "Phiên hai", turn_index: 1,
    started_at: "2026-09-07T09:02:00", latency_ms: 20, total_tokens: 10,
    cost_usd: 0 },
];

// TASK-033 dataset: 25 sessions → 12/12/1 session pages; 24 of them carry 25
// turns → 12/12/1 turn pages, the oldest (s0) only 5 → a one-page turn list.
// started_at grows with the index, so the journal order is s24 … s0.
const manyRows = [];
for (let i = 0; i < 25; i++) {
  const turnCount = i === 0 ? 5 : 25;
  for (let j = 0; j < turnCount; j++) {
    manyRows.push({
      session_id: `s${i}`, title: `Phiên ${i}`, turn_index: j,
      started_at: `2026-09-08T10:${String(i).padStart(2, "0")}:${String(j).padStart(2, "0")}`,
      latency_ms: 10, total_tokens: 5, cost_usd: null,
    });
  }
}
// Detail of any turn in the paging scenarios: 4 steps (input, thinking, tool,
// output) → a single steps page with the tool fold at absolute index 2.
const detailMessages = [
  { role: "user", content: "xin chào", ts: "2026-09-08T10:00:00" },
  { role: "assistant", content: "suy nghĩ", ts: "2026-09-08T10:00:01",
    tool_calls: [{ id: "c0", name: "read", arguments: { n: 1 } }] },
  { role: "tool", tool_call_id: "c0", content: "ok", meta: { latency_ms: 3 } },
  { role: "assistant", content: "xong", ts: "2026-09-08T10:00:02" },
];
// Deferred gate for the race scenarios: the s1 detail fetch only resolves
// when the scenario releases it, so the arrival lands AFTER the navigation.
let releaseDetail;
const detailGate = new Promise((resolve) => { releaseDetail = resolve; });

// Generation-guard scenario: the boot list fetch is held back while the user
// already changes the period — the reload (newer generation) must win and the
// late boot snapshot must be discarded.
const GENERATION = SCENARIO === "generation";
let releaseBoot;
const bootGate = new Promise((resolve) => { releaseBoot = resolve; });
const genBootRows = [{ session_id: "boot-only", title: "Boot-only", turn_index: 0,
  started_at: "2026-09-01T10:00:00", latency_ms: 1, total_tokens: 1, cost_usd: null }];
const genReloadRows = [{ session_id: "reload-new", title: "Reload-new", turn_index: 0,
  started_at: "2026-09-09T10:00:00", latency_ms: 1, total_tokens: 1, cost_usd: null }];
let genListCalls = 0;

// Detail-invalidation scenario: the first s1 detail fetch is held on a gate
// while a fresh list snapshot is accepted; the stale result must be discarded
// and the re-opened turn must refetch.
const ORPHAN = SCENARIO === "detail-orphan";
let releaseOrphanDetail;
const orphanGate = new Promise((resolve) => { releaseOrphanDetail = resolve; });
const detailCalls = { count: 0 };
const listUrls = [];
// Recorded per-turn diagnostics (restored in the disclosure): token split and
// round count, present on every detail payload served below.
const diagFields = { prompt_tokens: 120, cached_tokens: 40, completion_tokens: 30, rounds: 2 };

// TASK-033 review scenarios: late-back / switch-cost / rerender-inflight —
// open s1, open the turn disclosure, hold the detail in flight.
const RACE = SCENARIO === "late-back"
  || SCENARIO === "switch-cost" || SCENARIO === "rerender-inflight";

const PAGING = SCENARIO === "paging" || SCENARIO === "deeplink-page";

// 14 tool rounds + final reply = 31 steps (input, 14×[thinking+tool], output)
// → 3 pages at 12/page. The first five tools cover the payload variants:
// structured args with HTML/null/false/0, parse_error with no result,
// empty string output, numeric 0, boolean false.
const specials = [
  { args: { path: "a.md", flag: false, count: 0,
    nested: { x: null, html: "<script>alert(1)</script>" } }, out: "out <b>text</b>" },
  { args: "{bad json", parseError: "invalid json", out: undefined },
  { args: { cmd: "ls" }, out: "" },
  { args: { cmd: "wc" }, out: 0 },
  { args: { cmd: "cat" }, out: false },
];
const messages = [{ role: "user", content: "Xin chào bot", ts: "2026-09-08T10:00:00" }];
for (let i = 0; i < 14; i++) {
  const sp = specials[i] || { args: { n: i }, out: `ok ${i}` };
  const call = { id: `c${i}`, name: "read", arguments: sp.args };
  if (sp.parseError) call.parse_error = sp.parseError;
  messages.push({ role: "assistant", content: `suy nghĩ ${i}`,
    ts: "2026-09-08T10:00:01", tool_calls: [call] });
  if (sp.out !== undefined) {
    messages.push({ role: "tool", tool_call_id: `c${i}`, content: sp.out });
  }
}
messages.push({ role: "assistant", content: "Kết quả: <script>x</script> & —",
  ts: "2026-09-08T10:00:02" });

globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.startsWith("/api/config")) {
    return { ok: true, json: async () => ({ values: { timeline: { timezone: "UTC" } } }) };
  }
  if (/^\/api\/traces\?/.test(u)) {
    listUrls.push(u);
    if (GENERATION) {
      genListCalls += 1;
      if (genListCalls === 1) await bootGate; // hold the boot snapshot back
      const rows = genListCalls === 1 ? genBootRows : genReloadRows;
      return { ok: true, json: async () => ({ traces: rows, total: rows.length }) };
    }
    // Other scenarios keep their own list handling below.
  }
  if (ORPHAN && u.startsWith("/api/traces/s1/")) {
    detailCalls.count += 1;
    const call = detailCalls.count;
    return {
      ok: true,
      json: async () => {
        if (call === 1) await orphanGate; // hold the pre-refresh detail back
        return { messages: detailMessages, total_tokens: 7, ...diagFields };
      },
    };
  }
  if ((SCENARIO === "steps" || SCENARIO === "visible-turn" || SCENARIO === "deeplink-old")
    && u.startsWith("/api/traces/s1/")) {
    return { ok: true, json: async () => ({ messages, total_tokens: 99, ...diagFields }) };
  }
  if (RACE && u.startsWith("/api/traces/s1/")) {
    return {
      ok: true,
      json: async () => {
        await detailGate;
        return { messages, total_tokens: 99, ...diagFields };
      },
    };
  }
  if (PAGING && u.startsWith("/api/traces/")) {
    return { ok: true, json: async () => ({ messages: detailMessages, total_tokens: 7, ...diagFields }) };
  }
  if (PAGING && /\/api\/traces\?/.test(u)) {
    const page = new URLSearchParams(u.split("?")[1]);
    const limit = Number(page.get("limit") || 200);
    const offset = Number(page.get("offset") || 0);
    return {
      ok: true,
      json: async () => ({ traces: manyRows.slice(offset, offset + limit), total: manyRows.length }),
    };
  }
  if (SCENARIO === "fetch-error") throw new Error("backend down");
  return { ok: true, json: async () => ({ traces, total: traces.length }) };
};

await import("./thyca/webui/trace.js");
await tick();

const result = {
  statusText: els["trace-status"].text,
  statusClass: els["trace-status"].className,
  noteText: els["trace-note"].text,
  noteHidden: els["trace-note"].hidden,
  sessionsText: els["trace-sessions"].textContent,
};

// TASK-033 helpers: pagers are nav siblings of the two lists; identify them
// by their meaningful aria-labels and read prev/label/next in DOM order.
const navByAria = (label) => shell.querySelectorAll(".journal-pager")
  .find((n) => n.attributes["aria-label"] === label) ?? null;
const pagerInfo = (nav) => nav ? {
  hidden: nav.hidden,
  label: nav.children[1].textContent,
  prevDisabled: Boolean(nav.children[0].disabled),
  nextDisabled: Boolean(nav.children[2].disabled),
} : null;
const listPagerCount = (label) => shell.querySelectorAll(".journal-pager")
  .filter((n) => n.attributes["aria-label"] === label).length;
result.sessionPager = pagerInfo(navByAria("Phân trang danh sách phiên"));
result.pagerCount = shell.querySelectorAll(".journal-pager").length;
result.lastUrl = historyCalls.length ? historyCalls[historyCalls.length - 1].join(" ") : null;

if (SCENARIO === "steps") {
  const q = (sel) => els["trace-turns"].querySelector(sel);
  const qa = (sel) => els["trace-turns"].querySelectorAll(sel);
  const sections = (fold) => fold.querySelectorAll(".trace-step-io").map((sec) => ({
    label: sec.querySelector(".trace-io-label").textContent,
    text: sec.querySelector(".trace-code").text,
  }));

  els["trace-sessions"].querySelector(".trace-session-open").emit("click");
  await tick();
  const turnFold = q(".trace-turn-fold");
  turnFold.open = true;
  turnFold.emit("toggle");
  await tick();

  const page1 = qa(".trace-step-data");
  result.page1Count = page1.length;
  result.page1FirstNum = q(".trace-step-num").textContent;
  result.step0 = sections(page1[0]);
  result.step2 = sections(page1[2]);
  result.step4 = sections(page1[4]);
  result.step6 = sections(page1[6]);
  result.step8 = sections(page1[8]);
  result.step10 = sections(page1[10]);

  // Open step 2 (absolute index) and page away and back.
  page1[2].open = true;
  page1[2].emit("toggle");
  const pager = qa(".journal-pager-step");
  pager[1].emit("click");
  await tick();
  const page2 = qa(".trace-step-data");
  result.page2FirstNum = q(".trace-step-num").textContent;
  result.page2AllClosed = page2.every((f) => !f.open);
  pager[0].emit("click");
  await tick();
  const page1Again = qa(".trace-step-data");
  result.page1AgainFirstNum = q(".trace-step-num").textContent;
  result.step2StillOpen = page1Again[2].open;

  // Last page: final assistant step payload + the turn IO block.
  pager[1].emit("click");
  await tick();
  pager[1].emit("click");
  await tick();
  const page3 = qa(".trace-step-data");
  result.page3Count = page3.length;
  result.finalStep = sections(page3[page3.length - 1]);
  result.turnIo = [...q(".trace-io").children]
    .filter((n) => n.tag === "pre")
    .map((pre) => pre.text);
  // Restored diagnostics: compact per-turn token split + round count, and the
  // recorded wall-clock time on every step meta line.
  result.turnDiag = q(".trace-turn-diag")?.text ?? null;
  result.stepMeta = qa(".trace-step .journal-meta").map((m) => m.textContent);

  // Full turn re-render (session re-open) must not collapse step 2 either.
  els["trace-sessions"].querySelector(".trace-session-open").emit("click");
  await tick();
  pager[0].emit("click");
  await tick();
  pager[0].emit("click");
  await tick();
  result.rerenderStep2Open = qa(".trace-step-data")[2].open;
}

// TASK-033: outer session list + inner turn list, 12/page, state preserved.
if (SCENARIO === "paging") {
  const q = (sel) => els["trace-turns"].querySelector(sel);
  const qa = (sel) => els["trace-turns"].querySelectorAll(sel);
  const sessionButtons = () => els["trace-sessions"].querySelectorAll(".trace-session-open");
  const sp = navByAria("Phân trang danh sách phiên");

  // Session list: 12 / 12 / 1, first/last buttons disabled at the ends.
  result.sessionPage1Count = sessionButtons().length;
  result.sessionPage1First = sessionButtons()[0].textContent;
  result.sessionPagerPage1 = pagerInfo(sp);
  sp.children[2].emit("click");
  await tick();
  result.sessionPage2Count = sessionButtons().length;
  sp.children[2].emit("click");
  await tick();
  result.sessionPage3Count = sessionButtons().length;
  result.sessionPage3First = sessionButtons()[0].textContent;
  result.sessionPagerPage3 = pagerInfo(sp);

  // Oldest session (s0) has 5 turns → one page, turn pager hidden.
  sessionButtons()[0].emit("click");
  await tick();
  result.pagerCountAfterSelect = shell.querySelectorAll(".journal-pager").length;
  result.turnPagerSmall = pagerInfo(navByAria("Phân trang danh sách lượt"));
  result.turnsSmallCount = qa(".trace-turn-fold").length;

  // Back: the session list keeps its page (3 of 3).
  els["trace-back"].emit("click");
  await tick();
  result.backSessionPageCount = sessionButtons().length;
  result.backSessionPager = pagerInfo(sp);

  // Re-enter: the same one-page turn list.
  sessionButtons()[0].emit("click");
  await tick();
  result.reenterSmallTurns = qa(".trace-turn-fold").length;

  // Switch to s24 (25 turns): per-session turn pages are independent.
  els["trace-back"].emit("click");
  await tick();
  sp.children[0].emit("click");
  await tick();
  sp.children[0].emit("click");
  await tick();
  sessionButtons()[0].emit("click"); // s24, session page 1
  await tick();
  result.turns25Page1 = pagerInfo(navByAria("Phân trang danh sách lượt"));
  result.turns25Page1Count = qa(".trace-turn-fold").length;
  navByAria("Phân trang danh sách lượt").children[2].emit("click");
  await tick();
  navByAria("Phân trang danh sách lượt").children[2].emit("click");
  await tick();
  result.turns25Page3 = pagerInfo(navByAria("Phân trang danh sách lượt"));
  result.turns25Page3Count = qa(".trace-turn-fold").length;
  result.lastTurnTitle = q(".trace-turn-title").textContent;
  result.lastTurnIndex = q(".trace-turn").dataset.turnIndex;

  // Open the turn + a step fold, page away and back on TURNS: both survive.
  const fold = q(".trace-turn-fold");
  fold.open = true;
  fold.emit("toggle");
  await tick();
  result.stepsInLastTurn = qa(".trace-step-data").length;
  qa(".trace-step-data")[2].open = true;
  qa(".trace-step-data")[2].emit("toggle");
  navByAria("Phân trang danh sách lượt").children[0].emit("click");
  await tick();
  result.awayStepFolds = qa(".trace-step-data").length;
  navByAria("Phân trang danh sách lượt").children[2].emit("click");
  await tick();
  result.backStepOpen = qa(".trace-step-data")[2].open;

  // Back to sessions and re-enter: turn page + open states preserved, still
  // exactly one pager node per list (never duplicated by re-renders).
  els["trace-back"].emit("click");
  await tick();
  sessionButtons()[0].emit("click");
  await tick();
  result.reenterTurnPager = pagerInfo(navByAria("Phân trang danh sách lượt"));
  result.reenterFoldOpen = q(".trace-turn-fold").open;
  result.reenterStepOpen = qa(".trace-step-data")[2].open;
  // Exactly ONE pager node per list even after every re-render (an open turn
  // adds its own steps pager, but never a second session/turn pager).
  result.sessionPagerSingleton = listPagerCount("Phân trang danh sách phiên");
  result.turnPagerSingleton = listPagerCount("Phân trang danh sách lượt");
  result.totalPagerNodes = shell.querySelectorAll(".journal-pager").length;

  // Out-of-range clamp: next on the last page keeps the last page.
  navByAria("Phân trang danh sách lượt").children[2].emit("click");
  await tick();
  result.clampAfterOvershoot = pagerInfo(navByAria("Phân trang danh sách lượt"));
}

// TASK-033: legacy deep link to a turn on turn page 3 selects the correct
// session/turn pages BEFORE reveal, and back lands on the session's page.
if (SCENARIO === "deeplink-page") {
  const q = (sel) => els["trace-turns"].querySelector(sel);
  const qa = (sel) => els["trace-turns"].querySelectorAll(sel);
  // ?session=s10&turn=24: turn_index 24 is absolute position 24 → page 3.
  result.deepLinkTurnCount = qa(".trace-turn-fold").length;
  result.deepLinkTurnIndex = q(".trace-turn").dataset.turnIndex;
  result.deepLinkFoldOpen = q(".trace-turn-fold").open;
  result.deepLinkTurnTitle = q(".trace-turn-title").textContent;
  result.deepLinkTurnPager = pagerInfo(navByAria("Phân trang danh sách lượt"));
  // The disclosure really loads: emit the toggle the browser fires natively.
  q(".trace-turn-fold").emit("toggle");
  await tick();
  result.deepLinkSteps = qa(".trace-step-data").length;
  // Back to sessions: lands on the page holding s10 (2 of 3), not page 1.
  els["trace-back"].emit("click");
  await tick();
  const backButtons = els["trace-sessions"].querySelectorAll(".trace-session-open");
  result.deepLinkBackCount = backButtons.length;
  result.deepLinkBackFirst = backButtons[0].textContent;
  result.deepLinkBackSessionPager = pagerInfo(navByAria("Phân trang danh sách phiên"));
}

// TASK-033 review: a late detail arrival must not write the turn's status or
// URL over a context the user has moved to (hidden-but-connected DOM), while
// the content still lands in the current connected body — no stuck loader.
if (RACE) {
  const urlSnapshot = () => historyCalls.map((args) => args.join(" "));
  els["trace-sessions"].querySelector(".trace-session-open").emit("click");
  await tick();
  // Explicit navigation syncs the session into the URL immediately (no turn).
  result.selectUrlCount = historyCalls.length;
  result.selectUrl = urlSnapshot().join(" | ");
  const fold = els["trace-turns"].querySelector(".trace-turn-fold");
  const foldBody = fold.querySelectorAll(".trace-turn-body")[0];
  fold.open = true;
  fold.emit("toggle"); // detail fetch starts, held on the gate
  await tick();
  result.preArrivalUrlCount = historyCalls.length;

  if (SCENARIO === "late-back") {
    els["trace-back"].emit("click"); // back on the session picker
    await tick();
    result.backUrlCount = historyCalls.length;
    result.backUrl = urlSnapshot()[urlSnapshot().length - 1];
  } else if (SCENARIO === "switch-cost") {
    els["trace"].hidden = true; // dashboard.js switched to the Cost view
    await tick();
  } else {
    // Same-turn re-render (session re-opened) while the fetch is in flight.
    els["trace-sessions"].querySelector(".trace-session-open").emit("click");
    await tick();
    result.rerenderUrlCount = historyCalls.length;
  }

  result.arrivalStatusText = els["trace-status"].text;
  result.arrivalUrlCount = historyCalls.length;
  releaseDetail(); // the slow detail finally arrives
  await tick();
  result.afterUrlCount = historyCalls.length;
  result.afterUrlSnapshot = urlSnapshot();
  releaseDetail(); // the slow detail finally arrives
  await tick();

  if (SCENARIO === "rerender-inflight") {
    // The user is looking at the visible turn: content lands AND the
    // URL/status update as usual.
    result.inflightStepFolds = els["trace-turns"].querySelectorAll(".trace-step-data").length;
    result.inflightStatusText = els["trace-status"].text;
    result.inflightUrl = urlSnapshot()[urlSnapshot().length - 1] ?? null;
  } else {
    // Content is rendered into the still-connected (hidden) body…
    result.hiddenBodyStepFolds = foldBody.querySelectorAll(".trace-step-data").length;
    // …but no global side effect leaked over the other context.
    result.afterStatusText = els["trace-status"].text;
    // Re-enter the trace context: content shows from cache, no stuck loader.
    els["trace"].hidden = false;
    els["trace-sessions"].querySelector(".trace-session-open").emit("click");
    await tick();
    result.reenterUrlCount = historyCalls.length;
    result.reenterUrl = urlSnapshot()[urlSnapshot().length - 1];
    result.reenterStepFolds = els["trace-turns"].querySelectorAll(".trace-step-data").length;
    result.reenterBodyText = els["trace-turns"].querySelector(".trace-turn-body").textContent;
  }
}

// The normal visible-turn flow must keep updating URL and status.
if (SCENARIO === "visible-turn") {
  els["trace-sessions"].querySelector(".trace-session-open").emit("click");
  await tick();
  const fold = els["trace-turns"].querySelector(".trace-turn-fold");
  fold.open = true;
  fold.emit("toggle");
  await tick();
  result.visibleStepFolds = els["trace-turns"].querySelectorAll(".trace-step-data").length;
  result.visibleStatusText = els["trace-status"].text;
  result.visibleUrlSnapshot = historyCalls.map((args) => args.join(" "));
}

// Out-of-order loads: a reload (period change) that completes while boot is
// still in flight must win, and the late boot snapshot must be discarded.
if (GENERATION) {
  els["trace-period"].emit("change"); // reloadTrace with the newer generation
  await tick();
  result.afterReloadText = els["trace-sessions"].textContent;
  result.afterReloadStatus = els["trace-status"].text;
  releaseBoot(); // the stale boot snapshot finally arrives
  await tick();
  result.afterLateBootText = els["trace-sessions"].textContent;
  result.afterLateBootStatus = els["trace-status"].text;
  result.afterLateBootNote = els["trace-note"].text;
}

// Snapshot refresh while a detail is in flight: the stale result must be
// discarded, the open disclosure kept, and a re-open must refetch.
if (ORPHAN) {
  els["trace-sessions"].querySelector(".trace-session-open").emit("click");
  await tick();
  const fold = els["trace-turns"].querySelector(".trace-turn-fold");
  fold.open = true;
  fold.emit("toggle"); // detail fetch #1 starts, held on the gate
  await tick();
  result.orphanOpened = { detailCalls: detailCalls.count, foldOpen: fold.open };
  els["trace-period"].emit("change"); // accepted snapshot refresh
  await tick();
  result.orphanListUrls = listUrls.slice();
  releaseOrphanDetail(); // the stale detail finally arrives
  await tick();
  result.orphanAfterArrival = {
    detailCalls: detailCalls.count,
    bodyText: els["trace-turns"].textContent,
    statusText: els["trace-status"].text,
    urlCount: historyCalls.length,
  };
  // Re-open: the disclosure state survived the refresh and the turn refetches.
  els["trace-sessions"].querySelector(".trace-session-open").emit("click");
  await tick();
  result.orphanReopen = {
    foldOpen: els["trace-turns"].querySelector(".trace-turn-fold").open,
    detailCalls: detailCalls.count,
    stepFolds: els["trace-turns"].querySelectorAll(".trace-step-data").length,
  };
  // The "all" period option reads the unfiltered API window (no from/to).
  els["trace-period"].value = "all";
  els["trace-period"].emit("change");
  await tick();
  result.allPeriodListUrls = listUrls.slice();
}

// An explicit ?session= deep link must resolve against the UNFILTERED window,
// even when the session is older than the default rolling range.
if (SCENARIO === "deeplink-old") {
  result.listUrls = listUrls;
  result.periodValue = els["trace-period"].value;
  result.foldOpen = els["trace-turns"].querySelector(".trace-turn-fold").open;
  els["trace-turns"].querySelector(".trace-turn-fold").emit("toggle");
  await tick();
  result.stepFolds = els["trace-turns"].querySelectorAll(".trace-step-data").length;
}

console.log(JSON.stringify(result));
"""


def _run_boot(node: str, scenario: str) -> dict:
    result = subprocess.run(
        [node, "--input-type=module", "-e", BOOT_HARNESS],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        # TZ=UTC keeps naive recorded timestamps deterministic: the fake
        # formatter zone is UTC, so "10:00:01" formats back as "10:00:01".
        env={**os.environ, "SCENARIO": scenario, "TZ": "UTC"},
    )
    return json.loads(result.stdout)


def test_boot_clears_loading_status_after_successful_load(node: str) -> None:
    """Plain success (groups loaded, no deep link): the “Đang đọc trace
    backend…” status must be gone — previously it lingered forever."""
    boot = _run_boot(node, "success")
    assert boot["statusText"] == ""
    assert boot["statusClass"] == "screen-status"
    # Notes are a separate channel and stay untouched by the clear.
    assert boot["noteHidden"] is True
    assert boot["noteText"] == ""
    # Session list rendered: partial cost labelled as partial, never a bare sum.
    assert "$0.00002" in boot["sessionsText"]
    assert "một phần" in boot["sessionsText"]
    assert "chưa định giá" in boot["sessionsText"]


def test_boot_reports_backend_error_instead_of_loading(node: str) -> None:
    boot = _run_boot(node, "fetch-error")
    assert boot["statusText"] == "Không kết nối được với backend Thyca."
    assert "is-error" in boot["statusClass"]


def test_boot_keeps_deeplink_error_status(node: str) -> None:
    """The success clear must come after the deep-link check: an unresolvable
    ?session= keeps its explicit error message."""
    boot = _run_boot(node, "deeplink-missing")
    assert "Không tìm thấy phiên ghost" in boot["statusText"]
    assert "is-error" in boot["statusClass"]


def test_failed_turn_deeplink_keeps_its_url(node: str) -> None:
    """A turn that does not resolve is reported AND its original deep-link URL
    is restored after the session navigation rewrote it: the failed link stays
    inspectable on refresh instead of being silently replaced."""
    boot = _run_boot(node, "deeplink-bad-turn")
    assert "Lượt 99 không có trong dữ liệu đã tải" in boot["statusText"]
    assert "is-error" in boot["statusClass"]
    assert boot["lastUrl"].endswith("?session=s1&turn=99#trace")


# --- TASK-032: per-step payload disclosure. The fake-DOM flow opens a real
# --- session → turn → step pager, so the payload rendering and its open-state
# --- persistence are proven as DOM behavior over the whole turn.


@pytest.fixture(scope="module")
def steps_dom(node: str) -> dict:
    return _run_boot(node, "steps")


def test_step_payload_renders_structured_and_verbatim(steps_dom: dict) -> None:
    """Tool Input (arguments) is pretty-printed JSON — nested null/false/0 and
    HTML survive as data. Strings (tool output, user content) render verbatim,
    including HTML-as-text."""
    dom = steps_dom
    assert dom["page1Count"] == 12
    assert dom["page1FirstNum"] == "01"
    # Step 0 = user input: content verbatim.
    assert dom["step0"] == [{"label": "Input", "text": "Xin chào bot"}]
    # Step 2 = tool c1: structured arguments, string output.
    assert dom["step2"][0]["label"] == "Input"
    assert '"path": "a.md"' in dom["step2"][0]["text"]
    assert '"flag": false' in dom["step2"][0]["text"]
    assert '"count": 0' in dom["step2"][0]["text"]
    assert '"x": null' in dom["step2"][0]["text"]
    assert "<script>alert(1)</script>" in dom["step2"][0]["text"]
    assert dom["step2"][1] == {"label": "Output", "text": "out <b>text</b>"}


def test_step_output_distinguishes_missing_empty_zero_false(steps_dom: dict) -> None:
    """Missing (no result message) is an explicit placeholder, never faked as
    empty/zero; empty string, 0 and false are real values and stay distinct."""
    dom = steps_dom
    parse_error_sections = dom["step4"]
    assert parse_error_sections[0]["label"] == "Input"
    assert "bad json" in parse_error_sections[0]["text"]
    # No result message recorded → explicit placeholder, not "".
    assert parse_error_sections[1] == {"label": "Output", "text": "Chưa ghi nhận output"}
    # parse_error is kept as a labelled diagnostic next to the status chip.
    assert {"label": "Lỗi cú pháp", "text": "invalid json"} in parse_error_sections
    # Empty string, 0 and false are real recorded values, kept verbatim —
    # each tool fold still leads with its Input (arguments) section.
    assert len(dom["step6"]) == 2
    assert dom["step6"][0]["label"] == "Input"
    assert dom["step6"][1] == {"label": "Output", "text": ""}
    assert dom["step8"][1] == {"label": "Output", "text": "0"}
    assert dom["step10"][1] == {"label": "Output", "text": "false"}


def test_final_step_and_turn_io_blocks_survive(steps_dom: dict) -> None:
    """The final assistant step keeps its Output payload and the turn still
    ends with the labelled Input/Output block (first user text, final reply)."""
    dom = steps_dom
    # 30 steps: input + 14 * (thinking + tool) + final reply → 12/12/6.
    assert dom["page3Count"] == 6
    assert dom["finalStep"] == [{"label": "Output", "text": "Kết quả: <script>x</script> & —"}]
    assert dom["turnIo"][0] == "Xin chào bot"
    assert dom["turnIo"][1] == "Kết quả: <script>x</script> & —"


def test_step_open_state_persists_paging_and_rerender(steps_dom: dict) -> None:
    """Open state is keyed by the ABSOLUTE step index on the turn state, so it
    survives steps paging (page 1 → 2 → 1) and a full turn re-render."""
    dom = steps_dom
    assert dom["page2FirstNum"] == "13"
    assert dom["page2AllClosed"] is True
    assert dom["page1AgainFirstNum"] == "01"
    assert dom["step2StillOpen"] is True
    assert dom["rerenderStep2Open"] is True


# --- TASK-033: the OUTER session list and the INNER turn list both page at
# --- 12 entries on the loaded snapshot. Pagers sit OUTSIDE each list as nav
# --- siblings with meaningful aria-labels; pages are kept per session / for
# --- the session list across back and session switches.


@pytest.fixture(scope="module")
def paging_dom(node: str) -> dict:
    return _run_boot(node, "paging")


@pytest.fixture(scope="module")
def deeplink_dom(node: str) -> dict:
    return _run_boot(node, "deeplink-page")


def test_one_page_pagers_hidden_with_meaningful_labels(node: str) -> None:
    """A single session never shows a pager, but the nav still carries its
    meaningful landmark label (class contract .journal-pager)."""
    boot = _run_boot(node, "success")
    assert boot["pagerCount"] == 1  # session pager only; no turn list yet
    pager = boot["sessionPager"]
    assert pager == {
        "hidden": True,
        "label": "1 / 1",
        "prevDisabled": True,
        "nextDisabled": True,
    }


def test_session_list_pages_12_12_1_with_end_buttons_disabled(paging_dom: dict) -> None:
    dom = paging_dom
    assert dom["sessionPage1Count"] == 12
    assert dom["sessionPage1First"] == "Phiên 24"  # newest first
    assert dom["sessionPagerPage1"] == {
        "hidden": False, "label": "1 / 3",
        "prevDisabled": True, "nextDisabled": False,
    }
    assert dom["sessionPage2Count"] == 12
    assert dom["sessionPage3Count"] == 1
    assert dom["sessionPage3First"] == "Phiên 0"  # absolute index 24
    assert dom["sessionPagerPage3"]["nextDisabled"] is True
    assert dom["sessionPagerPage3"]["prevDisabled"] is False
    assert dom["sessionPagerPage3"]["label"] == "3 / 3"


def test_turn_list_pages_and_one_page_turn_list_hides_pager(paging_dom: dict) -> None:
    dom = paging_dom
    # s0: 5 turns → one page, pager hidden; still exactly one nav per list.
    assert dom["pagerCountAfterSelect"] == 2
    assert dom["turnPagerSmall"] == {
        "hidden": True, "label": "1 / 1",
        "prevDisabled": True, "nextDisabled": True,
    }
    assert dom["turnsSmallCount"] == 5
    # s24: 25 turns → 12 / 12 / 1 with absolute indices on the last page.
    assert dom["turns25Page1"] == {
        "hidden": False, "label": "1 / 3",
        "prevDisabled": True, "nextDisabled": False,
    }
    assert dom["turns25Page1Count"] == 12
    assert dom["turns25Page3Count"] == 1
    assert dom["lastTurnTitle"] == "Lượt 25"
    assert dom["lastTurnIndex"] == "24"
    assert dom["turns25Page3"]["label"] == "3 / 3"
    assert dom["turns25Page3"]["nextDisabled"] is True


def test_session_page_survives_back_and_reenter(paging_dom: dict) -> None:
    dom = paging_dom
    assert dom["backSessionPageCount"] == 1
    assert dom["backSessionPager"]["label"] == "3 / 3"
    assert dom["reenterSmallTurns"] == 5


def test_turn_page_and_open_state_persist_across_paging_and_sessions(
    paging_dom: dict,
) -> None:
    """Turn pages are kept PER SESSION; open turn/step disclosures survive
    turn paging (away AND back), a session switch and a full re-render."""
    dom = paging_dom
    assert dom["stepsInLastTurn"] == 4
    # Turn page 2 shows no step folds (no disclosure open there).
    assert dom["awayStepFolds"] == 0
    # Back on turn page 3: the step fold keyed by ABSOLUTE index is still open.
    assert dom["backStepOpen"] is True
    # Back to the session list and in again: same turn page, same open state.
    assert dom["reenterTurnPager"]["label"] == "3 / 3"
    assert dom["reenterFoldOpen"] is True
    assert dom["reenterStepOpen"] is True


def test_no_duplicate_pagers_and_overshoot_clamps_to_last_page(
    paging_dom: dict,
) -> None:
    dom = paging_dom
    # Sessions + turns paging, selection, back and re-renders: always exactly
    # one pager node per list (an open turn adds its steps pager on top).
    assert dom["sessionPagerSingleton"] == 1
    assert dom["turnPagerSingleton"] == 1
    assert dom["totalPagerNodes"] == 3
    # Clicking next past the end clamps to the last page.
    assert dom["clampAfterOvershoot"]["label"] == "3 / 3"
    assert dom["clampAfterOvershoot"]["nextDisabled"] is True


def test_deeplink_turn_on_page3_selects_pages_before_reveal(deeplink_dom: dict) -> None:
    """?session=s10&turn=24 resolves on the loaded snapshot, renders turn
    page 3 directly (one entry, absolute position 24), opens its disclosure
    and loads the steps."""
    dom = deeplink_dom
    assert dom["deepLinkTurnCount"] == 1
    assert dom["deepLinkTurnIndex"] == "24"
    assert dom["deepLinkTurnTitle"] == "Lượt 25"
    assert dom["deepLinkFoldOpen"] is True
    assert dom["deepLinkTurnPager"] == {
        "hidden": False, "label": "3 / 3",
        "prevDisabled": False, "nextDisabled": True,
    }
    assert dom["deepLinkSteps"] == 4
    # Back to the session list lands on the page holding s10 (2 of 3).
    assert dom["deepLinkBackCount"] == 12
    assert dom["deepLinkBackFirst"] == "Phiên 12"
    assert dom["deepLinkBackSessionPager"]["label"] == "2 / 3"


# --- TASK-033 review: a late detail arrival must not write the turn's
# --- status/URL over a context the user has moved to. isConnected is not
# --- enough — hidden-but-connected DOM (Cost view, session picker) stays
# --- connected — so the side effects are gated on real visibility, while the
# --- content still renders into the current connected body (no stuck loader).


@pytest.fixture(scope="module")
def late_back_dom(node: str) -> dict:
    return _run_boot(node, "late-back")


@pytest.fixture(scope="module")
def switch_cost_dom(node: str) -> dict:
    return _run_boot(node, "switch-cost")


@pytest.fixture(scope="module")
def rerender_inflight_dom(node: str) -> dict:
    return _run_boot(node, "rerender-inflight")


@pytest.fixture(scope="module")
def visible_turn_dom(node: str) -> dict:
    return _run_boot(node, "visible-turn")


def test_late_detail_arrival_at_picker_keeps_status_and_url(
    late_back_dom: dict,
) -> None:
    """User opens a turn, goes back to the picker, THEN the detail arrives:
    no turn status in the picker, no URL rewrite — but the content is cached
    into the connected body and shows immediately on re-enter. Explicit
    navigation itself DOES sync the URL: the pick writes ?session= (no turn),
    Back clears the deep-link params."""
    dom = late_back_dom
    assert dom["selectUrlCount"] == 1
    assert "session=s1" in dom["selectUrl"]
    assert "turn=" not in dom["selectUrl"]
    assert dom["preArrivalUrlCount"] == 1
    # Back to the picker drops the deep-link params entirely.
    assert dom["backUrlCount"] == 2
    assert "session=" not in dom["backUrl"]
    assert "turn=" not in dom["backUrl"]
    # The late arrival leaks nothing: no status, no URL write.
    assert dom["arrivalStatusText"] == ""
    assert dom["arrivalUrlCount"] == 2
    assert dom["afterUrlCount"] == 2
    assert dom["hiddenBodyStepFolds"] == 12
    assert dom["afterStatusText"] == ""
    # Re-enter: content from cache; navigation re-syncs session then turn.
    assert dom["reenterUrlCount"] == 4
    assert "session=s1&turn=0" in dom["reenterUrl"]
    assert dom["reenterStepFolds"] == 12
    assert "Đang tải" not in dom["reenterBodyText"]
    assert "01" in dom["reenterBodyText"]


def test_late_detail_arrival_on_cost_switch_keeps_status_and_url(
    switch_cost_dom: dict,
) -> None:
    """Same gate for the view switch: #trace hidden (Cost visible) is still
    connected, so the arrival must not rewrite ?session=&turn=#trace over the
    Cost URL nor write a turn status while Cost is on screen. (In the real
    dashboard, dashboard.js itself strips the params when leaving Trace.)"""
    dom = switch_cost_dom
    assert dom["selectUrlCount"] == 1
    assert dom["preArrivalUrlCount"] == 1
    assert dom["arrivalUrlCount"] == 1
    assert dom["afterUrlCount"] == 1
    assert dom["arrivalStatusText"] == ""
    assert dom["hiddenBodyStepFolds"] == 12
    assert dom["afterStatusText"] == ""
    # Re-entering re-syncs session and (cached) turn into the URL: the cached
    # reopen follows the same URL contract as a fresh load.
    assert dom["reenterUrlCount"] == 3
    assert "session=s1&turn=0" in dom["reenterUrl"]
    assert dom["reenterStepFolds"] == 12
    assert "Đang tải" not in dom["reenterBodyText"]


def test_same_turn_rerender_inflight_still_receives_content(
    rerender_inflight_dom: dict,
) -> None:
    """A same-turn re-render while the detail is in flight: the arrival
    refreshes the CURRENT body (no stuck loader) and, the turn being on
    screen, the URL/status update as usual."""
    dom = rerender_inflight_dom
    assert dom["inflightStepFolds"] == 12
    assert "token trong lượt này" in dom["inflightStatusText"]
    # Writes: the pick, the re-render pick (session URL), the re-render's own
    # sync of the still-open turn, then the arrival (session + turn).
    assert dom["rerenderUrlCount"] == 3
    assert dom["afterUrlCount"] == 4
    assert "session=s1&turn=0" in dom["inflightUrl"]
    assert dom["inflightUrl"].endswith("#trace")


def test_visible_turn_still_updates_url_and_status(visible_turn_dom: dict) -> None:
    """The visibility gate must not mute the normal flow: an opened turn that
    stays on screen still gets its status and its ?session=&turn=#trace URL,
    after the pick's own ?session= write."""
    dom = visible_turn_dom
    assert dom["visibleStepFolds"] == 12
    assert "token trong lượt này" in dom["visibleStatusText"]
    urls = dom["visibleUrlSnapshot"]
    assert len(urls) == 2
    assert "session=s1" in urls[0]
    assert "turn=" not in urls[0]
    assert "session=s1&turn=0" in urls[1]
    assert urls[1].endswith("#trace")
