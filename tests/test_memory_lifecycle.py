"""remember / forget / reinforce / TTL — GOAL-006."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from thyca.memory.heading import TTL_DAYS, parse_heading
from thyca.tools.memory import MemoryFacade


def test_remember_default_month_and_get_resets_ttl(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "likes ca phe den", target="memory", now=t0)
    assert sid.startswith("memory#")
    text = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    meta = next(m for line in text.splitlines() if (m := parse_heading(line)))
    assert meta.importance == 3
    assert meta.expires_at == "2026-08-31T12:00:00Z"
    t1 = t0 + timedelta(days=5)
    facade.get(session_id=sid, now=t1)
    text = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    meta = next(m for line in text.splitlines() if (m := parse_heading(line)))
    assert meta.expires_at == "2026-09-05T12:00:00Z"


def test_search_does_not_refresh_and_forget_deletes(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("thit", "an thit quay", target="memory", now=t0)
    before = next(
        m for line in (tmp_path / "MEMORY.md").read_text(encoding="utf-8").splitlines() if (m := parse_heading(line))
    )
    facade.search("thit quay", now=t0)
    after = next(
        m for line in (tmp_path / "MEMORY.md").read_text(encoding="utf-8").splitlines() if (m := parse_heading(line))
    )
    assert after.expires_at == before.expires_at
    facade.forget(sid, now=t0)
    assert "thit quay" not in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert facade.search("thit quay", now=t0).hits == []


def test_expired_deleted_on_reindex(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("x", "ttl-purge-token-zzzx", target="memory", importance=1, now=t0)
    assert "ttl-purge-token-zzzx" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert TTL_DAYS[5] == 180
    facade._refresh_index(t0 + timedelta(days=4))
    assert "ttl-purge-token-zzzx" not in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
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


def test_remember_rejects_user_and_soul(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    try:
        facade.remember("me", "I am me", target="soul")  # type: ignore[arg-type]
    except Exception as exc:
        assert "daily|memory" in str(exc)
    else:
        raise AssertionError("expected reject")
    try:
        facade.remember("me", "I am me", target="user")  # type: ignore[arg-type]
    except Exception as exc:
        assert "daily|memory" in str(exc)
    else:
        raise AssertionError("expected reject")
