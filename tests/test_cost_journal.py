"""Node tests for the Chi phí journal helpers — webui/pages/dashboard/cost-data.js.

Covers session aggregation (dedupe on session_id+turn_index, missing-session
group, turn vs request counts), overview metrics (null vs zero cost, partial
pricing, cache split), the fixed pre-search share denominator, and the
estimate-only pricing split. Paging failure semantics are checked against
fetchAllTraces (dashboard-today.js). Mirrors the eval style of
test_dashboard_journal.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "thyca" / "webui" / "pages" / "dashboard" / "cost-data.js"
TODAY_SCRIPT = ROOT / "thyca" / "webui" / "shared" / "js" / "dashboard-today.js"
COST_JS = ROOT / "thyca" / "webui" / "pages" / "dashboard" / "cost.js"
COST_VIEW = ROOT / "thyca" / "webui" / "pages" / "dashboard" / "cost-view.js"
COST_ROWS = ROOT / "thyca" / "webui" / "pages" / "dashboard" / "cost-rows.js"
PAGER_JS = ROOT / "thyca" / "webui" / "shared" / "js" / "pager.js"


def _cost_script() -> str:
    """Concat of the Cost journal modules (entry + view + rows + shared
    pager): substring pins read the split files, asserts stay unchanged."""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (COST_JS, COST_VIEW, COST_ROWS, PAGER_JS)
    )


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ NO_SESSION_KEY, overviewMetrics, aggregateSessions, knownCostTotal,"
        f" knownTokenTotal, modelTokens, dailyCosts, shareLabel, shareRatio, priceRates,"
        f" estimatedCostSplit, turnCost, modelTurnCoverage, averageDisplay }}"
        f" from '{SCRIPT.as_posix()}';\n"
        f"import {{ fetchAllTraces }} from '{TODAY_SCRIPT.as_posix()}';\n"
        f"console.log(JSON.stringify(await ({expression})));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_turn_count_vs_request_count(node: str) -> None:
    rows = [
        {"session_id": "s1", "turn_index": 0, "requests": 3, "cost_usd": 0.01,
         "prompt_tokens": 100, "cached_tokens": 40, "completion_tokens": 10},
        {"session_id": "s1", "turn_index": 1, "requests": 2, "cost_usd": 0.02,
         "prompt_tokens": 50, "cached_tokens": 0, "completion_tokens": 20},
    ]
    overview = _eval(node, "overviewMetrics($rows)".replace("$rows", json.dumps(rows)))
    assert overview["turns"] == 2  # turns, not the 5 model requests
    assert overview["requests"] == 5
    assert overview["costUsd"] == pytest.approx(0.03)
    assert overview["pricedTurns"] == 2
    assert overview["averageTurns"] == 2  # no status on these rows → not failed
    assert overview["averageCostUsd"] == pytest.approx(0.03)
    assert overview["inputTokens"] == 60 + 50  # prompt minus cache subset
    assert overview["cacheTokens"] == 40
    assert overview["outputTokens"] == 30


def test_average_skips_failed_and_counts_only_priced_turns(node: str) -> None:
    # The per-turn average runs over priced, non-failed turns: a failed turn's
    # recorded cost stays in the total but is skipped from the average basis,
    # and unpriced turns cannot contribute either way.
    rows = [
        {"session_id": "s1", "turn_index": 0, "status": "failed", "cost_usd": 0.5},
        {"session_id": "s1", "turn_index": 1, "status": "completed", "cost_usd": 0.2},
        {"session_id": "s1", "turn_index": 2, "status": "completed", "cost_usd": None},
        {"session_id": "s1", "turn_index": 3, "status": "loop_limit", "cost_usd": 0.1},
    ]
    overview = _eval(node, "overviewMetrics($rows)".replace("$rows", json.dumps(rows)))
    assert overview["turns"] == 4 and overview["pricedTurns"] == 3
    assert overview["costUsd"] == pytest.approx(0.8)  # total keeps the failed cost
    assert overview["averageTurns"] == 2  # failed turn skipped, loop_limit kept
    assert overview["averageCostUsd"] == pytest.approx(0.3)


def test_average_basis_zero_when_every_priced_turn_failed(node: str) -> None:
    rows = [
        {"session_id": "s1", "turn_index": 0, "status": "failed", "cost_usd": 0.5},
        {"session_id": "s1", "turn_index": 1, "status": "completed", "cost_usd": None},
    ]
    overview = _eval(node, "overviewMetrics($rows)".replace("$rows", json.dumps(rows)))
    assert overview["averageTurns"] == 0 and overview["averageCostUsd"] is None


def test_session_aggregation_dedupes_and_groups(node: str) -> None:
    rows = [
        {"session_id": "s1", "turn_index": 1, "requests": 2, "cost_usd": 0.02,
         "prompt_tokens": 50, "cached_tokens": 0, "completion_tokens": 20,
         "title": "Phiên mới", "started_at": "2026-09-19T10:00:00Z"},
        # exact duplicate of the row above (same session_id + turn_index)
        {"session_id": "s1", "turn_index": 1, "requests": 2, "cost_usd": 0.02,
         "prompt_tokens": 50, "cached_tokens": 0, "completion_tokens": 20,
         "title": "Phiên mới", "started_at": "2026-09-19T10:00:00Z"},
        {"session_id": "s1", "turn_index": 0, "requests": 1, "cost_usd": None,
         "prompt_tokens": 10, "cached_tokens": 0, "completion_tokens": 1,
         "title": "Phiên cũ", "started_at": "2026-09-19T09:00:00Z"},
        {"session_id": "", "turn_index": 0, "requests": 1, "cost_usd": 0.5,
         "prompt_tokens": 5, "cached_tokens": 0, "completion_tokens": 2,
         "started_at": "2026-09-18T08:00:00Z"},
    ]
    sessions = _eval(node, "aggregateSessions($rows)".replace("$rows", json.dumps(rows)))
    assert len(sessions) == 2  # dedupe collapsed the repeated turn
    by_key = {s["key"]: s for s in sessions}
    # Cost-desc across groups: the priced 0.5 missing-id group outranks s1
    # (0.02); assert order by cost, not by position luck.
    costs = [s["costUsd"] for s in sessions]
    assert costs == sorted([c for c in costs if c is not None], reverse=True) + \
        [c for c in costs if c is None]
    top = by_key["(không có session)"]
    assert top["costUsd"] == pytest.approx(0.5)
    assert top["sessionId"] is None and top["title"] == "" and top["turns"] == 1
    s1 = by_key["s1"]
    assert s1["turns"] == 2
    assert s1["requests"] == 3
    assert s1["costUsd"] == pytest.approx(0.02)  # null turn contributes nothing
    assert s1["pricedTurns"] == 1  # partial: one of two turns unpriced
    assert s1["title"] == "Phiên mới"  # newest turn's title, no N+1 fetch


def test_session_order_priced_desc_unpriced_last(node: str) -> None:
    rows = [
        {"session_id": "paid-big", "turn_index": 0, "cost_usd": 0.5,
         "started_at": "2026-09-19T08:00:00Z"},
        {"session_id": "paid-small", "turn_index": 0, "cost_usd": 0.1,
         "started_at": "2026-09-19T09:00:00Z"},  # newer but cheaper
        {"session_id": "unpriced", "turn_index": 0, "cost_usd": None,
         "started_at": "2026-09-19T10:00:00Z"},  # newest but unpriced
    ]
    sessions = _eval(node, "aggregateSessions($rows)".replace("$rows", json.dumps(rows)))
    assert [s["key"] for s in sessions] == ["paid-big", "paid-small", "unpriced"]


def test_null_vs_zero_cost_semantics(node: str) -> None:
    result = _eval(
        node,
        "[" + ", ".join([
            "overviewMetrics([{'session_id':'a','turn_index':0,'cost_usd':null}])",
            "overviewMetrics([{'session_id':'b','turn_index':0,'cost_usd':0}])",
            "turnCost({'cost_usd':undefined})",
            "turnCost({'cost_usd':'nope'})",
        ]) + "]",
    )
    nothing_priced, zero_real, missing, garbage = result
    assert nothing_priced["costUsd"] is None and nothing_priced["pricedTurns"] == 0
    assert zero_real["costUsd"] == 0 and zero_real["pricedTurns"] == 1
    assert missing is None and garbage is None
    assert _eval(node, "aggregateSessions([])") == []


def test_share_denominator_fixed_and_nan_free(node: str) -> None:
    models = [
        {"model": "a", "cost_usd": 0.75},
        {"model": "b", "cost_usd": 0.25},
        {"model": "free", "cost_usd": None},
    ]
    result = _eval(
        node,
        "[" + ", ".join([
            f"knownCostTotal({json.dumps(models)})",
            "knownCostTotal([])",
            f"shareLabel(0.75, knownCostTotal({json.dumps(models)}))",
            "shareLabel(0.1, 0)",
            "shareLabel(0.1, null)",
            f"shareRatio(0.25, knownCostTotal({json.dumps(models)}))",
            "shareRatio(0.25, 0)",
        ]) + "]",
    )
    total, empty, share, zero_total, null_total, ratio, zero_ratio = result
    assert total == pytest.approx(1.0)
    assert empty is None  # nothing priced → no denominator, no NaN
    assert share == "75%"
    assert zero_total == "—" and null_total == "—"
    assert float(str(ratio).rstrip("%")) == pytest.approx(25.0)  # numeric, string cosmetic
    assert zero_ratio is None


def test_share_guards_null_and_clamps(node: str) -> None:
    result = _eval(
        node,
        "[" + ", ".join([
            "shareLabel(null, 10)",
            "shareLabel('', 10)",
            "shareRatio(null, 10)",
            "shareRatio(20, 10)",  # over 100% is clamped for rendering
            "shareRatio(-5, 10)",
            "shareLabel(5, 1e999)",  # non-finite denominator
        ]) + "]",
    )
    null_label, empty_label, null_ratio, over, under, inf_total = result
    assert null_label == "—" and empty_label == "—"  # unpriced never renders 0%
    assert null_ratio is None
    assert float(str(over).rstrip("%")) == 100.0
    assert float(str(under).rstrip("%")) == 0.0
    assert inf_total == "—"


def test_pricing_rates_and_estimate_labeling(node: str) -> None:
    config = {"pricing": {"m1": {"input": 2, "cache": 0.5, "output": 8}},
              "models": {"m2": {"input": 1}}}
    rows = {"prompt_tokens": 1_000_000, "cached_tokens": 400_000, "completion_tokens": 100_000}
    result = _eval(
        node,
        "[" + ", ".join([
            f"priceRates({json.dumps(config)}, 'm1')",
            f"priceRates({json.dumps(config)}, 'm2')",
            f"priceRates({json.dumps(config)}, 'unknown')",
            f"estimatedCostSplit({json.dumps(rows)}, priceRates({json.dumps(config)}, 'm1'))",
            f"estimatedCostSplit({json.dumps(rows)}, null)",
        ]) + "]",
    )
    rates_m1, rates_m2, rates_unknown, split, no_rates = result
    assert rates_m1 == {"input": 2, "cache": 0.5, "output": 8}
    assert rates_m2 == {"input": 1, "cache": None, "output": None}
    assert rates_unknown is None
    assert split["inputUsd"] == pytest.approx(1.2)  # 600k uncached * $2/M
    assert split["cacheUsd"] == pytest.approx(0.2)  # 400k * $0.5/M
    assert split["outputUsd"] == pytest.approx(0.8)
    assert no_rates is None  # unpriced model must not derive dollars


def test_pricing_null_rate_is_unknown_zero_is_free(node: str) -> None:
    result = _eval(
        node,
        "[" + ", ".join([
            f"priceRates({json.dumps({'pricing': {'a': {'input': None, 'cache': 1}}})}, 'a')",
            f"priceRates({json.dumps({'pricing': {'a': {'input': 0, 'cache': 0, 'output': 0}}})}, 'a')",
            f"priceRates({json.dumps({'pricing': {'a': {'input': '', 'cache': 'x'}}})}, 'a')",
            f"estimatedCostSplit({json.dumps({'prompt_tokens': 1000, 'cached_tokens': 0, 'completion_tokens': 100})}, priceRates({json.dumps({'pricing': {'a': {'input': 0}}})}, 'a'))",
        ]) + "]",
    )
    null_field, all_zero, blank, zero_split = result
    assert null_field == {"input": None, "cache": 1, "output": None}  # null ≠ free
    assert all_zero == {"input": 0, "cache": 0, "output": 0}  # explicit free stays 0
    assert blank is None  # all unknown → no rates, no false $0
    assert zero_split == {"inputUsd": 0, "cacheUsd": None, "outputUsd": None}  # only configured rate applies


def test_paging_multiple_pages_and_midway_failure(node: str) -> None:
    # 250 unique rows arrive as a 200-row page plus a 50-row page.
    expression = """(async () => {
      const row = (i) => ({ session_id: 's' + i, turn_index: 0 });
      const first = { total: 250, traces: Array.from({ length: 200 }, (_, i) => row(i)) };
      const second = { total: 250, traces: Array.from({ length: 50 }, (_, i) => row(200 + i)) };
      const ok = await fetchAllTraces(async (offset) => (offset === 0 ? first : second));
      const failed = await fetchAllTraces(async (offset) => {
        if (offset === 200) throw new Error('boom');
        return offset === 0 ? first : second;
      }).then(() => 'no-error', (error) => 'error: ' + error.message);
      return [ok.length, new Set(ok.map((r) => r.session_id)).size, failed];
    })()"""
    count, unique, failed = _eval(node, expression)
    assert count == 250
    assert unique == 250
    assert failed.startswith("error: boom")  # rejects instead of partial totals


def test_paging_overlap_must_cover_every_unique_turn(node: str) -> None:
    # Overlapping pages advance the offset by batch length while adding fewer
    # unique turns: [0,1] then [1,2] reaches offset 4 with only 3 unique rows.
    # With total=4 that is an incomplete window, never a complete answer.
    expression = """(async () => {
      const row = (i) => ({ session_id: 's', turn_index: i });
      const incomplete = await fetchAllTraces(async (offset) => (
        offset === 0
          ? { total: 4, traces: [row(0), row(1)] }
          : { total: 4, traces: [row(1), row(2)] }
      )).then(() => 'no-error', (error) => `${error.name}:${error.offset}:${error.total}`);
      // The same overlap is complete once total matches the unique coverage.
      const complete = await fetchAllTraces(async (offset) => (
        offset === 0
          ? { total: 3, traces: [row(0), row(1)] }
          : { total: 3, traces: [row(1), row(2)] }
      ));
      return [incomplete, complete.length];
    })()"""
    incomplete, complete = _eval(node, expression)
    assert incomplete == "TracesIncompleteError:4:4"
    assert complete == 3


def test_model_turn_coverage_derives_missing_turns_from_rows(node: str) -> None:
    # stats.by_model carries no turn count, so per-model missing-turn coverage
    # is derived from the loaded rows: dedupe on (session_id, turn_index),
    # group like the backend (model or "unknown"), count zero cost as priced
    # and missing cost as not priced.
    rows = [
        {"session_id": "s1", "turn_index": 0, "model": "m1", "cost_usd": 0.1},
        {"session_id": "s1", "turn_index": 1, "model": "m1", "cost_usd": None},
        # exact duplicate (same session + turn) must not be counted twice
        {"session_id": "s1", "turn_index": 1, "model": "m1", "cost_usd": None},
        {"session_id": "s2", "turn_index": 0, "model": "m2", "cost_usd": 0},
        {"session_id": "s3", "turn_index": 0, "model": "", "cost_usd": None},
    ]
    coverage = _eval(
        node,
        "Object.fromEntries(modelTurnCoverage($rows))".replace("$rows", json.dumps(rows)),
    )
    assert coverage == {
        "m1": {"turns": 2, "pricedTurns": 1},
        "m2": {"turns": 1, "pricedTurns": 1},  # explicit 0 is a real price
        "unknown": {"turns": 1, "pricedTurns": 0},
    }
    assert _eval(node, "Object.fromEntries(modelTurnCoverage([]))") == {}


def test_load_drops_snapshot_and_labels_recorded_known_costs() -> None:
    """cost.js reload markers: the old snapshot is dropped the moment a load
    starts (search/sort/pagers must not resurrect the previous period), the
    renderers refuse to draw without a snapshot, an accepted failure resets
    the DOM, the overview is labeled as recorded-known amounts, and per-model
    missing-turn coverage comes from the loaded rows."""
    script = _cost_script()
    assert script.count("view = null;") == 3  # declaration, load start, accepted failure
    assert script.count("if (!view) return;") == 3  # render, models, sessions
    # Sums/averages are recorded-known: a priced turn never proves every
    # model call inside it was priced, so no full-coverage claim.
    assert "số đã ghi nhận" in script
    assert "modelTurnCoverage(view.rows)" in script
    assert "partialNote(coverage.pricedTurns, coverage.turns)" in script


def test_pricing_disclosures_persist_across_repages() -> None:
    """TASK-018 follow-up: open/closed "Đơn giá" state is kept per model name
    within the fixed snapshot and cleared only on a fresh snapshot, so paging
    and filtering never collapse an open disclosure."""
    script = _cost_script()
    assert "const openPricing = new Set();" in script
    assert "details.open = openPricing.has(model.model);" in script
    assert "openPricing.add(model.model);" in script
    assert "openPricing.delete(model.model);" in script
    assert "openPricing.clear(); // disclosure state belongs to the old snapshot" in script


def test_multi_model_session_tokens_split_not_double_counted(node: str) -> None:
    rows = [
        {"session_id": "s1", "turn_index": 0, "requests": 1, "cost_usd": 0.01,
         "prompt_tokens": 200, "cached_tokens": 200, "completion_tokens": 5},
    ]
    overview = _eval(node, "overviewMetrics($rows)".replace("$rows", json.dumps(rows)))
    assert overview["inputTokens"] == 0  # all prompt tokens cached
    assert overview["cacheTokens"] == 200
    assert overview["inputTokens"] + overview["cacheTokens"] == 200


def test_overview_prompt_tokens_is_full_input_side(node: str) -> None:
    # The overview "Token đầu vào" value is promptTokens = uncached + cache
    # (cache is a subset of prompt), matching the 'N% qua cache' note —
    # never cache double-counted.
    rows = [
        {"session_id": "s1", "turn_index": 0, "prompt_tokens": 300,
         "cached_tokens": 100, "completion_tokens": 10},
        {"session_id": "s1", "turn_index": 1, "prompt_tokens": 50,
         "cached_tokens": None, "completion_tokens": 2},
    ]
    overview = _eval(node, "overviewMetrics($rows)".replace("$rows", json.dumps(rows)))
    assert overview["promptTokens"] == 350
    assert overview["inputTokens"] + overview["cacheTokens"] == overview["promptTokens"]
    assert overview["cacheTokens"] == 100


# --- Journal pager (TASK-018): pure math runs in Node via the marker block;
# --- the DOM paging itself is browser-test responsibility (TASK-019).


def _pager_block(path):
    text = path.read_text(encoding="utf-8")
    block = text.split("// >>> journal-pager", 1)[1]
    block = block[block.index("\n") + 1:]  # skip the rest of the marker line
    return block.split("// <<< journal-pager", 1)[0]


def _run_pager(node, path, expression):
    source = _pager_block(path) + "\nconsole.log(JSON.stringify(" + expression + "));\n"
    result = subprocess.run([node, "--input-type=module", "-e", source], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_pager_math_thirteen_items_make_two_pages(node):
    result = _run_pager(
        node,
        PAGER_JS,
        "[journalPageCount(13), journalPageCount(12), journalPageCount(0), journalPageCount(1), journalPageCount(25, 10)]",
    )
    assert result == [2, 1, 1, 1, 3]


def test_pager_clamps_at_both_bounds(node):
    result = _run_pager(
        node,
        PAGER_JS,
        "[journalClampPage(0, 2), journalClampPage(1, 2), journalClampPage(2, 2), journalClampPage(3, 2), journalClampPage(-5, 3)]",
    )
    assert result == [1, 1, 2, 2, 1]


def test_pager_resets_on_filter_sort_and_snapshot(node):
    script = _cost_script()
    # Search input, sort click and a fresh snapshot each restart at page 1
    # (the 4th hit is the `let modelsPage = 1` declaration).
    assert script.count("modelsPage = 1;") == 4
    assert script.count("sessionsPage = 1;") == 2
    # Both journals slice the in-memory snapshot; the share denominator and
    # aggregation still run on the FULL set, never on the page slice.
    assert "rows.slice(start, start + JOURNAL_PAGE_SIZE)" in script
    assert "sessions.slice(start, start + JOURNAL_PAGE_SIZE)" in script
    assert "knownCostTotal(models)" in script
    assert "aggregateSessions(view.rows)" in script
    # Page state is clamped back into range before every render.
    assert "modelsPage = journalClampPage(modelsPage, modelsPages)" in script
    assert "sessionsPage = journalClampPage(sessionsPage, sessionsPages)" in script
    # Pager reuses the shared button styles and Vietnamese labels.
    assert 'setAttribute("aria-label", "Trang trước")' in script
    assert 'setAttribute("aria-label", "Trang sau")' in script
    assert 'className = "screen-button journal-pager-step"' in script


def test_model_token_total_never_double_counts_cache(node: str) -> None:
    # Token-by-model total is the whole prompt side plus completion; cache is
    # already inside prompt_tokens, so prompt + completion is the full count.
    rows = [
        {"prompt_tokens": 300, "cached_tokens": 100, "completion_tokens": 10},
        {"prompt_tokens": 50, "cached_tokens": None, "completion_tokens": 2},
    ]
    totals = _eval(node, "[$rows.map(modelTokens)]".replace("$rows", json.dumps(rows)))
    assert totals == [[310, 52]]
    assert _eval(node, "knownTokenTotal($rows)".replace("$rows", json.dumps(rows))) == 362
    assert _eval(node, "knownTokenTotal([])") is None


def test_daily_costs_zero_fill_missing_days(node: str) -> None:
    by_day = [
        {"day": "2026-09-20", "cost_usd": 0.5},
        {"day": "2026-09-22", "cost_usd": None},
        {"day": "nope", "cost_usd": 99},
    ]
    days = _eval(
        node,
        "dailyCosts($rows, { from: '2026-09-20', to: '2026-09-22', days: 3 })"
        .replace("$rows", json.dumps(by_day)),
    )
    assert days == [
        {"day": "2026-09-20", "value": 0.5},
        {"day": "2026-09-21", "value": 0},
        {"day": "2026-09-22", "value": 0},
    ]


def test_average_display_value_and_meta(node: str) -> None:
    rows = [
        {"session_id": "s1", "turn_index": 0, "status": "completed", "cost_usd": 0.2},
        {"session_id": "s1", "turn_index": 1, "status": "failed", "cost_usd": 0.5},
        {"session_id": "s1", "turn_index": 2, "status": "completed", "cost_usd": None},
        {"session_id": "s1", "turn_index": 3, "status": "completed", "cost_usd": 0.1},
    ]
    display = _eval(
        node,
        "averageDisplay(overviewMetrics($rows))".replace("$rows", json.dumps(rows)),
    )
    assert display["value"] == pytest.approx(0.15)
    assert display["meta"] == [
        "2 lượt hợp lệ",
        "bỏ 1 lỗi · 1 chưa định giá",
    ]


def test_average_display_skip_line_lists_only_present_causes(node: str) -> None:
    # Unpriced-only basis skips the failed part and vice versa, so the skip
    # line never mentions a cause with zero turns.
    unpriced_only = [
        {"session_id": "s1", "turn_index": 0, "status": "completed", "cost_usd": 0.2},
        {"session_id": "s1", "turn_index": 1, "status": "completed", "cost_usd": None},
    ]
    display = _eval(
        node,
        "averageDisplay(overviewMetrics($rows))".replace("$rows", json.dumps(unpriced_only)),
    )
    assert display["meta"] == ["1 lượt hợp lệ", "bỏ 1 chưa định giá"]
    failed_only = [
        {"session_id": "s1", "turn_index": 0, "status": "completed", "cost_usd": 0.2},
        {"session_id": "s1", "turn_index": 1, "status": "failed", "cost_usd": 0.5},
    ]
    display = _eval(
        node,
        "averageDisplay(overviewMetrics($rows))".replace("$rows", json.dumps(failed_only)),
    )
    assert display["meta"] == ["1 lượt hợp lệ", "bỏ 1 lỗi"]


def test_average_display_empty_basis(node: str) -> None:
    rows = [
        {"session_id": "s1", "turn_index": 0, "status": "failed", "cost_usd": 0.5},
    ]
    display = _eval(
        node,
        "averageDisplay(overviewMetrics($rows))".replace("$rows", json.dumps(rows)),
    )
    assert display == {"value": None, "meta": ["không có lượt nào đủ giá để tính trung bình"]}
    empty = _eval(node, "averageDisplay(overviewMetrics([]))")
    assert empty == {"value": None, "meta": ["chưa có lượt nào"]}


def test_average_display_full_coverage_single_line(node: str) -> None:
    rows = [
        {"session_id": "s1", "turn_index": 0, "status": "completed", "cost_usd": 0.0},
        {"session_id": "s1", "turn_index": 1, "status": "completed", "cost_usd": 0.4},
    ]
    display = _eval(
        node,
        "averageDisplay(overviewMetrics($rows))".replace("$rows", json.dumps(rows)),
    )
    assert display["value"] == pytest.approx(0.2)
    assert display["meta"] == ["2 lượt hợp lệ"]
