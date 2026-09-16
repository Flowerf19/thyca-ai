"""remember / forget / reinforce / TTL — GOAL-006."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

import pytest

import thyca.memory.writer as writer_module
from thyca.memory.heading import TTL_DAYS, parse_heading
from thyca.tools.memory import MemoryFacade


def test_facade_indexes_existing_sources_on_open(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text("# Soul\nstartup-canonical-token\n", encoding="utf-8")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "2026-08-01.md").write_text(
        "# 2026-08-01\n"
        "## 10:00 — old note\n"
        "- startup-daily-token\n",
        encoding="utf-8",
    )

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")

    assert facade.search("startup-canonical-token").hits
    assert facade.search("startup-daily-token").hits


def test_canonical_write_refreshes_search_index(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text("# Soul\namberquartz\n", encoding="utf-8")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    assert facade.search("amberquartz").hits

    facade.write_canonical("SOUL.md", "# Soul\nvioletfjord\n")

    assert facade.search("violetfjord").hits
    assert facade.search("amberquartz").hits == []


def test_remember_default_month_and_get_resets_ttl(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
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


def test_two_facades_serialize_append_with_update(tmp_path: Path, monkeypatch) -> None:
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    first = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    session_id = first.remember("first", "first-token", now=now)
    second = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    daily = tmp_path / "memory" / "2026-08-01.md"

    rewrite_started = Event()
    release_rewrite = Event()
    append_attempted = Event()
    append_finished = Event()
    original_atomic_write = writer_module._atomic_write

    def blocked_atomic_write(path, text):
        rewrite_started.set()
        assert release_rewrite.wait(timeout=2)
        return original_atomic_write(path, text)

    def append_from_second() -> None:
        append_attempted.set()
        second.writer.append(
            daily,
            '## 13:00 — second <!-- thyca {"id":"bbbbbbbb","imp":3} -->\n'
            "- second-token\n",
        )
        append_finished.set()

    monkeypatch.setattr(writer_module, "_atomic_write", blocked_atomic_write)
    updater = Thread(
        target=lambda: first.writer.update_session(
            session_id, body_lines=["- updated-token"]
        )
    )
    updater.start()
    assert rewrite_started.wait(timeout=2)

    appender = Thread(target=append_from_second)
    appender.start()
    assert append_attempted.wait(timeout=2)
    assert not append_finished.is_set()

    release_rewrite.set()
    updater.join(timeout=2)
    appender.join(timeout=2)
    assert not updater.is_alive()
    assert not appender.is_alive()
    assert append_finished.is_set()
    text = daily.read_text(encoding="utf-8")
    assert "updated-token" in text
    assert "second-token" in text


def test_public_map_heading_serializes_concurrent_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = tmp_path / "memory" / "2026-08-01.md"
    daily.parent.mkdir()
    daily.write_text(
        '# 2026-08-01\n'
        '## 10:00 — first <!-- thyca {"id":"aaaaaaaa","imp":3} -->\n'
        "- original-token\n",
        encoding="utf-8",
    )
    writer = writer_module.MemoryWriter(tmp_path)
    rewrite_started = Event()
    release_rewrite = Event()
    append_started = Event()
    append_finished = Event()
    original_atomic_write = writer_module._atomic_write

    def blocked_atomic_write(path, text):
        rewrite_started.set()
        assert release_rewrite.wait(timeout=2)
        return original_atomic_write(path, text)

    errors: list[Exception] = []

    def map_heading() -> None:
        try:
            writer.map_heading(
                daily, "aaaaaaaa", lambda meta: replace(meta, title="updated")
            )
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    def append() -> None:
        append_started.set()
        try:
            writer.append(
                daily,
                '## 11:00 — second <!-- thyca {"id":"bbbbbbbb","imp":3} -->\n'
                "- appended-token\n",
            )
            append_finished.set()
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    monkeypatch.setattr(writer_module, "_atomic_write", blocked_atomic_write)
    mapper = Thread(target=map_heading)
    mapper.start()
    assert rewrite_started.wait(timeout=2)
    appender = Thread(target=append)
    appender.start()
    assert append_started.wait(timeout=2)
    assert not append_finished.is_set()
    release_rewrite.set()
    mapper.join(timeout=2)
    appender.join(timeout=2)

    assert not mapper.is_alive()
    assert not appender.is_alive()
    assert not errors
    text = daily.read_text(encoding="utf-8")
    assert "updated" in text
    assert "appended-token" in text


@pytest.mark.parametrize("kind", ["daily", "canonical"])
def test_two_facades_serialize_write_and_archive_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    if kind == "daily":
        target = tmp_path / "memory" / "2026-08-13.md"
        target.parent.mkdir()
        target.write_text(
            '# 2026-08-13\n## 10:00 — first <!-- thyca {"id":"aaaaaaaa","imp":3} -->\n'
            "- original-token\n",
            encoding="utf-8",
        )
    else:
        target = tmp_path / "SOUL.md"
        target.write_text("# Soul\noriginal-token\n", encoding="utf-8")

    first = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    second = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    first.archive.reindex(now)
    second.archive.reindex(now)

    refresh_started = Event()
    release_refresh = Event()
    original_replace = first.archive.store.replace_source

    def blocked_replace(path, source_kind, day, mtime_ns, size, chunks):
        if path == str(target):
            refresh_started.set()
            assert release_refresh.wait(timeout=2)
        return original_replace(path, source_kind, day, mtime_ns, size, chunks)

    monkeypatch.setattr(first.archive.store, "replace_source", blocked_replace)
    second_reindex_started = Event()
    original_reindex = second.archive.reindex

    def observed_reindex(now=None):
        second_reindex_started.set()
        return original_reindex(now)

    monkeypatch.setattr(second.archive, "reindex", observed_reindex)
    first_error: list[Exception] = []
    second_error: list[Exception] = []

    if kind == "daily":
        sid = "2026-08-13#aaaaaaaa"
        first_write = lambda: first.update(sid, summary="first-token", now=now)
        second_write = lambda: second.update(sid, summary="second-token", now=now)
    else:
        first_write = lambda: first.write_canonical("SOUL.md", "# Soul\nfirst-token\n")
        second_write = lambda: second.write_canonical("SOUL.md", "# Soul\nsecond-token\n")

    def run(operation, errors):
        try:
            operation()
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    first_thread = Thread(target=run, args=(first_write, first_error))
    first_thread.start()
    assert refresh_started.wait(timeout=2)

    second_thread = Thread(target=run, args=(second_write, second_error))
    second_thread.start()
    # A second facade must not mutate or refresh this source while the first
    # facade is between its source read and archive replacement.
    assert not second_reindex_started.wait(timeout=1)

    release_refresh.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not first_error
    assert not second_error
    assert second_reindex_started.is_set()
    assert first.search("second-token", now=now).hits
    assert second.search("second-token", now=now).hits


def test_search_does_not_refresh_and_forget_deletes(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
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
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("x", "ttl-purge-token-zzzx", importance=1, now=t0)
    daily = tmp_path / "memory" / "2026-08-01.md"
    assert "ttl-purge-token-zzzx" in daily.read_text(encoding="utf-8")
    assert TTL_DAYS[5] == 180
    facade._refresh_index(t0 + timedelta(days=4))
    assert "ttl-purge-token-zzzx" not in daily.read_text(encoding="utf-8")
    assert facade.search("ttl-purge-token-zzzx", now=t0 + timedelta(days=4)).hits == []


def test_legacy_memory_selectors_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text("# Soul\n", encoding="utf-8")
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    for action in (
        lambda: facade.get(session_id="memory#abcdef12"),
        lambda: facade.get(
            session_id="memory#abcdef12", path=str(tmp_path / "SOUL.md")
        ),
        lambda: facade.forget("memory#abcdef12"),
        lambda: facade.reinforce("memory#abcdef12"),
        lambda: facade.update("memory#abcdef12", topic="nope"),
    ):
        try:
            action()
        except Exception as exc:
            assert "MEMORY.md" in str(exc)
        else:
            raise AssertionError("expected legacy MEMORY.md selector rejection")


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


def test_proj_survives_update_and_reinforce_and_filters_search(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    later = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    proj = "/home/flowerf/Projects/thyca-ai"
    sid = facade.remember(
        "link", "zzzxuniqueaaa", now=t0, proj=proj, chat="2026-09-16T14-39-01_a1b2"
    )
    other = facade.remember("other", "zzzyuniquebbb", now=t0, proj="/tmp/other")
    facade.archive.reindex(later)
    linked = facade.search("zzzxuniqueaaa", now=later, proj=proj)
    assert [hit.session_id for hit in linked.hits] == [sid]
    assert facade.search("zzzyuniquebbb", now=later, proj=proj).hits == []
    unfiltered = facade.search("zzzyuniquebbb", now=later)
    assert [hit.session_id for hit in unfiltered.hits] == [other]

    facade.update(sid, topic="link mới", now=later)
    facade.reinforce(sid, now=later)
    daily = tmp_path / "memory" / "2026-08-13.md"
    meta = next(m for line in daily.read_text(encoding="utf-8").splitlines() if (m := parse_heading(line)) and m.title.startswith("link"))
    assert meta.proj == proj
    assert meta.chat == "2026-09-16T14-39-01_a1b2"
    assert meta.title == "link mới"

    with pytest.raises(ValueError, match="absolute"):
        facade.remember("x", "y", now=t0, proj="relative/path")
