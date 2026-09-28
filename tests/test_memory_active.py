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
    assert snap.today.startswith("[... truncated ")
    assert "\n## 11:00 — new\n" in snap.today
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
    out = tail_text(headed, budget)
    assert out.startswith("[... truncated ")
    assert "\n## 09:00 — keep\n" in out
    fenced = "pre\n```\n" + ("c" * 80) + "\n```\n"
    tailed = tail_text(fenced, budget)
    assert tailed.startswith("[... truncated ")
    assert "\n```\n" in tailed
    assert tailed.count("```") >= 2


def test_tail_does_not_split_utf8() -> None:
    text = "á" * 80
    out = tail_text(text, 50)
    out.encode("utf-8")
    assert out.startswith("[... truncated 110 bytes above ...]\n")
    body = out.split("\n", 1)[1]
    assert body == "á" * 25
    assert not body.startswith("\ufffd")


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


def _seed_today(tmp_path: Path, day: str, body: str) -> ActiveMemory:
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    memory.open_session(at(day))
    (tmp_path / "memory" / f"{day}.md").write_text(f"# {day}\n{body}", encoding="utf-8")
    return memory


def test_split_today_groups_by_session(tmp_path: Path) -> None:
    memory = _seed_today(
        tmp_path,
        "2026-09-28",
        "## 13:54 — own-note <!-- thyca {\"id\":\"aaaa1111\",\"imp\":3,\"chat\":\"SID-HERE\"} -->\n"
        "- body OWN detail\n"
        "## 15:04 — foreign-note <!-- thyca {\"id\":\"bbbb2222\",\"imp\":3,\"chat\":\"SID-OTHER\"} -->\n"
        "- body FOREIGN detail\n",
    )
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"), session_id="SID-HERE")
    assert "body OWN detail" in snap.today
    assert "body FOREIGN detail" not in snap.today
    assert "thyca {" not in snap.today  # metadata comments stripped, like legacy
    assert snap.today_elsewhere == "- 15:04 — foreign-note [2026-09-28#bbbb2222]"


def test_split_today_own_headings_carry_pullable_ids(tmp_path: Path) -> None:
    from thyca.memory.facade import MemoryFacade

    memory = _seed_today(
        tmp_path,
        "2026-09-28",
        "## 13:54 — own-note <!-- thyca {\"id\":\"aaaa1111\",\"imp\":3,\"chat\":\"SID-HERE\"} -->\n"
        "- body OWN detail\n",
    )
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"), session_id="SID-HERE")
    assert "## 13:54 — own-note [2026-09-28#aaaa1111]" in snap.today
    assert "thyca {" not in snap.today
    # The visible id resolves through the write-back tools.
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    assert "OWN detail" in facade.get(session_id="2026-09-28#aaaa1111")


def test_split_today_index_ids_are_pullable(tmp_path: Path) -> None:
    from thyca.memory.facade import MemoryFacade

    memory = _seed_today(
        tmp_path,
        "2026-09-28",
        "## 15:04 — foreign-note <!-- thyca {\"id\":\"bbbb2222\",\"imp\":3,\"chat\":\"SID-OTHER\"} -->\n"
        "- body FOREIGN detail\n"
        "## 15:05 — legacy-note\n"
        "- hand-written, no metadata\n",
    )
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"), session_id="SID-HERE")
    assert snap.today == "# 2026-09-28\n"  # day title has no owner, stays in here
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    lines = snap.today_elsewhere.splitlines()
    assert len(lines) == 2
    foreign_sid = lines[0].split("[")[1].rstrip("]")
    legacy_sid = lines[1].split("[")[1].rstrip("]")
    assert "FOREIGN detail" in facade.get(session_id=foreign_sid)
    assert "no metadata" in facade.get(session_id=legacy_sid)


def test_refresh_without_session_keeps_legacy_full_tail(tmp_path: Path) -> None:
    memory = _seed_today(
        tmp_path,
        "2026-09-28",
        "## 13:54 — own-note <!-- thyca {\"id\":\"aaaa1111\",\"imp\":3,\"chat\":\"SID-HERE\"} -->\n"
        "- body OWN detail\n"
        "## 15:04 — foreign-note <!-- thyca {\"id\":\"bbbb2222\",\"imp\":3,\"chat\":\"SID-OTHER\"} -->\n"
        "- body FOREIGN detail\n",
    )
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"))
    assert "body OWN detail" in snap.today
    assert "body FOREIGN detail" in snap.today
    assert snap.today_elsewhere == ""


def test_split_today_index_caps_at_25_lines(tmp_path: Path) -> None:
    from thyca.memory import ELSEWHERE_MAX_LINES

    assert ELSEWHERE_MAX_LINES == 25
    blocks = "".join(
        f"## 10:{i:02d} — note-{i:02d} <!-- thyca {{\"id\":\"{i:08x}\",\"imp\":3,\"chat\":\"SID-OTHER\"}} -->\n- detail {i:02d}\n"
        for i in range(30)
    )
    memory = _seed_today(tmp_path, "2026-09-28", blocks)
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"), session_id="SID-HERE")
    lines = snap.today_elsewhere.splitlines()
    assert len(lines) == 26  # 25 index lines + honesty marker
    assert "note-29" in lines[-2]  # newest kept
    assert lines[-1] == "[... 5 older lines omitted ...]"
    assert "note-00" not in snap.today_elsewhere  # oldest dropped


