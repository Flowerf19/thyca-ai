"""Wave 4 regression tests: ledger safety, profile markers, trigram, purge churn, today search."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from tests.bench.memory_edge_common import BASE_NOW, TZ_NAME
from tests.bench.memory_edge_pack_a import (
    _sim_fts_vs_trigram_unnormalized_score_inversion,
    _sim_other_fuzzy_dedupe_drops_negation_flip,
    _sim_other_single_heading_forget_collateral_damage,
    _sim_profile_manual_heading_corrupts_markers,
    _sim_purge_expired_rewrites_every_daily_file_unconditionally,
    _sim_trigram_search_fails_single_word_typo,
)
from tests.bench.memory_edge_suite import (
    _sim_same_day_truncation_blind_spot,
    _sim_today_elsewhere_overflow_blind_spot,
)
from thyca.core.channel import WindowRef
from thyca.memories.facade import MemoryFacade
from thyca.memories.profile import PROFILE_AUTO_END, PROFILE_AUTO_START, update_profile_block
from thyca.memories.visibility import MemoryView, bound_memory_view


def test_wave4_ledger_and_profile_safety(tmp_path: Path) -> None:
    for runner in (
        _sim_other_fuzzy_dedupe_drops_negation_flip,
        _sim_other_single_heading_forget_collateral_damage,
        _sim_profile_manual_heading_corrupts_markers,
    ):
        case_dir = tmp_path / runner.__name__
        case_dir.mkdir()
        res = runner(case_dir)
        assert res.passed, f"{res.case_id}: {res.findings}"


def test_wave4_retrieval_trigram_purge_and_today_search(tmp_path: Path) -> None:
    for runner in (
        _sim_trigram_search_fails_single_word_typo,
        _sim_fts_vs_trigram_unnormalized_score_inversion,
        _sim_purge_expired_rewrites_every_daily_file_unconditionally,
        _sim_same_day_truncation_blind_spot,
        _sim_today_elsewhere_overflow_blind_spot,
    ):
        case_dir = tmp_path / runner.__name__
        case_dir.mkdir()
        res = runner(case_dir)
        assert res.passed, f"{res.case_id}: {res.findings}"


def test_today_in_memory_search_respects_view_and_never_writes_sqlite(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    dm_sid = facade.remember(
        "Private note",
        "SECRET_TODAY_DM_TOKEN_XYZ",
        chat="dm-priv",
        bots=["thyca"],
        now=BASE_NOW,
    )
    grp_sid = facade.remember(
        "Group note",
        "PUBLIC_TODAY_GRP_TOKEN_XYZ",
        chat="grp-team",
        bots=["thyca"],
        now=BASE_NOW,
    )
    facade.archive.reindex(BASE_NOW)
    assert facade.archive.fts_memory_hits("SECRET_TODAY_DM_TOKEN_XYZ", None, BASE_NOW, 10)[0] == []
    assert facade.archive.fts_memory_hits("PUBLIC_TODAY_GRP_TOKEN_XYZ", None, BASE_NOW, 10)[0] == []

    group_view = MemoryView(
        window=WindowRef(
            window_id="grp-team",
            channel_id="webui",
            kind="group",
            participants=("thyca", "coder"),
        ),
        bot_id="thyca",
        thyca_dir=tmp_path,
        denied_origins=frozenset({"dm-priv"}),
    )
    with bound_memory_view(group_view):
        dm_auto = facade.search("SECRET_TODAY_DM_TOKEN_XYZ", now=BASE_NOW)
        grp_auto = facade.search("PUBLIC_TODAY_GRP_TOKEN_XYZ", now=BASE_NOW)
    assert dm_auto.hits == []
    assert [h.memory_id for h in grp_auto.hits] == [grp_sid]

    dm_self = facade.search("SECRET_TODAY_DM_TOKEN_XYZ", now=BASE_NOW)
    assert [h.memory_id for h in dm_self.hits] == [dm_sid]


def test_today_partial_does_not_outrank_exact_fts_and_exact_today_survives_truncation(
    tmp_path: Path,
) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    past = BASE_NOW - timedelta(days=2)
    exact_fts_sid = facade.remember(
        "Triển khai Redis",
        "Kế hoạch triển khai Redis cluster trên staging với 3 node",
        now=past,
    )
    for idx in range(6):
        facade.remember(
            f"Archive note {idx}",
            f"Ghi chú cũ số {idx} nhắc tới từ khóa độc nhất alphaqueue",
            now=past + timedelta(minutes=idx + 1),
        )
    facade.archive.reindex(BASE_NOW)

    facade.remember(
        "Ghi chú sáng nay",
        "Đang triển khai giao diện trang chủ mới",
        now=BASE_NOW,
    )
    today_exact_sid = facade.remember(
        "Cập nhật alphaqueue hôm nay",
        "alphaqueue vừa chuyển sang bản mới sáng nay",
        now=BASE_NOW + timedelta(minutes=1),
    )

    multi = facade.search("triển khai redis", limit=5, now=BASE_NOW)
    assert multi.hits
    assert multi.hits[0].memory_id == exact_fts_sid
    assert multi.hits[0].match_type == "fts"

    short = facade.search("alphaqueue", limit=5, now=BASE_NOW)
    assert today_exact_sid in [h.memory_id for h in short.hits]


def test_profile_markers_safe_when_heading_prefix_matches_profile(tmp_path: Path) -> None:
    user_md = tmp_path / "USER.md"
    user_md.write_text("# User\n\n## Profile Notes\n- Keep manual note\n", encoding="utf-8")
    update_profile_block(user_md, ["- Auto bullet one"])
    first = user_md.read_text(encoding="utf-8")
    assert first.count(PROFILE_AUTO_START) == 1
    assert first.count(PROFILE_AUTO_END) == 1
    assert first.index(PROFILE_AUTO_START) < first.index(PROFILE_AUTO_END)
    assert "- Keep manual note" in first
    assert "- Auto bullet one" in first

    update_profile_block(user_md, ["- Auto bullet one"])
    assert user_md.read_text(encoding="utf-8") == first
