from __future__ import annotations

from thyca.config import PricingCfg
from thyca.llm.pricing import cost_for


USAGE = {"prompt_tokens": 100, "cached_tokens": 20, "completion_tokens": 10, "total_tokens": 110}
MINI = {"gpt-4o-mini": PricingCfg(input=0.15, cache=0.075, output=0.60)}


def test_cost_for_known_model_matches_table() -> None:
    # ((100-20)*0.15 + 20*0.075 + 10*0.60) / 1e6
    assert cost_for("gpt-4o-mini", USAGE, MINI) == round(
        (80 * 0.15 + 20 * 0.075 + 10 * 0.60) / 1_000_000, 6
    )


def test_cost_for_unknown_model_is_none() -> None:
    assert cost_for("foo/bar", USAGE, MINI) is None
    assert cost_for(None, USAGE, MINI) is None
    assert cost_for("gpt-4o-mini", None, MINI) is None


def test_cached_is_cheaper_than_uncached() -> None:
    uncached = {**USAGE, "cached_tokens": 0}
    assert cost_for("gpt-4o-mini", USAGE, MINI) < cost_for("gpt-4o-mini", uncached, MINI)


def test_default_prices_cover_gpt_4o_mini_without_config() -> None:
    assert cost_for("gpt-4o-mini", USAGE) == cost_for("gpt-4o-mini", USAGE, MINI)


def test_resolves_dated_and_prefixed_model_ids() -> None:
    expected = cost_for("gpt-4o-mini", USAGE, MINI)
    assert cost_for("gpt-4o-mini-2024-07-18", USAGE, MINI) == expected
    assert cost_for("openai/gpt-4o-mini", USAGE, MINI) == expected
    assert cost_for("openai/gpt-4o-mini-2024-07-18", USAGE) == expected


def test_config_overlay_beats_default() -> None:
    overlay = {"gpt-4o-mini": PricingCfg(input=1.0, cache=1.0, output=1.0)}
    assert cost_for("gpt-4o-mini", USAGE, overlay) == round(110 / 1_000_000, 6)


def test_gpt_56_luna_prices() -> None:
    expected = round((80 * 0.20 + 20 * 0.02 + 10 * 1.20) / 1_000_000, 6)
    assert cost_for("gpt-5.6-luna", USAGE) == expected
    assert cost_for("openai/gpt-5.6-luna", USAGE) == expected
    overlay = {"openai/gpt-5.6-luna": PricingCfg(input=0.20, cache=0.02, output=1.20)}
    assert cost_for("openai/gpt-5.6-luna", USAGE, overlay) == expected


def test_muse_spark_contributor_prices() -> None:
    expected = round((80 * 0.10 + 20 * 0.002 + 10 * 0.20) / 1_000_000, 6)
    assert cost_for("muse-spark-1.2-contributor", USAGE) == expected
    assert cost_for("meta/muse-spark-1.2-contributor", USAGE) == expected
    overlay = {"meta/muse-spark-1.2-contributor": PricingCfg(input=0.10, cache=0.002, output=0.20)}
    assert cost_for("meta/muse-spark-1.2-contributor", USAGE, overlay) == expected
