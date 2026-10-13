"""Real scheduler (BE-5): cron matching + due-run entry point.

Schedules live on the Agent Card (``schedule: null | cron``). ``tick`` is
the backend trigger the serve loop (or an operator) calls: it runs the
Thyca memory idle sweep and fires due bots by giving them a turn in their
own 1-1 room through the normal ``ChatApp.turn`` path — so routing,
attribution, and transcript recording all behave like any other bot turn.
A room busy with a turn is skipped, never queued behind it.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

CRON_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("weekday", 0, 6),  # Monday=0 .. Sunday=6 (datetime.weekday)
)


class CronError(ValueError):
    """Cron expression could not be parsed."""


def match_field(expr: str, value: int, minimum: int, maximum: int) -> bool:
    """One cron field: ``*``, ``*/n``, ``a-b``, ``a-b/n``, lists, numbers."""
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"empty cron part in {expr!r}")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError:
                raise CronError(f"bad cron step in {expr!r}") from None
            if step < 1:
                raise CronError(f"bad cron step in {expr!r}")
        if part in ("*", ""):
            low, high = minimum, maximum
        elif "-" in part:
            bounds = part.split("-", 1)
            try:
                low, high = int(bounds[0]), int(bounds[1])
            except ValueError:
                raise CronError(f"bad cron range in {expr!r}") from None
        else:
            try:
                low = high = int(part)
            except ValueError:
                raise CronError(f"bad cron number in {expr!r}") from None
        if low < minimum or high > maximum or low > high:
            raise CronError(f"cron value out of range in {expr!r}")
        if low <= value <= high and (value - low) % step == 0:
            return True
    return False


def due(schedule: str | None, now: datetime) -> bool:
    """True when a card's cron schedule fires at ``now`` (minute precision).

    Weekday is Monday=0 .. Sunday=6 (``datetime.weekday``); no Sun=0/7
    alias — one convention, documented here."""
    if schedule is None:
        return False
    if not isinstance(schedule, str) or not schedule.strip():
        return False
    parts = schedule.strip().split()
    if len(parts) != 5:
        raise CronError(f"cron needs 5 fields, got {schedule!r}")
    values = [now.minute, now.hour, now.day, now.month, now.weekday()]
    for (_name, minimum, maximum), expr, value in zip(CRON_FIELDS, parts, values):
        if not match_field(expr, value, minimum, maximum):
            return False
    return True


def tick(app, now: datetime | None = None, *, idle_s: int = 900) -> dict:
    """One scheduler pass: idle sweep + due bots (BE-4 idle trigger lives here).

    Returns ``{"swept": {...}, "fired": [{bot_id, room, reply}...], "skipped": [...]}``.
    Never raises for one bot's failure; failures land in ``skipped``.
    """
    from thyca.sessions import SessionBusy

    moment = now or datetime.now().astimezone()
    report: dict = {"swept": {}, "fired": [], "skipped": []}
    try:
        report["swept"] = app.sweep_idle_sessions(idle_s=idle_s)
    except Exception as exc:
        report["swept"] = {"error": type(exc).__name__}
    try:
        cards = app._bots.list()
    except Exception as exc:
        report["skipped"].append(f"registry: {type(exc).__name__}")
        return report
    for doc in cards:
        if not doc.ok or doc.card is None:
            continue
        card = doc.card
        if card.paused or not card.schedule:
            continue
        try:
            if not due(card.schedule, moment):
                continue
        except CronError as exc:
            report["skipped"].append(f"{card.bot_id}: {exc}")
            continue
        try:
            room_id = _solo_room(app, card.bot_id)
            detail = app.turn(
                room_id,
                f"[scheduled {card.schedule}] Đến giờ làm việc định kỳ. "
                "Thực hiện nhiệm vụ thường lệ của bạn rồi báo cáo ngắn gọn.",
            )
            report["fired"].append({"bot_id": card.bot_id, "room": room_id,
                                    "reply": str(detail.get("reply", ""))[:500]})
        except SessionBusy:
            report["skipped"].append(f"{card.bot_id}: room busy")
        except Exception as exc:
            report["skipped"].append(f"{card.bot_id}: {type(exc).__name__}")
    return report


def _solo_room(app, bot_id: str) -> str:
    """Find or create the bot's own 1-1 room (participants == [bot_id])."""
    for session in app._sessions.list_sessions():
        bots = session.bots if session.bots else ["thyca"]
        if bots == [bot_id]:
            return session.id
    return app.create(bots=[bot_id])["id"]


def zone_now(timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        zone = ZoneInfo("UTC")
    return datetime.now(zone)
