from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from thyca.core.channel import WindowRef
from thyca.memories.visibility import MemoryView, bound_memory_view, current_memory_view
from thyca.conversations.runtime.store import ConversationStore
from thyca.bots.workspace_context import WorkspaceContextBuilder, _channel_brief
from thyca.bots.work_items import prioritize_window, project_work_items
from thyca.bots.tasks import OpenTask

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _today_str(days_ago: int = 0) -> str:
    # Seeds must land on the wall-clock day: memory indexes a daily
    # file into SQLite only after day-close, and same-day entries are
    # served live from today's file. A fixed seed date rots the day
    # the wall clock moves past it (the Oct-19 date bomb).
    return (datetime.now(TZ).date() - timedelta(days=days_ago)).isoformat()


TODAY = _today_str()
YESTERDAY = _today_str(1)
DM_ID = "coder"
DM_OTHER = "helper"
GRP = "group-" + "a" * 32
GRP_OTHER = "group-" + "b" * 32
GRP_BIG = "group-" + "c" * 32
UNKNOWN_ID = "group-" + "f" * 32




def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00+07:00")


# Seed one runtime door: a DM is its bot id, a group is group-<hex32>.
def _seed_session(
    tmp_path: Path, sid: str, *, bots=(), dm_bot=None
) -> None:
    from thyca.conversations.store.runtime import ensure_runtime_store
    from thyca.conversations.runtime import ConversationManager

    ensure_runtime_store(tmp_path)
    store = ConversationStore(tmp_path / "sessions")
    store.ensure_dir()
    if dm_bot:
        ConversationManager(tmp_path / "sessions").ensure_dm(dm_bot)
        return
    path = store.create(sid)
    if bots:
        store.append_bots(path, list(bots))


def _seed_memory(tmp_path: Path, sid: str, title: str, body: str) -> None:
    from thyca.memories.facade import MemoryFacade

    MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh").remember(
        title, body, now=_at(TODAY), chat=sid
    )


def _group_window(sid: str, bots=("thyca", "coder")) -> WindowRef:
    return WindowRef(
        window_id=sid, channel_id="webui", kind="group", participants=tuple(bots)
    )


def _dm_window(sid: str, bot: str) -> WindowRef:
    return WindowRef(
        window_id=sid, channel_id="webui", kind="dm", participants=(bot,)
    )


def test_group_window_denies_dm_origin(tmp_path: Path) -> None:
    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    _seed_memory(tmp_path, DM_ID, "PRIVATE-TITLE", "PRIVATE-SUMMARY")
    _seed_memory(tmp_path, GRP, "PUBLIC-TITLE", "PUBLIC-SUMMARY")

    view = MemoryView(_group_window(GRP), "coder", tmp_path)
    with bound_memory_view(view):
        assert not view.can_read_origin(DM_ID)
        assert view.can_read_origin(GRP)
        assert view.can_read_origin(None) is False


def test_group_window_denies_other_group_and_unknown(tmp_path: Path) -> None:
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    _seed_session(tmp_path, GRP_OTHER, bots=["thyca", "helper"])
    _seed_memory(tmp_path, GRP_OTHER, "OTHER-GRP", "OTHER-BODY")

    view = MemoryView(_group_window(GRP), "coder", tmp_path)
    # participants (thyca,coder) not subset of (thyca,helper) -> denied
    assert not view.can_read_origin(GRP_OTHER)
    assert not view.can_read_origin(UNKNOWN_ID)


def test_group_window_broader_origin_allowed(tmp_path: Path) -> None:
    _seed_session(tmp_path, GRP, bots=["coder"])
    _seed_session(tmp_path, GRP_BIG, bots=["thyca", "coder", "helper"])
    view = MemoryView(_group_window(GRP, ("thyca", "coder")), "coder", tmp_path)
    # (thyca,coder) subset of origin (thyca,coder,helper) -> allowed
    assert view.can_read_origin(GRP_BIG)


