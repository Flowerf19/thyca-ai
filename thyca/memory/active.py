"""Active memory: files currently injected into the system prompt."""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from thyca.config import DEFAULT_LIMITS_HOT_TAIL_KB, DEFAULT_TIMELINE_TIMEZONE
from thyca.memory.heading import (
    day,
    is_session_heading,
    iter_session_blocks,
    read_text_file,
    session_id,
    strip_comment,
    strip_heading_comments,
)

if TYPE_CHECKING:
    from thyca.skills import SkillStore

_FENCE_RE = re.compile(r"^```", re.MULTILINE)


def _packaged(name: str, fallback: str) -> str:
    # PromptManager.template is the sole prompt loader; Active keeps only
    # the missing-file fallback. Local import: llm.prompt_manager imports
    # this module for ActiveSnapshot, so a top-level import would cycle.
    from thyca.llm.prompt_manager import PromptManager

    try:
        return PromptManager().template(name)
    except FileNotFoundError:
        # Missing file only: corruption/permission bugs must stay loud.
        return fallback


def _default_files() -> dict[str, str]:
    return {
        "SOUL.md": _packaged("soul", "# Soul\n"),
        "IDENTITY.md": _packaged("identity", "# Identity\n"),
        "USER.md": _packaged("user", "# User\n"),
    }


class ActiveMemoryError(RuntimeError):
    """Active memory file or directory could not be prepared or read."""


@dataclass
class ActiveState:
    day: str
    today_path: Path


@dataclass(frozen=True)
class ActiveSnapshot:
    soul: str
    user: str
    today: str
    identity: str = ""
    skills: str = ""
    today_elsewhere: str = ""


