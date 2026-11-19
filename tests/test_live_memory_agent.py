"""Opt-in live scenarios: real provider chooses memory tools and writes ledgers."""
from __future__ import annotations

import os

import pytest

from tests.live.memory_agent import (
    LIVE_ENV,
    SCENARIOS,
    live_config,
    live_config_path,
    live_enabled,
    run_scenario,
)


def test_live_env_flag_documented() -> None:
    assert LIVE_ENV == "THYCA_LIVE"
    assert isinstance(live_enabled(), bool)


@pytest.mark.live
@pytest.mark.skipif(
    not live_enabled(),
    reason=f"set {LIVE_ENV}=1 to run live Thyca memory-agent scenarios",
)
@pytest.mark.parametrize("name", SCENARIOS)
def test_live_memory_agent_scenario(name: str) -> None:
    path = live_config_path()
    if not path.is_file():
        pytest.skip(f"live config missing at {path}")
    try:
        live_config()
    except Exception as exc:  # noqa: BLE001 — surface config/key problems as skip
        pytest.skip(f"live config unusable: {exc}")
    result = run_scenario(name)
    assert result.passed, (
        f"{name} failed: {result.reasons}; "
        f"tools={[c.name for c in result.tool_calls]}; "
        f"reply={result.reply[:300]!r}"
    )
