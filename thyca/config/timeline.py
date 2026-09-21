"""Timeline entity plus host-timezone detection."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .defaults import DEFAULT_TIMELINE_TIMEZONE
from .errors import ConfigError
from .validation import _text


def _system_timezone() -> str:
    """Best-effort IANA zone of the host; falls back to the default."""
    try:
        link = pathlib.Path("/etc/localtime")
        if link.is_symlink():
            target = link.resolve()
            if "zoneinfo" in target.parts:
                name = "/".join(target.parts[target.parts.index("zoneinfo") + 1 :])
                ZoneInfo(name)
                return name
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    return DEFAULT_TIMELINE_TIMEZONE


@dataclass(frozen=True)
class TimelineCfg:
    timezone: str = DEFAULT_TIMELINE_TIMEZONE

    def __post_init__(self) -> None:
        _text(self.timezone, "timeline.timezone")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
            raise ConfigError(
                f"timeline.timezone is not a valid IANA timezone: {self.timezone!r}"
            ) from error
