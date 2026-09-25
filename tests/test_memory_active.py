"""ActiveMemory tests — TASK-304 verification."""
from __future__ import annotations

import stat
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from thyca.llm.prompt_manager import PromptManager
from thyca.memory import ActiveMemory, tail_text

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
    for name in ("user", "identity"):
        path = tmp_path / f"{name.upper()}.md"
        assert path.read_text(encoding="utf-8") == PromptManager().template(name)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not (tmp_path / "MEMORY.md").exists()
    assert (tmp_path / "memory" / "2026-08-17.md").is_file()
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "memory").stat().st_mode) == 0o700


@pytest.mark.parametrize("name", ["SOUL", "IDENTITY", "USER"])
@pytest.mark.parametrize("kind", ["custom", "empty", "stub"])
def test_ensure_never_replaces_existing_profiles(tmp_path: Path, name: str, kind: str) -> None:
    content = {"custom": "# Custom\nNội dung đã lưu.\n", "empty": "", "stub": f"# {name.title()}\n"}[kind]
    path = tmp_path / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(at("2026-08-17"))
    memory.ensure_files(at("2026-08-17"))
    assert path.read_text(encoding="utf-8") == content
    assert getattr(memory.refresh(state, at("2026-08-17")), name.lower()) == content


def test_new_profiles_are_injected_from_packaged_templates(tmp_path: Path) -> None:
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(at("2026-08-17"))
    snapshot = memory.refresh(state, at("2026-08-17"))
    manager = PromptManager()
    for name in ("identity", "soul", "user"):
        assert getattr(snapshot, name) == manager.template(name)
    text = manager.build(snapshot)
    assert f"<identity>\n{snapshot.identity.strip()}\n</identity>" in text
    assert f"<role>\n{snapshot.soul.strip()}\n</role>" in text
    assert f"<user>\n{snapshot.user}\n</user>" in text


def test_refresh_sees_canonical_and_today_not_previous_day(tmp_path: Path) -> None:
    memory = ActiveMemory(tmp_path, tail_kb=4, timezone_name="Asia/Ho_Chi_Minh")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "2026-08-16.md").write_text("# previous day\n", encoding="utf-8")
    state = memory.open_session(at("2026-08-17"))
    snap = memory.refresh(state, at("2026-08-17"))
    assert snap.today == "# 2026-08-17\n"
    assert not hasattr(snap, "yesterday")
    assert "previous day" not in PromptManager().build(snap)
    (tmp_path / "SOUL.md").write_text("# soul v2\n", encoding="utf-8")
    (tmp_path / "IDENTITY.md").write_text("# identity v2\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text("# user v2\n", encoding="utf-8")
    (tmp_path / "memory" / "2026-08-17.md").write_text("# today v2\n", encoding="utf-8")
    snap2 = memory.refresh(state, at("2026-08-17"))
    assert snap2.soul == "# soul v2\n"
    assert snap2.identity == "# identity v2\n"
    assert snap2.user == "# user v2\n"
    assert snap2.today == "# today v2\n"


def test_soul_user_not_tailed_today_is(tmp_path: Path) -> None:
    memory = ActiveMemory(tmp_path, tail_kb=1, timezone_name="Asia/Ho_Chi_Minh")
    memory.ensure_files(at("2026-08-17"))
    big = "x" * 2000
    (tmp_path / "SOUL.md").write_text(big, encoding="utf-8")
    (tmp_path / "USER.md").write_text(big, encoding="utf-8")
    (tmp_path / "memory" / "2026-08-17.md").write_text(
        "## 10:00 — old\nshort\n## 11:00 — new\n" + big + "\n",
        encoding="utf-8",
    )
    snap = memory.refresh(memory.open_session(at("2026-08-17")), at("2026-08-17"))
    assert snap.soul == big
    assert snap.user == big
    assert snap.today.startswith("## 11:00 — new")
    assert "old" not in snap.today


def test_day_rollover_creates_today_and_fires_hook(tmp_path: Path) -> None:
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
    assert snap.today == "# 2026-08-18\n"
    assert not hasattr(snap, "yesterday")
    assert "d17 live" not in snap.today
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


def test_refresh_strips_heading_comment(tmp_path: Path) -> None:
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    memory.ensure_files(at("2026-08-17"))
    (tmp_path / "memory" / "2026-08-17.md").write_text(
        '## 10:00 — cafe <!-- thyca {"id":"aaaaaaaa","imp":3,"exp":"2026-09-01T00:00:00Z"} -->\n- den\n',
        encoding="utf-8",
    )
    snap = memory.refresh(memory.open_session(at("2026-08-17")), at("2026-08-17"))
    assert "thyca" not in snap.today
    assert snap.today.startswith("## 10:00 — cafe")


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import pytest
from pathlib import Path

def test_x16_active_loads_prompts_via_template(tmp_path: Path) -> None:
    from thyca.llm.prompt_manager import PromptManager
    from thyca.memory.active import ActiveMemory, _packaged

    ActiveMemory(tmp_path).ensure_files()
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == PromptManager().template(
        "soul"
    )
    assert _packaged("soul", "fallback") == PromptManager().template("soul")


def test_x16_active_keeps_missing_file_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from thyca.llm import prompt_manager as pm
    from thyca.memory.active import _packaged

    def boom(self, name: str) -> str:
        raise FileNotFoundError(name)

    monkeypatch.setattr(pm.PromptManager, "template", boom)
    assert _packaged("soul", "fallback") == "fallback"


def test_m5_refresh_recreates_deleted_today(tmp_path: Path) -> None:
    from datetime import datetime

    from thyca.memory.active import ActiveMemory

    memory = ActiveMemory(tmp_path)
    now = datetime(2026, 8, 17, 12, 0)
    state = memory.open_session(now)
    assert state.today_path.is_file()
    state.today_path.unlink()
    snapshot = memory.refresh(state, now)
    assert state.today_path.is_file()
    assert "# 2026-08-17" in state.today_path.read_text(encoding="utf-8")
    assert snapshot.today != ""