class ActiveMemory:
    """Read-only window over the markdown files in the current prompt."""

    def __init__(
        self,
        thyca_dir: Path | None = None,
        tail_kb: int | None = None,
        timezone_name: str | None = None,
        on_day_close: Callable[[str], None] | None = None,
        skills_store: SkillStore | None = None,
    ) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca")
        self.tail_kb = DEFAULT_LIMITS_HOT_TAIL_KB if tail_kb is None else tail_kb
        self.timezone_name = timezone_name or DEFAULT_TIMELINE_TIMEZONE
        self.on_day_close = on_day_close
        if skills_store is not None:
            self._skills = skills_store
        else:
            from thyca.skills import SkillStore

            self._skills = SkillStore(self.thyca_dir)

    @property
    def skills_store(self) -> SkillStore:
        return self._skills

    @property
    def memory_dir(self) -> Path:
        return self.thyca_dir / "memory"

    @property
    def _budget(self) -> int:
        return self.tail_kb * 1024

    def ensure_files(self, now: datetime | None = None) -> None:
        self._secure_dir(self.thyca_dir)
        self._secure_dir(self.memory_dir)
        for name, template in _default_files().items():
            self._create_if_missing(self.thyca_dir / name, template)
        day = self._day(now or self._now())
        self._create_if_missing(self._daily_path(day), f"# {day}\n")
        self._skills.ensure_defaults()

    def open_session(self, now: datetime) -> ActiveState:
        self.ensure_files(now)
        day = self._day(now)
        return ActiveState(day=day, today_path=self._daily_path(day))

    def refresh(
        self, state: ActiveState, now: datetime, session_id: str | None = None
    ) -> ActiveSnapshot:
        today = self._day(now)
        if today != state.day:
            closed = state.day
            state.day = today
            state.today_path = self._daily_path(today)
            self._create_if_missing(state.today_path, f"# {today}\n")
            if self.on_day_close is not None:
                self.on_day_close(closed)
        elif not state.today_path.exists():
            # A today file deleted mid-day comes back instead of silently
            # reading as "" until rollover.
            self._secure_dir(self.memory_dir)
            self._create_if_missing(state.today_path, f"# {today}\n")
        if session_id is None:
            today_text, elsewhere_text = self._tail(self._read(state.today_path)), ""
        else:
            # Grouping needs the raw metadata comments; strip them from the
            # injected same-session text afterwards, like the legacy path.
            here_raw, elsewhere_text = split_today_by_session(
                self._read_raw(state.today_path),
                state.day,
                str(state.today_path),
                session_id,
            )
            today_text = self._tail(strip_heading_comments(here_raw))
        return ActiveSnapshot(
            soul=self._read(self.thyca_dir / "SOUL.md"),
            identity=self._read(self.thyca_dir / "IDENTITY.md"),
            user=self._read(self.thyca_dir / "USER.md"),
            today=today_text,
            skills=self._skills.index_text(),
            today_elsewhere=elsewhere_text,
        )

    def _now(self) -> datetime:
        return datetime.now(self._zone())

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)

    def _day(self, now: datetime) -> str:
        return day(now, self._zone())

    def _daily_path(self, day: str) -> Path:
        return self.memory_dir / f"{day}.md"

    def _secure_dir(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)
        except OSError as exc:
            raise ActiveMemoryError(f"cannot secure {path}: {exc}") from exc

    def _create_if_missing(self, path: Path, template: str) -> None:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        except OSError as exc:
            raise ActiveMemoryError(f"cannot create {path}: {exc}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(template)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise ActiveMemoryError(f"cannot write {path}: {exc}") from exc
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _read_raw(self, path: Path) -> str:
        try:
            text = read_text_file(path)
        except (OSError, UnicodeDecodeError) as exc:
            raise ActiveMemoryError(f"cannot read {path}: {exc}") from exc
        if text is None:
            return ""
        return text

    def _read(self, path: Path) -> str:
        return strip_heading_comments(self._read_raw(path))

    def _tail(self, text: str) -> str:
        return tail_text(text, self._budget)


ELSEWHERE_MAX_LINES = 25


def split_today_by_session(
    text: str, day_str: str, path: str, chat_id: str
) -> tuple[str, str]:
    """Split today's file into (this-session raw text, other-session index).

    Same-session blocks keep full text with a pullable ``[day#id]`` suffix
    on each heading (the metadata comment is replaced, not kept: the prompt
    needs the id for memory_get/update/reinforce/forget, not the raw JSON).
    The caller applies the byte budget, like the legacy path. Other blocks — and
    unattributed ones, which cannot prove same-session — shrink to one
    pullable index line each, so foreign topics can neither dominate
    attention nor evict this session's own notes. ``path`` must be the same
    absolute daily-file path the writer scans, otherwise legacy (comment-
    less) entry ids in the index will not resolve via ``memory_get``.
    Free text before the first session heading (day title, hand-written
    notes) has no owner, so it joins the here portion; the caller still
    applies the tail budget. When the index exceeds ELSEWHERE_MAX_LINES,
    the oldest lines are dropped and a trailing marker records the count.
    """
    lines = text.splitlines(keepends=True)
    here: list[str] = []
    elsewhere: list[str] = []
    first_start: int | None = None
    for meta, entry_id, start, end in iter_session_blocks(lines, path):
        if first_start is None:
            first_start = start
        if meta.chat == chat_id:
            here.append(_here_block(lines, start, end, day_str, entry_id))
        else:
            elsewhere.append(
                f"- {meta.time} — {meta.title} [{session_id(day_str, entry_id)}]"
            )
    prefix = "".join(lines) if first_start is None else "".join(lines[:first_start])
    here_text = prefix + "".join(here)
    if len(elsewhere) > ELSEWHERE_MAX_LINES:
        omitted = len(elsewhere) - ELSEWHERE_MAX_LINES
        kept = elsewhere[-ELSEWHERE_MAX_LINES:]
        kept.append(f"[... {omitted} older lines omitted ...]")
        return here_text, "\n".join(kept)
    return here_text, "\n".join(elsewhere[-ELSEWHERE_MAX_LINES:])


def _here_block(
    lines: list[str], start: int, end: int, day_str: str, entry_id: str
) -> str:
    """One same-session block with ``[day#id]`` on its heading."""
    head = strip_comment(lines[start])
    ending = lines[start][len(lines[start].rstrip("\r\n")):]
    first = f"{head} [{session_id(day_str, entry_id)}]{ending}"
    return first + "".join(lines[start + 1 : end])


def tail_text(text: str, budget_bytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= budget_bytes:
        return text
    start = len(raw) - budget_bytes
    while start < len(raw) and raw[start] & 0xC0 == 0x80:
        start += 1
    index = len(raw[:start].decode("utf-8"))
    fence = _fence_start(text, index)
    if fence is not None:
        index = fence
    heading = _last_heading_at_or_before(text, index)
    if heading is not None:
        cut = heading
    else:
        nl = text.rfind("\n", 0, index)
        cut = nl + 1 if nl != -1 else index
    if cut == 0:
        return text
    result = text[cut:]
    # One small encode: result is ~budget-sized, hidden may be huge.
    hidden = len(raw) - len(result.encode("utf-8"))
    if hidden <= 0:
        return result
    return f"[... truncated {hidden} bytes above ...]\n{result}"


def _fence_start(text: str, index: int) -> int | None:
    opens = [m.start() for m in _FENCE_RE.finditer(text) if m.start() < index]
    if len(opens) % 2 == 0:
        return None
    return opens[-1]


def _last_heading_at_or_before(text: str, index: int) -> int | None:
    found: int | None = None
    pos = 0
    for line in text.splitlines(keepends=True):
        if pos <= index and is_session_heading(line):
            found = pos
        pos += len(line)
        if pos > index:
            break
    return found