def test_dm_view_rules(tmp_path: Path) -> None:
    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, DM_OTHER, dm_bot="helper")
    _seed_session(tmp_path, GRP, bots=["coder", "helper"])
    view = MemoryView(_dm_window(DM_ID, "coder"), "coder", tmp_path)
    assert view.can_read_origin(DM_ID)      # own DM
    assert not view.can_read_origin(DM_OTHER) # other bot's DM
    assert view.can_read_origin(GRP)           # participant
    assert view.can_read_origin(UNKNOWN_ID)         # unknown owner-scoped
    assert view.can_read_origin(None)


def test_team_owner_cannot_override_dm_boundary(tmp_path: Path) -> None:
    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    view = MemoryView(_group_window(GRP), "helper", tmp_path)
    assert not view.can_read_entry(DM_ID, ("team",))


def test_snapshot_filters_private_dm_entries(tmp_path: Path) -> None:
    from thyca.memories.active import ActiveMemory

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    _seed_memory(tmp_path, DM_ID, "PRIVATE-TITLE", "PRIVATE-SUMMARY")
    _seed_memory(tmp_path, GRP, "PUBLIC-TITLE", "PUBLIC-SUMMARY")

    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(_at(TODAY))
    with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
        snap = memory.refresh(state, _at(TODAY), conversation_id=GRP,
                              reader="coder")
    assert "PRIVATE-TITLE" not in snap.today
    assert "PRIVATE-TITLE" not in snap.today_elsewhere
    assert "PRIVATE-SUMMARY" not in snap.today
    assert "PUBLIC-TITLE" in snap.today


def test_private_view_still_reads_own_dm(tmp_path: Path) -> None:
    from thyca.memories.active import ActiveMemory

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_memory(tmp_path, DM_ID, "PRIVATE-TITLE", "PRIVATE-SUMMARY")
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(_at(TODAY))
    with bound_memory_view(MemoryView(_dm_window(DM_ID, "coder"), "coder", tmp_path)):
        snap = memory.refresh(state, _at(TODAY), conversation_id=DM_ID,
                              reader="coder")
    assert "PRIVATE-TITLE" in snap.today


def test_no_view_is_unchanged(tmp_path: Path) -> None:
    from thyca.memories.active import ActiveMemory

    _seed_memory(tmp_path, DM_ID, "PRIVATE-TITLE", "PRIVATE-SUMMARY")
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(_at(TODAY))
    snap = memory.refresh(state, _at(TODAY), conversation_id=DM_ID)
    assert "PRIVATE-TITLE" in snap.today
    assert current_memory_view() is None


def test_facade_get_denies_private_in_group(tmp_path: Path) -> None:
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.archived import ArchiveError

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("PRIVATE-TITLE", "PRIVATE-SUMMARY",
                    now=_at(TODAY), chat=DM_ID)
    day = facade.archive.day(_at(TODAY))
    sid = None
    for line in (tmp_path / "memory" / f"{day}.md").read_text().splitlines():
        if "PRIVATE-TITLE" in line and "id" in line:
            import json, re

            meta = json.loads(re.search(r"thyca (\{.*\})", line).group(1))
            sid = f"{day}#{meta['id']}"
    assert sid
    # Facade reindex picks the entry into SQLite; still denied in group view.
    facade._refresh_index()
    with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
        with pytest.raises(ArchiveError):
            facade.get(memory_id=sid)
        hits = facade.search("PRIVATE", limit=10).hits
        assert all("PRIVATE-TITLE" not in h.heading for h in hits)
        assert all("PRIVATE" not in h.heading for h in facade.recent(10))
    with bound_memory_view(MemoryView(_dm_window(DM_ID, "coder"), "coder", tmp_path)):
        assert "PRIVATE-SUMMARY" in facade.get(memory_id=sid)


