"""ActiveMemory tests — TASK-304 verification."""
from __future__ import annotations

import stat
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.memory import ActiveMemory, ActiveSnapshot, tail_text

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def at(day: str, hour: int = 10) -> datetime:
    parts = [int(bit) for bit in day.split("-")]
    return datetime(parts[0], parts[1], parts[2], hour, 0, tzinfo=TZ)


def test_ensure_creates_missing_and_keeps_existing(tmp_path: Path) -> None:
    soul = tmp_path / "SOUL.md"
    soul.write_text("# existing soul\n", encoding="utf-8")
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    memory.ensure_files(at("2026-08-17"))
    assert soul.read_text(encoding="utf-8") == "# existing soul\n"
    assert (tmp_path / "USER.md").is_file()
    assert (tmp_path / "MEMORY.md").is_file()
    assert (tmp_path / "memory" / "2026-08-17.md").is_file()
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "memory").stat().st_mode) == 0o700


def test_refresh_sees_canonical_and_today_not_yesterday(tmp_path: Path) -> None:
    memory = ActiveMemory(tmp_path, tail_kb=4, timezone_name="Asia/Ho_Chi_Minh")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "2026-08-16.md").write_text("# yesterday original\n", encoding="utf-8")
    state = memory.open_session(at("2026-08-17"))
    snap = memory.refresh(state, at("2026-08-17"))
    assert snap.yesterday == "# yesterday original\n"
    (tmp_path / "SOUL.md").write_text("# soul v2\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text("# user v2\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("# mem v2\n", encoding="utf-8")
    (tmp_path / "memory" / "2026-08-17.md").write_text("# today v2\n", encoding="utf-8")
    (tmp_path / "memory" / "2026-08-16.md").write_text("# yesterday changed\n", encoding="utf-8")
    snap2 = memory.refresh(state, at("2026-08-17"))
    assert snap2.soul == "# soul v2\n"
    assert snap2.user == "# user v2\n"
    assert snap2.memory == "# mem v2\n"
    assert snap2.today == "# today v2\n"
    assert snap2.yesterday == "# yesterday original\n"


def test_soul_user_not_tailed_memory_is(tmp_path: Path) -> None:
    memory = ActiveMemory(tmp_path, tail_kb=1, timezone_name="Asia/Ho_Chi_Minh")
    memory.ensure_files(at("2026-08-17"))
    big = "x" * 2000
    (tmp_path / "SOUL.md").write_text(big, encoding="utf-8")
    (tmp_path / "USER.md").write_text(big, encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(
        "## 10:00 — old\nshort\n## 11:00 — new\n" + big + "\n",
        encoding="utf-8",
    )
    snap = memory.refresh(memory.open_session(at("2026-08-17")), at("2026-08-17"))
    assert snap.soul == big
    assert snap.user == big
    assert snap.memory.startswith("## 11:00 — new")
    assert "old" not in snap.memory


def test_day_rollover_swaps_and_fires_hook(tmp_path: Path) -> None:
    closed: list[str] = []
    memory = ActiveMemory(
        tmp_path,
        timezone_name="Asia/Ho_Chi_Minh",
        on_day_close=closed.append,
    )
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "2026-08-16.md").write_text("# d16\n", encoding="utf-8")
    state = memory.open_session(at("2026-08-17"))
    (tmp_path / "memory" / "2026-08-17.md").write_text("# d17 live\n", encoding="utf-8")
    snap = memory.refresh(state, at("2026-08-18"))
    assert state.day == "2026-08-18"
    assert snap.yesterday == "# d17 live\n"
    assert closed == ["2026-08-17"]
    assert (tmp_path / "memory" / "2026-08-18.md").is_file()


def test_tail_heading_newline_and_fence() -> None:
    budget = 64
    headed = "ignore\n## 09:00 — keep\n" + ("b" * 80)
    assert tail_text(headed, budget).startswith("## 09:00 — keep")
    fenced = "pre\n```\n" + ("c" * 80) + "\n```\n"
    tailed = tail_text(fenced, budget)
    assert tailed.startswith("```\n")
    assert "```" in tailed[3:]


def test_tail_does_not_split_utf8() -> None:
    text = "á" * 80
    out = tail_text(text, 50)
    out.encode("utf-8")
    assert out == "á" * (len(out))
    assert not out.startswith("\ufffd")


def test_missing_yesterday_is_empty(tmp_path: Path) -> None:
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    snap: ActiveSnapshot = memory.refresh(memory.open_session(at("2026-08-17")), at("2026-08-17"))
    assert snap.yesterday == ""
