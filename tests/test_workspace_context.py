from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from thyca.core.channel import WindowRef
from thyca.memory.visibility import MemoryView, bound_memory_view, current_memory_view
from thyca.sessions.store import SessionStore
from thyca.bots.workspace_context import WorkspaceContextBuilder, _channel_brief
from thyca.bots.work_items import WorkItem, prioritize_window, project_work_items
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
DM_ID = f"{TODAY}T09-00-00_c0de"
DM_OTHER = f"{TODAY}T09-01-00_1ee2"
GRP = f"{TODAY}T09-02-00_ab12"
GRP_OTHER = f"{TODAY}T09-03-00_5e12"
GRP_BIG = f"{TODAY}T09-04-00_7e34"




def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00+07:00")


def _seed_session(
    tmp_path: Path, sid: str, *, bots=(), dm_bot=None
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.ensure_dir()
    path = store.create(sid)
    if bots:
        store.append_bots(path, list(bots))
    if dm_bot:
        store.append_dm(path, dm_bot)


def _seed_memory(tmp_path: Path, sid: str, title: str, body: str) -> None:
    from thyca.memory.facade import MemoryFacade

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
    assert not view.can_read_origin(f"{TODAY}T09-99-00_dead")


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
    assert view.can_read_origin(f"{TODAY}T09-99-00_dead")         # legacy owner-scoped
    assert view.can_read_origin(None)


def test_team_owner_cannot_override_dm_boundary(tmp_path: Path) -> None:
    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    view = MemoryView(_group_window(GRP), "helper", tmp_path)
    assert not view.can_read_entry(DM_ID, ("team",))


def test_snapshot_filters_private_dm_entries(tmp_path: Path) -> None:
    from thyca.memory.active import ActiveMemory

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    _seed_memory(tmp_path, DM_ID, "PRIVATE-TITLE", "PRIVATE-SUMMARY")
    _seed_memory(tmp_path, GRP, "PUBLIC-TITLE", "PUBLIC-SUMMARY")

    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(_at(TODAY))
    with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
        snap = memory.refresh(state, _at(TODAY), session_id=GRP,
                              reader="coder")
    assert "PRIVATE-TITLE" not in snap.today
    assert "PRIVATE-TITLE" not in snap.today_elsewhere
    assert "PRIVATE-SUMMARY" not in snap.today
    assert "PUBLIC-TITLE" in snap.today


def test_private_view_still_reads_own_dm(tmp_path: Path) -> None:
    from thyca.memory.active import ActiveMemory

    _seed_session(tmp_path, DM_ID, dm_bot="coder")
    _seed_memory(tmp_path, DM_ID, "PRIVATE-TITLE", "PRIVATE-SUMMARY")
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(_at(TODAY))
    with bound_memory_view(MemoryView(_dm_window(DM_ID, "coder"), "coder", tmp_path)):
        snap = memory.refresh(state, _at(TODAY), session_id=DM_ID,
                              reader="coder")
    assert "PRIVATE-TITLE" in snap.today


def test_no_view_is_unchanged(tmp_path: Path) -> None:
    from thyca.memory.active import ActiveMemory

    _seed_memory(tmp_path, DM_ID, "PRIVATE-TITLE", "PRIVATE-SUMMARY")
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(_at(TODAY))
    snap = memory.refresh(state, _at(TODAY), session_id=DM_ID)
    assert "PRIVATE-TITLE" in snap.today
    assert current_memory_view() is None


def test_facade_get_denies_private_in_group(tmp_path: Path) -> None:
    from thyca.memory.facade import MemoryFacade
    from thyca.memory.archived import ArchiveError

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
            facade.get(session_id=sid)
        hits = facade.search("PRIVATE", limit=10).hits
        assert all("PRIVATE-TITLE" not in h.heading for h in hits)
        assert all("PRIVATE" not in h.heading for h in facade.recent(10))
    with bound_memory_view(MemoryView(_dm_window(DM_ID, "coder"), "coder", tmp_path)):
        assert "PRIVATE-SUMMARY" in facade.get(session_id=sid)


def test_group_search_candidate_limit_still_returns_allowed(
        tmp_path: Path) -> None:
    """Private hits that outrank a public one are filtered BEFORE the
    final limit, so the allowed hit still surfaces."""
    from thyca.memory.facade import MemoryFacade

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
    from thyca.memory.facade import MemoryFacade

    _seed_session(tmp_path, GRP, bots=["thyca", "coder"])
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("anything", "body", now=_at(TODAY), chat=GRP)
    facade._refresh_index()
    with bound_memory_view(MemoryView(_group_window(GRP), "coder", tmp_path)):
        assert facade.get(session_id="canonical#soul") is not None


def test_memory_tasks_hide_private_title_in_group(tmp_path: Path) -> None:
    from thyca.memory.facade import MemoryFacade

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
    from thyca.memory.active import ActiveSnapshot
    from thyca.llm.prompt_manager import PromptManager

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
    from thyca.memory.active import ActiveSnapshot
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
          "title": "Memory task", "latest_session_id": "2026-10-19#aaaa1111"}],
        owner_bot="coder")
    assert len(items) == 2
    canon = next(i for i in items if i.work_id == "task-aabb1122")
    assert canon.memory_task_id == "task-aabb1122"
    assert canon.runtime_refs == ("s1",)
    assert canon.current_window == GRP
    assert canon.title == "Memory task"
    assert canon.latest_session_id == "2026-10-19#aaaa1111"
    assert canon.coordinator is None
    rt = next(i for i in items if i.work_id is None)
    assert rt.runtime_refs == ("s2",)
    ordered = prioritize_window(items, GRP)
    assert ordered[0].current_window == GRP
    other = prioritize_window(items, "2026-10-19T09-98-00_beef")
    assert len(other) == 2


def test_builder_dm_pending_includes_question(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from thyca.memory.active import ActiveSnapshot

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
