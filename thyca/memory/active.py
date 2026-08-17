"""Active memory: files currently injected into the system prompt."""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from thyca.config import DEFAULT_LIMITS_HOT_TAIL_KB, DEFAULT_TIMELINE_TIMEZONE

_HEADING_RE = re.compile(r"^## \d{2}:\d{2}\b", re.MULTILINE)
_FENCE_RE = re.compile(r"^```", re.MULTILINE)

_TEMPLATES = {
    "SOUL.md": "# Soul\n",
    "USER.md": "# User\n",
    "MEMORY.md": "# Memory\n",
}


class ActiveMemoryError(RuntimeError):
    """Active memory file or directory could not be prepared or read."""


@dataclass
class ActiveState:
    day: str
    today_path: Path
    yesterday_path: Path | None
    yesterday: str


@dataclass(frozen=True)
class ActiveSnapshot:
    soul: str
    user: str
    memory: str
    today: str
    yesterday: str


class ActiveMemory:
    """Read-only window over the markdown files in the current prompt."""

    def __init__(
        self,
        thyca_dir: Path | None = None,
        tail_kb: int | None = None,
        timezone_name: str | None = None,
        on_day_close: Callable[[str], None] | None = None,
    ) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca")
        self.tail_kb = DEFAULT_LIMITS_HOT_TAIL_KB if tail_kb is None else tail_kb
        self.timezone_name = timezone_name or DEFAULT_TIMELINE_TIMEZONE
        self.on_day_close = on_day_close

    @property
    def memory_dir(self) -> Path:
        return self.thyca_dir / "memory"

    @property
    def _budget(self) -> int:
        return self.tail_kb * 1024

    def ensure_files(self, now: datetime | None = None) -> None:
        self._secure_dir(self.thyca_dir)
        self._secure_dir(self.memory_dir)
        for name, template in _TEMPLATES.items():
            self._create_if_missing(self.thyca_dir / name, template)
        day = self._day(now or self._now())
        self._create_if_missing(self._daily_path(day), f"# {day}\n")

    def open_session(self, now: datetime) -> ActiveState:
        self.ensure_files(now)
        day = self._day(now)
        yesterday_day = self._shift_day(day, -1)
        yesterday_path = self._daily_path(yesterday_day)
        yesterday = ""
        if yesterday_path.is_file() and not yesterday_path.is_symlink():
            yesterday = self._tail(self._read(yesterday_path))
        else:
            yesterday_path = None
        return ActiveState(
            day=day,
            today_path=self._daily_path(day),
            yesterday_path=yesterday_path,
            yesterday=yesterday,
        )

    def refresh(self, state: ActiveState, now: datetime) -> ActiveSnapshot:
        day = self._day(now)
        if day != state.day:
            closed = state.day
            state.yesterday_path = state.today_path
            state.yesterday = self._tail(self._read(state.today_path))
            state.day = day
            state.today_path = self._daily_path(day)
            self._create_if_missing(state.today_path, f"# {day}\n")
            if self.on_day_close is not None:
                self.on_day_close(closed)
        return ActiveSnapshot(
            soul=self._read(self.thyca_dir / "SOUL.md"),
            user=self._read(self.thyca_dir / "USER.md"),
            memory=self._tail(self._read(self.thyca_dir / "MEMORY.md")),
            today=self._tail(self._read(state.today_path)),
            yesterday=state.yesterday,
        )

    def _now(self) -> datetime:
        return datetime.now(self._zone())

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)

    def _day(self, now: datetime) -> str:
        zone = self._zone()
        if now.tzinfo is None:
            aware = now.replace(tzinfo=zone)
        else:
            aware = now.astimezone(zone)
        return aware.date().isoformat()

    def _daily_path(self, day: str) -> Path:
        return self.memory_dir / f"{day}.md"

    @staticmethod
    def _shift_day(day: str, delta: int) -> str:
        return (datetime.fromisoformat(day).date() + timedelta(days=delta)).isoformat()

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
        if not path.is_file() or path.is_symlink():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ActiveMemoryError(f"cannot read {path}: {exc}") from exc

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
    for match in _HEADING_RE.finditer(text):
        if match.start() <= index:
            found = match.start()
        else:
            break
    return found
