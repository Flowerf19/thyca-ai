"""Contract tests for the offline memory/task failure-case simulation suite."""
from __future__ import annotations

from pathlib import Path

from tests.bench.memory_edge_suite import (
    EDGE_SIMULATIONS,
    format_markdown_report,
    run_all_edge_simulations,
)
from tests.live.edge_scenarios import EDGE_SCENARIOS
from tests.live.memory_agent import SCENARIOS


def test_offline_edge_simulation_suite_executes_all_cases(tmp_path: Path) -> None:
    report = run_all_edge_simulations(tmp_path)
    assert report["total"] == len(EDGE_SIMULATIONS) == 29
    assert report["passed"] + report["failed"] == report["total"]
    assert report["failed"] == 0, [c for c in report["cases"] if not c["passed"]]
    ids = {c["case_id"] for c in report["cases"]}
    assert ids == {case_id for case_id, _grp, _fn in EDGE_SIMULATIONS}
    for case in report["cases"]:
        assert isinstance(case["passed"], bool)
        assert isinstance(case["findings"], list)
        assert case["severity"] in ("P0", "P1", "P2")
        if not case["passed"]:
            assert case["findings"], f"failed case missing findings: {case}"
    md = format_markdown_report(report)
    assert "Offline Memory & Task Failure-Case Simulation Report" in md
    assert "ghost_31_days_ttl_silent_deletion" in md
    assert "group_chat_leaks_auto_profile_block" in md


def test_live_edge_scenarios_registered() -> None:
    assert len(EDGE_SCENARIOS) == 20
    for name in EDGE_SCENARIOS:
        assert name in SCENARIOS