def test_group_search_candidate_limit_still_returns_allowed(
        tmp_path: Path) -> None:
    """Private hits that outrank a public one are filtered BEFORE the
    final limit, so the allowed hit still surfaces."""
    from thyca.memories.facade import MemoryFacade

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    # Archive search only covers CLOSED days: write yesterday's file so
    # the candidate pool actually contains these entries.
    yesterday = _at(YESTERDAY)
    for i in range(3):
        facade.remember(f"needle-PRIVATE-{i}", f"needle body {i}",
                        now=yesterday, chat=DM_ID, bots=["coder"])
    facade.remember("needle-PUBLIC", "needle body",
                    now=yesterday, chat=GRP)
    facade._refresh_index()
    with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
        hits = facade.search("needle", limit=1).hits
        assert hits, "allowed public hit must survive candidate filtering"
        assert "PUBLIC" in hits[0].heading


def test_canonical_get_bypasses_daily_origin_check(tmp_path: Path) -> None:
    from thyca.memories.facade import MemoryFacade

    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("anything", "body", now=_at(TODAY), chat=GRP)
    facade._refresh_index()
    with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
        assert facade.get(memory_id="canonical#soul") is not None


def test_memory_tasks_hide_private_title_in_group(tmp_path: Path) -> None:
    from thyca.memories.facade import MemoryFacade

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("PRIVATE-TASK-TITLE", "body",
                    now=_at(TODAY), chat=DM_ID,
                    task="task-aabb1122", status="open", bots=["coder"])
    facade.remember("PUBLIC-TASK-TITLE", "body",
                    now=_at(TODAY), chat=GRP,
                    task="task-bbcc2233", status="open")
    with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
        catalog = facade.tasks(status="open")["tasks"]
    titles = [row.get("title", "") for row in catalog]
    assert not any("PRIVATE-TASK-TITLE" in t for t in titles)
    assert any("PUBLIC-TASK-TITLE" in t for t in titles)


def test_channel_brief_text_and_section(tmp_path: Path) -> None:
    from thyca.memories.active import ActiveSnapshot
    from thyca.llms.prompt_manager import PromptManager

    window = _group_window(GRP)
    hot = WorkspaceContextBuilder(tmp_path).build(
        ActiveSnapshot(soul="s", user="u", today="t"),
        bot_id="coder", window=window)
    assert hot.channel_brief == _channel_brief(window)
    assert "channel=webui" in hot.channel_brief
    assert "kind=group visibility=public" in hot.channel_brief
    assert "participants=thyca, coder" in hot.channel_brief
    rendered = PromptManager().build(hot)
    assert "<channel>" in rendered
    # channel sits before <today>
    assert rendered.index("<channel>") < rendered.index("<today>")


def test_builder_memory_read_gate(tmp_path: Path) -> None:
    from thyca.memories.active import ActiveSnapshot
    from thyca.bots.card import BotCard

    from thyca.bots.card import BotMemoryPolicy

    card = BotCard(bot_id="x", name="x", role="r", description="d",
                   persona="PERSONA-X", memory=BotMemoryPolicy(read=False))
    hot = WorkspaceContextBuilder(tmp_path).build(
        ActiveSnapshot(soul="soul", user="u", today="TODAY-LEAK",
                       today_elsewhere="ELSEWHERE-LEAK"),
        bot_id="x", card=card)
    assert hot.today == "" and hot.today_elsewhere == ""
    assert "PERSONA-X" in hot.soul


def test_workitem_projection_links_only_explicit(tmp_path: Path) -> None:
    linked = OpenTask(task="delegate the fix", status="open", room=GRP,
                      key="s1", memory_task_id="task-aabb1122")
    lookalike = OpenTask(task="task-aabb1122", status="open", room=GRP,
                         key="s2")
    items = project_work_items(
        [linked, lookalike],
        [{"task_id": "task-aabb1122", "status": "open",
          "title": "Memory task", "latest_memory_id": "2026-10-19#aaaa1111"}],
        owner_bot="coder")
    assert len(items) == 2
    canon = next(i for i in items if i.work_id == "task-aabb1122")
    assert canon.memory_task_id == "task-aabb1122"
    assert canon.runtime_refs == ("s1",)
    assert canon.current_window == GRP
    assert canon.title == "Memory task"
    assert canon.latest_memory_id == "2026-10-19#aaaa1111"
    assert canon.coordinator is None
    rt = next(i for i in items if i.work_id is None)
    assert rt.runtime_refs == ("s2",)
    ordered = prioritize_window(items, GRP)
    assert ordered[0].current_window == GRP
    other = prioritize_window(items, "2026-10-19T09-98-00_beef")
    assert len(other) == 2


