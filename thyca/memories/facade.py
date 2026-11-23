"""Memory facade: remember / forget / reinforce / get."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from thyca.configs import atomic_write_text
from thyca.memories.active import ActiveMemory
from thyca.memories.archive_store import DATE_RE, decode_bots, encode_bots
from thyca.memories.archived import (
    GET_MEMORY_CAP,
    NO_FALLBACK_PREFIXES,
    ArchivedMemory,
    ArchiveError,
    Hit,
    SearchResult,
)
from thyca.memories.chunk import Chunk
from thyca.memories.fallback import read_unindexed_chunk
from thyca.memories.habits import strip_auto_habits
from thyca.memories.heading import (
    DEFAULT_IMPORTANCE,
    TASK_STATUSES,
    TTL_DAYS,
    HeadingMeta,
    expiry_ts,
    format_body,
    format_ts,
    is_visible,
    memory_id,
    new_entry_id,
    new_task_id,
    parse_heading,
    read_text_file,
    render_heading,
    utc_now,
    validate_task_meta,
)
from thyca.memories.identity import current_bot_id
from thyca.memories.ledgers import (
    append_other_journal as _append_other_journal,
)
from thyca.memories.ledgers import (
    extract_task_updated_day,
    first_summary_line,
    format_task_catalog,  # noqa: F401 -- reason: re-exported
    locate_visible_block,
    resolve_owners,
    validate_entry_fields,
)
from thyca.memories.ledgers import (
    record_habit_entry as _record_habit_entry,
)
from thyca.memories.ledgers import (
    today_entries as _today_entries,
)
from thyca.memories.ledgers import (
    upsert_task_card as _upsert_task_card,
)
from thyca.memories.search import _absolute_proj, lexical_search
from thyca.memories.search import recent as _recent_search
from thyca.memories.stats import CanonicalFile, MemoryStatsResult, build_stats
from thyca.memories.visibility import current_memory_view
from thyca.memories.writer import MemoryWriter


class MemoryFacade:
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
        importance: int = DEFAULT_IMPORTANCE,
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
        validate_entry_fields(topic, summary, content, importance)
        validate_task_meta(task, cont, kind, status, habit, habit_explicit)
        if isinstance(habit, str):
            habit = " ".join(habit.split())
        normalized_proj = _absolute_proj(proj)
        owners = resolve_owners(bots, current_bot_id())
        with self.writer.mutation_lock():
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
                importance=importance,
                expires_at=expiry_ts(importance, moment),
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
        """Group visible task entries into a task catalog (phase 1A).

        Returns ``{"tasks": [...], "warnings": [...]}``. A child bot
        only sees entries it may read, so a task made solely of another
        bot's entries never appears for it."""

        warnings: list[str] = []
        if status is not None and status not in TASK_STATUSES:
            raise ValueError(
                f"status must be one of {list(TASK_STATUSES)}, got {status!r}"
            )
        for name, value in (("date_from", date_from), ("date_to", date_to)):
            if value is not None and (
                not isinstance(value, str) or DATE_RE.fullmatch(value) is None
            ):
                raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}")
        requested_limit = limit
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 50))
        if limit != requested_limit:
            warnings.append(f"limit clamped from {requested_limit} to {limit}")
        self._ensure_rollover_indexed(now)
        now_ts = format_ts(utc_now(now))
        reader = current_bot_id()
        rows = self.archive.store.task_memory_rows(now_ts, reader=reader)
        members_by_session: dict[str, dict] = {}
        for row in rows:
            keys = row.keys()
            origin = row["chat"] if "chat" in keys else None
            if not self._entry_readable(
                    origin, row["bots"],
                    allow_own_cross_window=allow_own_cross_window):
                continue
            row_text = str(row["text_raw"] or "") if "text_raw" in keys else None
            updated_day = extract_task_updated_day(row_text)
            row_last_day = (
                max(str(row["last_day"]), updated_day)
                if updated_day
                else str(row["last_day"])
            )
            member = {
                "task": str(row["task"]),
                "memory_id": str(row["memory_id"]),
                "first_day": str(row["first_day"]),
                "last_day": row_last_day,
                "first_line": int(row["first_line"]),
                "last_line": int(row["last_line"]),
                "title": str(row["title"]),
                "status": row["status"],
                "bots": row["bots"],
                "chat": origin,
            }
            if row_text is not None:
                member["summary"] = first_summary_line(row_text)
            members_by_session[str(row["memory_id"])] = member
        today = self.archive.day(now)
        try:
            today_text = read_text_file(self.thyca_dir / "memory" / f"{today}.md")
        except (OSError, UnicodeDecodeError):
            today_text = None
        if today_text:
            today_chunks = self.archive.chunker.chunk_markdown(
                str(self.thyca_dir / "memory" / f"{today}.md"),
                today_text,
                source_kind="daily",
                timeline_day=today,
            )
            by_session: dict[str, list] = {}
            for chunk in today_chunks:
                if chunk.task is None or chunk.memory_id in members_by_session:
                    continue
                if not self._entry_readable(
                        getattr(chunk, "chat_session", None),
                        encode_bots(chunk.bots),
                        allow_own_cross_window=allow_own_cross_window):
                    continue
                by_session.setdefault(chunk.memory_id, []).append(chunk)
            for memory_id, leaves in by_session.items():
                first = leaves[0]
                if not is_visible(first.expires_at, now):
                    continue
                owners = first.bots
                if (
                    reader is not None
                    and reader != "thyca"
                    and owners is not None
                    and "team" not in owners
                    and reader not in owners
                ):
                    continue
                today_body = "\n".join(leaf.text_raw for leaf in leaves)
                updated_day = extract_task_updated_day(today_body)
                member_last_day = max(today, updated_day) if updated_day else today
                members_by_session[memory_id] = {
                    "task": str(first.task),
                    "memory_id": memory_id,
                    "first_day": today,
                    "last_day": member_last_day,
                    "first_line": min(leaf.line_start for leaf in leaves),
                    "last_line": max(leaf.line_start for leaf in leaves),
                    "title": first.memory_title,
                    "status": first.status,
                    "bots": encode_bots(first.bots),
                    "chat": getattr(first, "chat_session", None),
                    "summary": first_summary_line(first.text_raw),
                }
        grouped: dict[str, list] = {}
        for member in members_by_session.values():
            grouped.setdefault(member["task"], []).append(member)
        catalog = []
        for task_id, members in grouped.items():
            ordered = sorted(
                members,
                key=lambda r: (str(r["first_day"]), int(r["first_line"])),
            )
            latest = max(
                members,
                key=lambda r: (str(r["last_day"]), int(r["last_line"])),
            )
            latest_status = "open"
            latest_memory_id = str(latest["memory_id"])
            for member in sorted(
                members,
                key=lambda r: (str(r["last_day"]), int(r["last_line"])),
                reverse=True,
            ):
                candidate = member["status"]
                if isinstance(candidate, str) and candidate:
                    latest_status = candidate
                    break
            if status is not None and latest_status != status:
                continue
            first_day = str(ordered[0]["first_day"])
            last_day = str(latest["last_day"])
            if date_from is not None and last_day < date_from:
                continue
            if date_to is not None and first_day > date_to:
                continue
            owners: set[str] = set()
            for member in members:
                decoded = decode_bots(member["bots"])
                if decoded:
                    owners.update(decoded)
                else:
                    owners.add("team")
            latest_summary = latest.get("summary")
            if latest_summary is None:
                mem_rows = self.archive.store.get_memory(latest_memory_id, now_ts)
                latest_summary = (
                    first_summary_line(str(mem_rows[0]["text_raw"]))
                    if mem_rows
                    else ""
                )
            catalog.append(
                {
                    "task_id": task_id,
                    "title": str(ordered[0]["title"]),
                    "status": latest_status,
                    "latest_memory_id": latest_memory_id,
                    "entry_count": len(members),
                    "first_day": first_day,
                    "last_day": last_day,
                    "bots": sorted(owners),
                    "chat": latest.get("chat"),
                    "summary": latest_summary,
                }
            )
        catalog.sort(key=lambda item: item["last_day"], reverse=True)
        return {"tasks": catalog[:limit], "warnings": warnings}

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
        importance: int = DEFAULT_IMPORTANCE,
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
        importance: int = DEFAULT_IMPORTANCE,
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
        """Append a non-task/non-habit fact to today's scoped journal or skip dupes."""
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
        found = locate_visible_block(self, memory_id_str, now)
        return found[0] if found is not None else None

    def forget(self, memory_id: str, now: datetime | None = None) -> None:
        self._check_mutate(memory_id, "forget", now)
        with self.writer.mutation_lock():
            self.writer.forget(memory_id, now)
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
            self.writer.update_memory(
                memory_id, topic=topic, body_lines=body_lines,
                proj=normalized_proj, keep_details=keep_details,
                task=task, cont=cont, kind=kind, status=status,
                habit=habit.strip() if habit is not None else None,
                habit_explicit=habit_explicit or None,
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
        provided = [
            (name, value)
            for name, value in (
                ("chunk_id", chunk_id),
                ("memory_id", memory_id),
            )
            if value is not None
        ]
        if len(provided) != 1:
            raise ArchiveError("exactly one of chunk_id, memory_id is required")
        name, value = provided[0]
        if not isinstance(value, str) or not value.strip():
            raise ArchiveError(f"{name} must be a non-empty string")
        now_ts = format_ts(utc_now(now))
        try:
            text = self.archive.get(chunk_id=chunk_id, memory_id=memory_id, now=now)
            if chunk_id is not None:
                sid = self.archive.lookup_memory_id(chunk_id, now)
                chunk_ids = [chunk_id]
            else:
                sid = memory_id or ""
                rows = self.archive.store.get_memory(sid, now_ts)
                chunk_ids = [str(row["chunk_id"]) for row in rows[:GET_MEMORY_CAP]]
        except ArchiveError as original:
            if str(original).startswith(NO_FALLBACK_PREFIXES):
                raise
            if chunk_id is not None:
                text, sid = read_unindexed_chunk(
                    self.archive, self.writer, chunk_id, now, original
                )
                chunk_ids = [chunk_id]
            elif memory_id is None:
                raise
            else:
                text = self.writer.read_memory(memory_id, now=now)
                sid = memory_id
                chunk_ids = self._memory_leaf_ids(memory_id, text)[:GET_MEMORY_CAP]
        if not sid.startswith("canonical#"):
            self._check_read(sid, now)
            origin_chat = None
            try:
                origin_rows = self.archive.store.get_memory(sid, now_ts)
            except ArchiveError:
                origin_rows = []
            if origin_rows:
                keys = origin_rows[0].keys()
                if "chat_session" in keys:
                    origin_chat = origin_rows[0]["chat_session"]
            if origin_chat is None and isinstance(text, str):

                for line in text.splitlines():
                    if not line.startswith("## "):
                        break
                    meta = parse_heading(line)
                    if meta is not None:
                        origin_chat = meta.chat
                    break
            bots = self._lookup_bots(sid, now)
            if not self._origin_allowed(
                    origin_chat, bots,
                    allow_own_cross_window=allow_own_cross_window):
                raise ArchiveError("entry is not readable in this window")
        if chunk_ids and sid:
            self.archive.store.usage.record_gets(chunk_ids, sid, now_ts)
        if sid.startswith("canonical#"):

            _view = current_memory_view()
            if _view is not None and getattr(
                _view.window, "kind", None
            ) == "group" and isinstance(text, str):

                return strip_auto_habits(text)
            return text
        if self._may_mutate(sid, now):
            self.reinforce(sid, now=now)
            try:
                if chunk_id is not None:
                    return self.archive.get(chunk_id=chunk_id, now=now)
                return self.archive.get(memory_id=sid, now=now)
            except ArchiveError as reread_miss:
                if str(reread_miss).startswith(NO_FALLBACK_PREFIXES):
                    raise
                if chunk_id is not None:
                    leaf, _ = read_unindexed_chunk(
                        self.archive, self.writer, chunk_id, now, reread_miss
                    )
                    return leaf
                return self.writer.read_memory(sid, now=now)
        return text

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
            chat, bots, allow_own_cross_window=allow_own_cross_window)

    @staticmethod
    def _origin_allowed(
        chat: str | None, bots: tuple | None = None,
        allow_own_cross_window: bool = False,
    ) -> bool:

        view = current_memory_view()
        if view is None:
            return True
        return view.can_read_entry(
            chat, bots, allow_own_cross_window=allow_own_cross_window)

    def _ensure_rollover_indexed(self, now: datetime | None = None) -> None:
        today = self.archive.day(now)
        if today > self._indexed_day:
            with self.writer.mutation_lock():
                if today > self._indexed_day:
                    self.archive.reindex(now)
                    self._indexed_day = today

    def _refresh_index(self, now: datetime | None = None) -> None:
        with self.writer.mutation_lock():
            self.writer.purge_expired(utc_now(now))
            self.archive.reindex(now)
            self._indexed_day = self.archive.day(now)
            live = set(self.archive.store.chunk_ids())
            live.update(chunk.chunk_id for chunk in self._today_chunks(now))
            self.archive.store.usage.keep_gets(live)
            self.archive.store.usage.keep_searches(live)

    def forget_bot_entries(self, bot_id: str, now: datetime | None = None) -> int:
        """Drop one bot's private entries on bot deletion (BE-5).

        Only entries owned exactly by ``[bot_id]`` go; team, legacy, and
        group entries stay with the team. Edges of removed entries drop
        via the refresh reaper."""
        with self.writer.mutation_lock():
            removed = self.writer.remove_bot_entries(bot_id)
            self._refresh_index(now)
            return removed

    def _lookup_bots(
        self, memory_id: str, now: datetime | None
    ) -> tuple[str, ...] | None:
        """Owner list of one entry; None = legacy/team entry.

        Indexed rows first; today's not-yet-indexed file falls back to the
        raw heading. Unknown sessions read as None (callers that need the
        distinction check existence separately)."""
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
        """True when the in-flight caller may edit/delete/renew an entry.

        The user (no bot bound) and Thyca may touch everything. A child
        bot may only touch its own non-team entries."""
        reader = current_bot_id()
        if reader is None or reader == "thyca":
            return True
        bots = self._lookup_bots(memory_id, now)
        if bots is None:
            return False
        return reader in bots and "team" not in bots

    def _check_mutate(self, memory_id: str, op: str, now: datetime | None = None) -> None:
        if self._may_mutate(memory_id, now):
            return
        reader = current_bot_id()
        bots = self._lookup_bots(memory_id, now)
        kind = "team" if (bots is None or "team" in (bots or ())) else "another bot's"
        raise ArchiveError(f"bot '{reader}' cannot {op} {kind} memory: {memory_id}")

    def _check_read(self, memory_id: str, now: datetime | None = None) -> None:
        """Raise unless the in-flight caller may read an entry.

        Read is wider than mutate: a child bot reads its own entries plus
        team/legacy entries, never another bot's private entries."""
        reader = current_bot_id()
        if reader is None or reader == "thyca":
            return
        bots = self._lookup_bots(memory_id, now)
        if bots is None or reader in bots or "team" in bots:
            return
        raise ArchiveError(f"bot '{reader}' cannot read another bot's memory: {memory_id}")

    CANONICAL_NAMES = ("SOUL.md", "USER.md", "IDENTITY.md")

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
        files: list[CanonicalFile] = []
        for name in self.CANONICAL_NAMES:
            path = self.thyca_dir / name
            try:
                text = read_text_file(path)
            except OSError:
                text = None
            except UnicodeDecodeError as exc:
                raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
            files.append(CanonicalFile(name=name, content=text or ""))
        return files

    def _today_chunks(self, now: datetime | None) -> list[Chunk]:
        day = self.archive.day(now)
        path = self.thyca_dir / "memory" / f"{day}.md"
        try:
            text = read_text_file(path)
        except OSError:
            return []
        except UnicodeDecodeError as exc:
            raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
        if text is None:
            return []
        return self.archive.chunker.chunk_markdown(
            path, text, source_kind="daily", timeline_day=day
        )

    def _memory_leaf_ids(self, memory_id: str, text: str) -> list[str]:
        path, _ = self.writer.locate(memory_id)
        day = memory_id.split("#", 1)[0]
        chunks = self.archive.chunker.chunk_markdown(
            path, text, source_kind="daily", timeline_day=day
        )
        return [chunk.chunk_id for chunk in chunks if chunk.memory_id == memory_id]
