"""remember / forget / reinforce / TTL — GOAL-006."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from thyca.memory.heading import TTL_DAYS, parse_heading
from thyca.tools.memory import MemoryFacade


def test_remember_default_month_and_get_resets_ttl(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "likes ca phe den", now=t0)
    assert sid.startswith("2026-08-01#")
    daily = tmp_path / "memory" / "2026-08-01.md"
    text = daily.read_text(encoding="utf-8")
    meta = next(m for line in text.splitlines() if (m := parse_heading(line)))
    assert meta.importance == 3
    assert meta.expires_at == "2026-08-31T12:00:00Z"
    t1 = t0 + timedelta(days=5)
    facade.get(session_id=sid, now=t1)
    text = daily.read_text(encoding="utf-8")
    meta = next(m for line in text.splitlines() if (m := parse_heading(line)))
    assert meta.expires_at == "2026-09-05T12:00:00Z"


def test_search_does_not_refresh_and_forget_deletes(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("thit", "an thit quay", now=t0)
    daily = tmp_path / "memory" / "2026-08-01.md"
    before = next(
        m for line in daily.read_text(encoding="utf-8").splitlines() if (m := parse_heading(line))
    )
    later = t0 + timedelta(days=1)
    facade.search("thit quay", now=later)
    after = next(
        m for line in daily.read_text(encoding="utf-8").splitlines() if (m := parse_heading(line))
    )
    assert after.expires_at == before.expires_at
    facade.forget(sid, now=later)
    assert "thit quay" not in daily.read_text(encoding="utf-8")
    assert facade.search("thit quay", now=later).hits == []


def test_expired_deleted_on_reindex(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("x", "ttl-purge-token-zzzx", importance=1, now=t0)
    daily = tmp_path / "memory" / "2026-08-01.md"
    assert "ttl-purge-token-zzzx" in daily.read_text(encoding="utf-8")
    assert TTL_DAYS[5] == 180
    facade._refresh_index(t0 + timedelta(days=4))
    assert "ttl-purge-token-zzzx" not in daily.read_text(encoding="utf-8")
    assert facade.search("ttl-purge-token-zzzx", now=t0 + timedelta(days=4)).hits == []


def test_reject_forget_soul(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    (tmp_path / "SOUL.md").write_text("# Soul\n", encoding="utf-8")
    try:
        facade.forget("canonical#soul")
    except Exception as exc:
        assert "cannot forget" in str(exc)
    else:
        raise AssertionError("expected reject")




def test_update_keeps_entry_id_and_reindexes(tmp_path: Path) -> None:
    from thyca.tools.memory import MemoryFacade

    facade = MemoryFacade(tmp_path)
    sid = facade.remember("Chủ đề cũ", "nội dung cũ", now=None)
    before = facade.get(session_id=sid)
    facade.update(sid, topic="Chủ đề mới", summary="nội dung mới", content="dòng chi tiết")
    after = facade.get(session_id=sid)
    # id nhúng trong heading giữ nguyên — session_id không đổi
    assert 'Chủ đề cũ' not in after
    assert "Chủ đề mới" in after
    assert "nội dung mới" in after
    assert "dòng chi tiết" in after
    import re

    id_before = re.search(r'"id":"([0-9a-f]{8})"', before)
    id_after = re.search(r'"id":"([0-9a-f]{8})"', after)
    assert id_before and id_after and id_before.group(1) == id_after.group(1)


def test_update_not_found(tmp_path: Path) -> None:
    import pytest

    from thyca.memory.archived import ArchiveError
    from thyca.tools.memory import MemoryFacade

    facade = MemoryFacade(tmp_path)
    with pytest.raises(ArchiveError):
        facade.update("nope#deadbeef", topic="x")


def test_update_topic_only_keeps_body(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path)
    sid = facade.remember("Chủ đề cũ", "nội dung giữ", content="dòng chi tiết")
    facade.update(sid, topic="Chủ đề mới")
    after = facade.get(session_id=sid)
    assert "Chủ đề cũ" not in after
    assert "Chủ đề mới" in after
    assert "nội dung giữ" in after
    assert "dòng chi tiết" in after