def _owned_sid(facade, title: str, body: str, *, origin: str,
                 day: str, bots=("coder",), **kwargs) -> str:
    # Seed one owner-held entry and return its session id for get checks.
    return facade.remember(title, body, now=_at(day), chat=origin,
                           bots=list(bots), **kwargs)


def test_owner_dm_entry_readable_in_group(tmp_path: Path) -> None:
    # Owner's own-DM entry opens in a group: get with the flag returns
    # the body, plain get still denies it, and the tool-path search
    # (flag set) returns the hit.
    from thyca.memories.archived import ArchiveError
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = _owned_sid(facade, "OWN-DM-NOTE-1", "OWN-DM-BODY-1",
                     origin=DM_ID, day=YESTERDAY)
    facade._refresh_index()
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
            assert "OWN-DM-BODY-1" in facade.get(
                memory_id=sid, allow_own_cross_window=True)
            with pytest.raises(ArchiveError):
                facade.get(memory_id=sid)
            hits = facade.search(
                "OWN-DM-NOTE-1", limit=10,
                allow_own_cross_window=True).hits
            assert any("OWN-DM-NOTE-1" in h.heading for h in hits)
    finally:
        reset_bot_id(token)


def test_delegated_note_readable_in_owner_dm(tmp_path: Path) -> None:
    # BT-03 repro: a note delegated to coder (origin Thyca DM, owner coder)
    # opens in coder's own DM.
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, "thyca", dm_bot="thyca")
    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = _owned_sid(facade, "DELEGATED-NOTE-2", "DELEGATED-BODY-2",
                     origin="thyca", day=TODAY)
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(
                MemoryView(_dm_window(DM_ID, "coder"), "coder", tmp_path)):
            assert "DELEGATED-BODY-2" in facade.get(
                memory_id=sid, allow_own_cross_window=True)
    finally:
        reset_bot_id(token)