def test_split_duplicate_legacy_titles_resolve_to_own_bodies(tmp_path: Path) -> None:
    """Occurrence counting stays global across the here/index partition."""
    from thyca.memory.facade import MemoryFacade

    memory = _seed_today(
        tmp_path,
        "2026-09-28",
        "## 15:05 — dup <!-- thyca {\"id\":\"aaaa1111\",\"imp\":3,\"chat\":\"SID-HERE\"} -->\n"
        "- first body HERE\n"
        "## 15:06 — dup\n"
        "- second body FOREIGN\n"
        "## 15:07 — dup\n"
        "- third body FOREIGN\n",
    )
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"), session_id="SID-HERE")
    assert "first body HERE" in snap.today
    lines = snap.today_elsewhere.splitlines()
    assert len(lines) == 2
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    bodies = [
        facade.get(session_id=line.split("[")[1].rstrip("]")) for line in lines
    ]
    assert "second body FOREIGN" in bodies[0]
    assert "third body FOREIGN" in bodies[1]


def test_t7_tail_marker_reports_hidden_bytes_and_skips_when_whole() -> None:
    from thyca.memory.heading import is_session_heading, parse_heading

    whole = "## 10:00 — a\n- short\n"
    assert tail_text(whole, 1024) == whole  # no cut, no marker
    big = "## 10:00 — old\n- old body\n## 11:00 — new\n" + ("x" * 2000) + "\n- keep\n"
    out = tail_text(big, 64)
    marker, _, body = out.partition("\n")
    hidden = len(big.encode("utf-8")) - len(body.encode("utf-8"))
    assert marker == f"[... truncated {hidden} bytes above ...]"
    assert body.startswith("## 11:00 — new")
    assert is_session_heading(marker + "\n") is False
    assert parse_heading(marker) is None


def test_t7_elsewhere_marker_only_when_capped(tmp_path: Path) -> None:
    from thyca.memory.heading import is_session_heading

    def seed(n: int) -> str:
        blocks = "".join(
            f"## 10:{i:02d} — note-{i:02d} <!-- thyca {{\"id\":\"{i:08x}\",\"imp\":3,\"chat\":\"SID-OTHER\"}} -->\n- d{i}\n"
            for i in range(n)
        )
        memory = _seed_today(tmp_path, "2026-09-28", blocks)
        state = memory.open_session(at("2026-09-28"))
        return memory.refresh(state, at("2026-09-28"), session_id="SID-HERE").today_elsewhere

    exact = seed(25)
    assert len(exact.splitlines()) == 25
    assert "older lines omitted" not in exact
    over = seed(27)
    lines = over.splitlines()
    assert len(lines) == 26
    assert lines[-1] == "[... 2 older lines omitted ...]"
    assert is_session_heading(lines[-1] + "\n") is False


def test_t7_session_path_keeps_free_text_before_first_heading(tmp_path: Path) -> None:
    memory = _seed_today(
        tmp_path,
        "2026-09-28",
        "free note before any heading\n"
        "## 13:54 — own <!-- thyca {\"id\":\"aaaa1111\",\"imp\":3,\"chat\":\"SID-HERE\"} -->\n"
        "- own body\n"
        "## 15:04 — foreign <!-- thyca {\"id\":\"bbbb2222\",\"imp\":3,\"chat\":\"SID-OTHER\"} -->\n"
        "- foreign body\n",
    )
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"), session_id="SID-HERE")
    assert snap.today.startswith("# 2026-09-28\nfree note before any heading\n")
    assert "own body" in snap.today
    assert "foreign body" not in snap.today


def test_t7_session_path_prefix_subject_to_tail_budget(tmp_path: Path) -> None:
    memory = ActiveMemory(tmp_path, tail_kb=1, timezone_name="Asia/Ho_Chi_Minh")
    memory.open_session(at("2026-09-28"))
    (tmp_path / "memory" / "2026-09-28.md").write_text(
        "# 2026-09-28\n" + ("p" * 2000) + "\n"
        "## 13:54 — own <!-- thyca {\"id\":\"aaaa1111\",\"imp\":3,\"chat\":\"SID-HERE\"} -->\n"
        "- own body\n",
        encoding="utf-8",
    )
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"), session_id="SID-HERE")
    assert snap.today.startswith("[... truncated ")
    assert "own body" in snap.today


def test_t7_session_path_without_headings_keeps_whole_file(tmp_path: Path) -> None:
    memory = _seed_today(tmp_path, "2026-09-28", "just free notes\nno headings\n")
    state = memory.open_session(at("2026-09-28"))
    snap = memory.refresh(state, at("2026-09-28"), session_id="SID-HERE")
    assert snap.today == "# 2026-09-28\njust free notes\nno headings\n"
    assert snap.today_elsewhere == ""
