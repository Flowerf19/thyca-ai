"""Memory facade: remember / forget / reinforce / get / 3-ledger dispatch."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from thyca.configs import atomic_write_text
from thyca.memories.active import ActiveMemory
from thyca.memories.archive_store import decode_bots
from thyca.memories.archived import (
    ArchivedMemory,
    ArchiveError,
    Hit,
    SearchResult,
)
from thyca.memories.chunk import Chunk
from thyca.memories.habits import record_habit_entry as _record_habit_entry
from thyca.memories.heading import (
    DEFAULT_IMPORTANCE,
    HABIT_DEFAULT_IMPORTANCE,
    TASK_DEFAULT_IMPORTANCE,
    TTL_DAYS,
    HeadingMeta,
    expiry_ts,
    format_body,
    format_ts,
    memory_id,
    new_entry_id,
    new_task_id,
    parse_heading,
    render_heading,
    utc_now,
    validate_task_meta,
)
from thyca.memories.identity import current_bot_id
from thyca.memories.ledger_common import (
    locate_visible_block,
    resolve_owners,
    today_blocks,
    validate_entry_fields,
)
from thyca.memories.ledger_common import (
    today_entries as _today_entries,
)
from thyca.memories.other_ledger import append_other_journal as _append_other_journal
from thyca.memories.reader import (
    canonical_files as _canonical_files,
)
from thyca.memories.reader import (
    memory_leaf_ids as _memory_leaf_ids,
)
from thyca.memories.reader import (
    read_memory_entry,
)
from thyca.memories.reader import (
    today_chunks as _today_chunks,
)
from thyca.memories.search import _absolute_proj, lexical_search
from thyca.memories.search import recent as _recent_search
from thyca.memories.stats import CanonicalFile, MemoryStatsResult, build_stats
from thyca.memories.task_ledger import (
    build_task_catalog,
    check_task_close_allowed,
    format_task_catalog,  # noqa: F401 -- reason: re-exported
)
from thyca.memories.task_ledger import (
    upsert_task_card as _upsert_task_card,
)
from thyca.memories.visibility import current_memory_view
from thyca.memories.writer import MemoryWriter


class MemoryFacade:
    CANONICAL_NAMES = ("SOUL.md", "USER.md", "IDENTITY.md")

    def __init__(
        self,
        thyca_dir: Path | None = None,
        timezone_name: str | None = None,
        archive: ArchivedMemory | None = None,
        writer: MemoryWriter | None = None,
    ) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca")
        self.active = ActiveMemory(self.thyca_dir, timezone_name=timezone_name)
        self.archive = archive or ArchivedMemory(self.thyca_dir, timezone_name=timezone_name)
        self.writer = writer or MemoryWriter(self.thyca_dir)
        with self.writer.mutation_lock():
            self.archive.store.drop_source(str(self.thyca_dir / "MEMORY.md"))
            self.archive.reindex()
        self._indexed_day: str = self.archive.day()

    def remember(
        self,
        topic: str,
        summary: str,
        content: str = "",
        importance: int | None = None,
        now: datetime | None = None,
        proj: str | None = None,
        chat: str | None = None,
        bots: list[str] | tuple[str, ...] | None = None,
        task: str | None = None,
        cont: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        habit: str | None = None,
        habit_explicit: bool = False,
    ) -> str:
        if importance is None:
            if task is not None:
                effective_importance = TASK_DEFAULT_IMPORTANCE
            elif kind == "habit":
                effective_importance = HABIT_DEFAULT_IMPORTANCE
            else:
                effective_importance = DEFAULT_IMPORTANCE
        else:
            effective_importance = importance
        validate_entry_fields(topic, summary, content, effective_importance)
        validate_task_meta(task, cont, kind, status, habit, habit_explicit)
        if isinstance(habit, str):
            habit = " ".join(habit.split())
        normalized_proj = _absolute_proj(proj)
        owners = resolve_owners(bots, current_bot_id())
        with self.writer.mutation_lock():
            if status in ("done", "cancelled") and task is not None:
                check_task_close_allowed(self, task, now)
            self.active.ensure_files(now)
            moment = utc_now(now)
            entry = new_entry_id()
            day = self.archive.day(now)
            path = self.thyca_dir / "memory" / f"{day}.md"
            sid = memory_id(day, entry)
            hour = moment.astimezone(self.archive.zone()).strftime("%H:%M")
            meta = HeadingMeta(
                time=hour,
                title=topic,
                entry_id=entry,
                importance=effective_importance,
                expires_at=expiry_ts(effective_importance, moment),
                proj=normalized_proj,
                chat=chat if isinstance(chat, str) and chat.strip() else None,
                bots=owners,
                task=task,
                cont=cont,
                kind=kind,
                status=status,
                habit=habit.strip() if isinstance(habit, str) else None,
                habit_explicit=bool(habit_explicit),
            )
            leaf = "\n".join(format_body(summary, content))
            with self.writer.lock_for(path):
                if not path.is_file():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"# {path.stem}\n", encoding="utf-8")
                self.writer.append(path, render_heading(meta) + leaf + "\n")
                self._refresh_index(now)
            return sid

    def new_task_id(self, tries: int = 8) -> str:
        """Mint a task id not already present in the store (phase 1A)."""
        for _ in range(max(1, tries)):
            candidate = new_task_id()
            if not self.archive.store.has_task(candidate):
                return candidate
        raise ArchiveError("could not mint a unique task id")

    def tasks(
        self,
        *,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
        now: datetime | None = None,
        allow_own_cross_window: bool = False,
    ) -> dict:
        """Group visible task entries into a task catalog (phase 1A)."""
        return build_task_catalog(
            self,
            status=status,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            now=now,
            allow_own_cross_window=allow_own_cross_window,
        )

    def today_entries(
        self,
        *,
        now: datetime | None = None,
        chat: str | None = None,
        reader: str | None = None,
    ) -> list[dict]:
        """Visible entries written today for prompt/delta inspection."""
        return _today_entries(self, now=now, chat=chat, reader=reader)

    def upsert_task_card(
        self,
        topic: str,
        summary: str,
        content: str = "",
        *,
        task: str,
        status: str | None = None,
        importance: int | None = None,
        bots: list[str] | tuple[str, ...] | None = None,
        chat: str | None = None,
        proj: str | None = None,
        now: datetime | None = None,
        target_memory_id: str | None = None,
    ) -> str:
        """Update an existing task card in-place or create a new checkpoint card."""
        return _upsert_task_card(
            self,
            topic,
            summary,
            content,
            task=task,
            status=status,
            importance=importance,
            bots=bots,
            chat=chat,
            proj=proj,
            now=now,
            target_memory_id=target_memory_id,
        )

    def record_habit_entry(
        self,
        topic: str,
        summary: str,
        content: str = "",
        *,
        habit: str,
        habit_explicit: bool = False,
        importance: int | None = None,
        bots: list[str] | tuple[str, ...] | None = None,
        chat: str | None = None,
        proj: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Record a habit observation or reinforce an existing habit across days."""
        return _record_habit_entry(
            self,
            topic,
            summary,
            content,
            habit=habit,
            habit_explicit=habit_explicit,
            importance=importance,
            bots=bots,
            chat=chat,
            proj=proj,
            now=now,
        )

    def append_other_journal(
        self,
        topic: str,
        summary: str,
        content: str = "",
        *,
        importance: int = DEFAULT_IMPORTANCE,
        bots: list[str] | tuple[str, ...] | None = None,
        chat: str | None = None,
        proj: str | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """Append a non-task/non-habit fact to today's topic-scoped block or skip dupes."""
        return _append_other_journal(
            self,
            topic,
            summary,
            content,
            importance=importance,
            bots=bots,
            chat=chat,
            proj=proj,
            now=now,
        )

    def _locate_visible_block(
        self, memory_id_str: str | None, now: datetime | None
    ) -> tuple[HeadingMeta, list[str]] | None:
        return locate_visible_block(self, memory_id_str, now)

    def _locate_visible_meta(
        self, memory_id_str: str | None, now: datetime | None
    ) -> HeadingMeta | None:
        located = locate_visible_block(self, memory_id_str, now)
        return located[0] if located is not None else None

    def forget(self, memory_id: str, now: datetime | None = None) -> None:
        self._check_mutate(memory_id, "forget", now)
        with self.writer.mutation_lock():
            self.writer.forget(memory_id)
            self._refresh_index(now)

    def update(
        self,
        memory_id: str,
        *,
        topic: str | None = None,
        summary: str | None = None,
        content: str | None = None,
        now: datetime | None = None,
        proj: str | None = None,
        task: str | None = None,
        cont: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        habit: str | None = None,
        habit_explicit: bool = False,
    ) -> None:
        normalized_proj = _absolute_proj(proj)
        for name, value in (("topic", topic), ("summary", summary), ("content", content)):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        if topic is not None and not topic.strip():
            raise ValueError("topic must not be blank")
        if summary is not None and not summary.strip():
            raise ValueError("summary must not be blank")
        if content is not None and summary is None:
            raise ValueError("content requires summary")
        for name, value in (("task", task), ("cont", cont), ("kind", kind), ("status", status)):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string")
        if habit is not None and (not isinstance(habit, str) or not habit.strip()):
            raise ValueError("habit must be a non-empty string")
        if isinstance(habit, str):
            habit = " ".join(habit.split())
        if habit_explicit and not isinstance(habit_explicit, bool):
            raise ValueError("habit_explicit must be a boolean")
        if (
            topic is None and summary is None and content is None
            and normalized_proj is None and task is None and cont is None
            and kind is None and status is None and habit is None
            and not habit_explicit
        ):
            raise ValueError("nothing to update")
        topic = topic.strip() if topic is not None else None
        body_lines = None
        keep_details = False
        if summary is not None:
            if content is None:
                keep_details = True
                body_lines = format_body(summary)
            else:
                body_lines = format_body(summary, content)
        self._check_mutate(memory_id, "update", now)
        with self.writer.mutation_lock():
            if status in ("done", "cancelled"):
                effective_task = task or self._lookup_task(memory_id, now)
                if effective_task is not None:
                    check_task_close_allowed(self, effective_task, now)
            self.writer.update_memory(
                memory_id,
                topic=topic,
                body_lines=body_lines,
                proj=normalized_proj,
                keep_details=keep_details,
                task=task,
                cont=cont,
                kind=kind,
                status=status,
                habit=habit.strip() if isinstance(habit, str) else None,
                habit_explicit=habit_explicit or None,
                task_updated_day=self.archive.day(now),
                reinforce_task_now=utc_now(now),
            )
            self._refresh_index(now)

    def reinforce(
        self,
        memory_id: str,
        importance: int | None = None,
        now: datetime | None = None,
    ) -> str:
        if importance is not None and importance not in TTL_DAYS:
            raise ValueError(f"importance must be 1..5, got {importance}")
        self._check_mutate(memory_id, "reinforce", now)
        with self.writer.mutation_lock():
            exp = self.writer.reinforce(memory_id, importance, now)
            self._refresh_index(now)
            return exp

    def get(
        self,
        *,
        chunk_id: str | None = None,
        memory_id: str | None = None,
        now: datetime | None = None,
        allow_own_cross_window: bool = False,
    ) -> str:
        return read_memory_entry(
            self,
            chunk_id=chunk_id,
            memory_id=memory_id,
            now=now,
            allow_own_cross_window=allow_own_cross_window,
        )

    def stats(
        self, now: datetime | None = None, *, bot_id: str | None = None,
    ) -> MemoryStatsResult:
        self._ensure_rollover_indexed(now)
        return build_stats(
            self.archive,
            today_chunks=self._today_chunks(now),
            files=self._canonical_files() if bot_id is None else [],
            now=now,
            bot_id=bot_id,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        timeline_day: str | None = None,
        now: datetime | None = None,
        proj: str | None = None,
        chat: str | None = None,
        task: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        allow_own_cross_window: bool = False,
    ) -> SearchResult:
        self._ensure_rollover_indexed(now)
        return lexical_search(
            self.archive,
            query,
            limit=limit,
            timeline_day=timeline_day,
            now=now,
            proj=proj,
            chat=chat,
            reader=current_bot_id(),
            task=task,
            kind=kind,
            status=status,
            allow_own_cross_window=allow_own_cross_window,
        )

    def recent(
        self, limit: int = 5, now: datetime | None = None,
        allow_own_cross_window: bool = False,
    ) -> list[Hit]:
        self._ensure_rollover_indexed(now)
        return _recent_search(
            self.archive, limit, now, reader=current_bot_id(),
            allow_own_cross_window=allow_own_cross_window,
        )

    @staticmethod
    def _entry_readable(
        chat: str | None, bots,
        allow_own_cross_window: bool = False,
    ) -> bool:
        view = current_memory_view()
        if view is None:
            return True
        if bots is not None and not isinstance(bots, tuple):
            try:
                bots = decode_bots(bots)
            except Exception:
                bots = None
        return view.can_read_entry(
            chat, bots, allow_own_cross_window=allow_own_cross_window
        )

    @staticmethod
    def _origin_allowed(
        chat: str | None, bots: tuple | None = None,
        allow_own_cross_window: bool = False,
    ) -> bool:
        view = current_memory_view()
        if view is None:
            return True
        return view.can_read_entry(
            chat, bots, allow_own_cross_window=allow_own_cross_window
        )

    def _ensure_rollover_indexed(self, now: datetime | None = None) -> None:
        today = self.archive.day(now)
        if today > self._indexed_day:
            with self.writer.mutation_lock():
                if today > self._indexed_day:
                    self.archive.reindex(now)
                    self._indexed_day = today

    def _refresh_index(self, now: datetime | None = None) -> None:
        with self.writer.mutation_lock():
            today = self.archive.day(now)
            self.writer.purge_expired(utc_now(now), keep_day=today)
            self.archive.reindex(now)
            self._indexed_day = today
            live = set(self.archive.store.chunk_ids())
            live.update(chunk.chunk_id for chunk in self._today_chunks(now))
            self.archive.store.usage.keep_gets(live)
            self.archive.store.usage.keep_searches(live)

    def forget_bot_entries(self, bot_id: str, now: datetime | None = None) -> int:
        """Drop one bot's private entries on bot deletion (BE-5)."""
        with self.writer.mutation_lock():
            removed = self.writer.remove_bot_entries(bot_id)
            self._refresh_index(now)
            return removed

    def _lookup_bots(
        self, memory_id: str, now: datetime | None
    ) -> tuple[str, ...] | None:
        bots = self.archive.store.memory_bots(memory_id)
        if bots is not None:
            return bots
        if self.archive.store.get_memory_any(memory_id):
            return None
        try:
            text = self.writer.read_memory(memory_id, now=now)
        except ArchiveError:
            return None
        first = text.splitlines()[0] if text.splitlines() else ""
        meta = parse_heading(first)
        return meta.bots if meta is not None else None

    def _may_mutate(self, memory_id: str, now: datetime | None = None) -> bool:
        reader = current_bot_id()
        if reader is None or reader == "thyca":
            return True
        bots = self._lookup_bots(memory_id, now)
        if bots is None:
            return False
        return reader in bots and "team" not in bots

    def _lookup_task(self, sid: str, now: datetime | None) -> str | None:
        now_ts = format_ts(utc_now(now))
        try:
            rows = self.archive.store.get_memory(sid, now_ts)
        except ArchiveError:
            rows = []
        if rows:
            keys = rows[0].keys()
            raw = rows[0]["task"] if "task" in keys else None
            return str(raw) if isinstance(raw, str) and raw else None
        today_str, _path, _lines, blocks = today_blocks(self, now)
        for meta, resolved, _start, _end in blocks:
            if memory_id(today_str, resolved) == sid:
                return meta.task
        return None

    def _check_mutate(self, memory_id: str, op: str, now: datetime | None = None) -> None:
        if self._may_mutate(memory_id, now):
            return
        reader = current_bot_id()
        bots = self._lookup_bots(memory_id, now)
        kind = "team" if (bots is None or "team" in (bots or ())) else "another bot's"
        raise ArchiveError(f"bot '{reader}' cannot {op} {kind} memory: {memory_id}")

    def _check_read(self, memory_id: str, now: datetime | None = None) -> None:
        reader = current_bot_id()
        if reader is None or reader == "thyca":
            return
        bots = self._lookup_bots(memory_id, now)
        if bots is None or reader in bots or "team" in bots:
            return
        raise ArchiveError(f"bot '{reader}' cannot read another bot's memory: {memory_id}")

    def write_canonical(self, name: str, content: str) -> None:
        """Ghi đè file canonical (SOUL/USER/IDENTITY.md). Chỉ whitelist, atomic."""
        if name not in self.CANONICAL_NAMES:
            raise ArchiveError(f"unknown canonical file: {name}")
        path = self.thyca_dir / name
        if path.is_symlink():
            raise ArchiveError("refusing to write symlink")
        text = str(content).replace("\r\n", "\n")
        if text and not text.endswith("\n"):
            text += "\n"
        try:
            with self.writer.mutation_lock(), self.writer.lock_for(path):
                atomic_write_text(path, text)
        except OSError as exc:
            raise ArchiveError(f"write failed: {name}") from exc
        with self.writer.mutation_lock():
            self._refresh_index()

    def _canonical_files(self) -> list[CanonicalFile]:
        return _canonical_files(self)

    def _today_chunks(self, now: datetime | None) -> list[Chunk]:
        return _today_chunks(self, now)

    def _memory_leaf_ids(self, memory_id: str, text: str) -> list[str]:
        return _memory_leaf_ids(self, memory_id, text)
