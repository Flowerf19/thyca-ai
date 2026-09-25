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
from thyca.memory.heading import day, is_session_heading, read_text_file, strip_heading_comments

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

    def refresh(self, state: ActiveState, now: datetime) -> ActiveSnapshot:
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
        return ActiveSnapshot(
            soul=self._read(self.thyca_dir / "SOUL.md"),
            identity=self._read(self.thyca_dir / "IDENTITY.md"),
            user=self._read(self.thyca_dir / "USER.md"),
            today=self._tail(self._read(state.today_path)),
            skills=self._skills.index_text(),
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

    def _read(self, path: Path) -> str:
        try:
            text = read_text_file(path)
        except (OSError, UnicodeDecodeError) as exc:
            raise ActiveMemoryError(f"cannot read {path}: {exc}") from exc
        if text is None:
            return ""
        return strip_heading_comments(text)

    def _tail(self, text: str) -> str:
        return tail_text(text, self._budget)


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
        return text[heading:]
    nl = text.rfind("\n", 0, index)
    if nl != -1:
        return text[nl + 1 :]
    return text[index:]


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
