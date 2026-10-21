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
    # Standard cron day-of-week: Sunday=0 (or 7), Monday=1 .. Saturday=6.
    ("weekday", 0, 7),
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


def _dow_matches(expr: str, cron_dow: int) -> bool:
    """Standard-cron DOW match; both 0 and 7 mean Sunday.

    The matched candidate set is built over 0..7 and folded 7→0, so
    ranges like ``5-7`` correctly mean Fri/Sat/Sun."""
    matched = {
        candidate % 7
        for candidate in range(0, 8)
        if match_field(expr, candidate, 0, 7)
    }
    return cron_dow in matched


def _restricted(expr: str) -> bool:
    return expr.strip() not in ("*", "*/1")


def due(schedule: str | None, now: datetime) -> bool:
    """True when a card's cron schedule fires at ``now`` (minute precision).

    Standard cron semantics: Sunday is 0 *and* 7, Monday is 1
    (``0 9 * * 1`` fires Monday 09:00); when both day-of-month and
    day-of-week are restricted (neither is ``*``), a match on either
    one fires (the classic Vixie-cron OR rule)."""
    if schedule is None:
        return False
    if not isinstance(schedule, str) or not schedule.strip():
        return False
    parts = schedule.strip().split()
    if len(parts) != 5:
        raise CronError(f"cron needs 5 fields, got {schedule!r}")
    minute_e, hour_e, dom_e, month_e, dow_e = parts
    if not match_field(minute_e, now.minute, 0, 59):
        return False
    if not match_field(hour_e, now.hour, 0, 23):
        return False
    if not match_field(month_e, now.month, 1, 12):
        return False
    dom_ok = match_field(dom_e, now.day, 1, 31)
    # datetime.weekday(): Mon=0..Sun=6 → cron: Mon=1..Sat=6, Sun=0.
    cron_dow = (now.weekday() + 1) % 7
    dow_ok = _dow_matches(dow_e, cron_dow)
    if _restricted(dom_e) and _restricted(dow_e):
        return dom_ok or dow_ok
    return dom_ok and dow_ok


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
            scheduled_turn = getattr(app, "scheduled_turn", None)
            if callable(scheduled_turn):
                detail = scheduled_turn(
                    card.bot_id,
                    f"[scheduled {card.schedule}] Đến giờ làm việc định kỳ. "
                    "Thực hiện nhiệm vụ thường lệ của bạn rồi báo cáo ngắn gọn.",
                )
                room_id = detail["id"]
            else:
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
    """The bot's fixed DM (CBE-5): scheduled/independent results land there.

    Prefers the app's deterministic ``bot_dm`` lookup (marked session,
    created lazily once). The participant-scan fallback keeps duck-typed
    test doubles working; app turns and delivered rows are appended by
    the app into whichever room the turn runs in, so a room's own work
    still reports into that room, never into the DM."""
    bot_dm = getattr(app, "bot_dm", None)
    if callable(bot_dm):
        return bot_dm(bot_id)["id"]
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