def test_owner_group_entry_readable_in_other_group(tmp_path: Path) -> None:
    # Owner's group entry opens in a different group with other members.
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    _seed_session(tmp_path, GRP_OTHER, bots=["thyca", "helper"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = _owned_sid(facade, "OWN-GRP-NOTE-3", "OWN-GRP-BODY-3",
                     origin=GRP, day=YESTERDAY)
    facade._refresh_index()
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(
                MemoryView(_group_window(GRP_OTHER, ("thyca", "helper")),
                           "coder", tmp_path)):
            assert "OWN-GRP-BODY-3" in facade.get(
                memory_id=sid, allow_own_cross_window=True)
            hits = facade.search(
                "OWN-GRP-NOTE-3", limit=10,
                allow_own_cross_window=True).hits
            assert any("OWN-GRP-NOTE-3" in h.heading for h in hits)
    finally:
        reset_bot_id(token)


def test_owner_group_entry_readable_in_larger_group(tmp_path: Path) -> None:
    # Owner's small-group entry opens in a larger group that covers it.
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, GRP, bots=["coder"])
    _seed_session(tmp_path, GRP_BIG, bots=["thyca", "coder", "helper"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = _owned_sid(facade, "OWN-GRP-NOTE-4", "OWN-GRP-BODY-4",
                     origin=GRP, day=YESTERDAY)
    facade._refresh_index()
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(
                MemoryView(
                    _group_window(GRP_BIG, ("thyca", "coder", "helper")),
                    "coder", tmp_path)):
            assert "OWN-GRP-BODY-4" in facade.get(
                memory_id=sid, allow_own_cross_window=True)
            hits = facade.search(
                "OWN-GRP-NOTE-4", limit=10,
                allow_own_cross_window=True).hits
            assert any("OWN-GRP-NOTE-4" in h.heading for h in hits)
    finally:
        reset_bot_id(token)


def test_owner_dm_task_visible_in_group_via_tool_path(tmp_path: Path) -> None:
    # Tool-path tasks (flag set) shows the owner's DM-origin task in a group.
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    _owned_sid(facade, "OWN-DM-TASK-5", "body", origin=DM_ID, day=TODAY,
               task="task-aabb1122", status="open")
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
            catalog = facade.tasks(
                status="open", allow_own_cross_window=True)["tasks"]
    finally:
        reset_bot_id(token)
    assert any("OWN-DM-TASK-5" in row.get("title", "") for row in catalog)


def test_other_bot_entry_still_denied_with_flag(tmp_path: Path) -> None:
    # Another bot's entry stays hidden even when the origin window matches.
    from thyca.memories.archived import ArchiveError
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, GRP, bots=["thyca", "coder", "helper"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = _owned_sid(facade, "HELPER-NOTE-6", "HELPER-BODY-6",
                     origin=GRP, day=YESTERDAY, bots=("helper",))
    facade._refresh_index()
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
            with pytest.raises(ArchiveError):
                facade.get(memory_id=sid)
            hits = facade.search(
                "HELPER-NOTE-6", limit=10,
                allow_own_cross_window=True).hits
            assert all("HELPER-NOTE-6" not in h.heading for h in hits)
    finally:
        reset_bot_id(token)


def test_team_co_owned_entry_still_denied_in_group(tmp_path: Path) -> None:
    # A team entry never takes the owner exemption, even for a listed bot.
    from thyca.memories.archived import ArchiveError
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = _owned_sid(facade, "TEAM-NOTE-7", "TEAM-BODY-7",
                     origin=DM_ID, day=YESTERDAY, bots=("team", "coder"))
    facade._refresh_index()
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
            with pytest.raises(ArchiveError):
                facade.get(memory_id=sid)
            hits = facade.search(
                "TEAM-NOTE-7", limit=10,
                allow_own_cross_window=True).hits
            assert all("TEAM-NOTE-7" not in h.heading for h in hits)
    finally:
        reset_bot_id(token)


def test_legacy_entry_from_other_dm_still_denied(tmp_path: Path) -> None:
    # A legacy (bots=None) entry from another bot's DM stays private.
    from thyca.memories.archived import ArchiveError
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, DM_OTHER, dm_bot="helper")
    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("LEGACY-NOTE-8", "LEGACY-BODY-8",
                          now=_at(YESTERDAY), chat=DM_OTHER)
    facade._refresh_index()
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(
                MemoryView(_dm_window(DM_ID, "coder"), "coder", tmp_path)):
            with pytest.raises(ArchiveError):
                facade.get(memory_id=sid)
    finally:
        reset_bot_id(token)


def test_search_without_flag_still_hides_owner_dm_hit(tmp_path: Path) -> None:
    # The facade default must not drift: without the flag the owner's
    # DM-origin hit stays hidden in a group.
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.identity import bind_bot_id, reset_bot_id

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    _owned_sid(facade, "OWN-DM-NOTE-9", "OWN-DM-BODY-9",
               origin=DM_ID, day=YESTERDAY)
    facade._refresh_index()
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
            hits = facade.search("OWN-DM-NOTE-9", limit=10).hits
            assert all("OWN-DM-NOTE-9" not in h.heading for h in hits)
    finally:
        reset_bot_id(token)


def test_builder_dm_pending_includes_question(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from thyca.memories.active import ActiveSnapshot

    pending = SimpleNamespace(
        pending_id="pf-1234abcd1234",
        work_id="task-aabb1122",
        status="waiting_private",
        origin_window=GRP,
        dm_window=DM_ID,
        question="Mã xác nhận  riêng là gì?",
    )
    builder = WorkspaceContextBuilder(tmp_path)
    hot = builder.build(
        ActiveSnapshot(soul="s", user="u", today="t"),
        bot_id="coder",
        window=_dm_window(DM_ID, "coder"),
        pending_followup=pending,
    )
    assert "question=Mã xác nhận riêng là gì?" in hot.private_followup

def test_denied_origin_matrix_hides_team_but_keeps_explicit_own(tmp_path: Path) -> None:
    # Pinned denial wins over same-window shortcuts and roster reloads,
    # while a concrete bot-owned entry keeps its proactive exemption.
    # Team, mixed-team, foreign, and unowned entries stay hidden.
    from thyca.memories.facade import MemoryFacade
    from thyca.memories.archived import ArchiveError

    _seed_session(tmp_path, GRP, bots=["thyca", "coder", "helper"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    when = _at(YESTERDAY)
    facade.remember("team marker TEAMMATRIX1", "TEAMMATRIX1 shared body",
                    now=when, chat=GRP)
    facade.remember("own marker OWNMATRIX1", "OWNMATRIX1 own body",
                    now=when, chat=GRP, bots=["helper"])
    facade.remember("foreign marker FOREIGNMATRIX1", "FOREIGNMATRIX1 other body",
                    now=when, chat=GRP, bots=["coder"])
    facade.remember("mixed marker MIXEDMATRIX1", "MIXEDMATRIX1 mixed body",
                    now=when, chat=GRP, bots=["helper", "team"])
    facade.remember("task team TASKTEAM1", "team task body",
                    now=when, chat=GRP, task="task-aabb1122", status="open")
    facade.remember("task own TASKOWN1", "own task body",
                    now=when, chat=GRP, bots=["helper"],
                    task="task-bbcc2233", status="open")
    facade._refresh_index()
    denied = MemoryView(window=None, bot_id="helper", thyca_dir=tmp_path,
                        denied_origins=frozenset({GRP}))
    member = MemoryView(_group_window(GRP, ("thyca", "coder", "helper")),
                        "helper", tmp_path)

    def _visible(view, query):
        with bound_memory_view(view):
            return {h.heading for h in facade.search(
                query, limit=10, allow_own_cross_window=True).hits}

    # Control: without the pin the member reads team and own entries.
    assert any("TEAMMATRIX1" in h for h in _visible(member, "TEAMMATRIX1"))
    assert any("OWNMATRIX1" in h for h in _visible(member, "OWNMATRIX1"))
    # Pinned: team, mixed-team, and foreign entries stay hidden; the
    # concrete own entry is still retrievable through the exemption.
    hidden = _visible(denied, "MATRIX1")
    assert all("TEAMMATRIX1" not in h for h in hidden)
    assert all("MIXEDMATRIX1" not in h for h in hidden)
    assert all("FOREIGNMATRIX1" not in h for h in hidden)
    assert any("OWNMATRIX1" in h for h in hidden)
    with bound_memory_view(denied):
        assert facade.tasks(status="open")["tasks"] == [], \
            "automatic catalogs carry no exemption"
        assert facade.search("TASKTEAM1", limit=10).hits == [] or all(
            "TASKTEAM1" not in h.heading
            for h in facade.search("TASKTEAM1", limit=10).hits)
        with pytest.raises(ArchiveError):
            facade.get(memory_id=_sid_for(facade, tmp_path, "TEAMMATRIX1"))
        recent_titles = [h.heading for h in facade.recent(
            10, allow_own_cross_window=True)]
        assert all("TEAMMATRIX1" not in h for h in recent_titles)
        assert all("MIXEDMATRIX1" not in h for h in recent_titles)


def _sid_for(facade, tmp_path: Path, needle: str) -> str:
    day = facade.archive.day(_at(YESTERDAY))
    for line in (tmp_path / "memory" / f"{day}.md").read_text().splitlines():
        if needle in line and "id" in line:
            import json as _json
            import re as _re
            meta = _json.loads(_re.search(r"thyca (\{.*\})", line).group(1))
            return f"{day}#{meta['id']}"
    raise AssertionError(f"seed missing: {needle}")
