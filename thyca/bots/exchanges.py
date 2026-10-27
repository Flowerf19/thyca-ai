"""Bot<->bot exchange pairing over stored room transcripts.

A send (`bot_send`) lands in the origin room as a bus row
(``meta.bus_event is True``, sender in ``meta.bot_id``); the recipient's
reply lands as a ``kind='result'`` row whose ``parent_span_id`` is the
bus row's ``span_id``. New bus rows also carry ``meta.recipient``
explicitly; older rows pair best-effort through that span linkage.

Pure functions over loaded messages so the serve endpoints and tests
share one rule. Roles other than assistant never count; a row counts
only when both sides of its pair resolve (sender + recipient known).
"""
from __future__ import annotations

from typing import Any


def _meta(message: Any) -> dict:
    meta = getattr(message, "meta", None)
    return meta if isinstance(meta, dict) else {}


def _role(message: Any) -> str:
    return getattr(message, "role", "") or ""


def _ts(message: Any) -> str:
    return str(getattr(message, "ts", "") or "")


def _is_bus_row(message: Any) -> bool:
    return _role(message) == "assistant" and _meta(message).get("bus_event") is True


def _is_result_row(message: Any) -> bool:
    return _role(message) == "assistant" and _meta(message).get("kind") == "result"


def exchange_rows(messages: list[Any]) -> list[dict[str, Any]]:
    """Classified exchange rows with resolved pairs, in storage order.

    Each item: ``{sender, partner, author, kind, content, ts, trace_id,
    span_id}``. ``sender`` is the exchange initiator; ``author`` is the
    row's actual writer (the partner on result rows).
    Bus rows pair via explicit ``meta.recipient`` (new writes) or the
    in-room result row whose ``parent_span_id`` is the bus ``span_id``.
    Result rows pair via the in-room bus row their ``parent_span_id``
    points at. Rows whose partner cannot be resolved are dropped.
    """
    messages = list(messages or [])
    bus_by_span: dict[str, dict] = {}
    for message in messages:
        if not _is_bus_row(message):
            continue
        meta = _meta(message)
        span = meta.get("span_id")
        if isinstance(span, str) and span:
            bus_by_span[span] = meta
    results_by_parent: dict[str, list[dict]] = {}
    for message in messages:
        if not _is_result_row(message):
            continue
        meta = _meta(message)
        parent = meta.get("parent_span_id")
        if isinstance(parent, str) and parent:
            results_by_parent.setdefault(parent, []).append(meta)
    out: list[dict[str, Any]] = []
    for message in messages:
        meta = _meta(message)
        if _is_bus_row(message):
            sender = meta.get("bot_id")
            if not isinstance(sender, str) or not sender:
                continue
            partner = meta.get("recipient")
            if not isinstance(partner, str) or not partner:
                hits = results_by_parent.get(
                    meta.get("span_id") if isinstance(meta.get("span_id"), str) else "", []
                )
                partner = hits[0].get("bot_id") if hits else None
            if not isinstance(partner, str) or not partner or partner == sender:
                continue
            out.append({
                "sender": sender,
                "partner": partner,
                "author": sender,
                "kind": meta.get("kind") if isinstance(meta.get("kind"), str) else "",
                "content": getattr(message, "content", "") or "",
                "ts": _ts(message),
                "trace_id": meta.get("trace_id") if isinstance(meta.get("trace_id"), str) else "",
                "span_id": meta.get("span_id") if isinstance(meta.get("span_id"), str) else "",
            })
        elif _is_result_row(message):
            bot = meta.get("bot_id")
            parent = meta.get("parent_span_id")
            bus = bus_by_span.get(parent) if isinstance(parent, str) else None
            sender = bus.get("bot_id") if bus else None
            if not isinstance(bot, str) or not bot or not isinstance(sender, str) or not sender or sender == bot:
                continue
            out.append({
                "sender": sender,
                "partner": bot,
                "author": bot,
                "kind": "result",
                "content": getattr(message, "content", "") or "",
                "ts": _ts(message),
                "trace_id": meta.get("trace_id") if isinstance(meta.get("trace_id"), str) else "",
                "span_id": meta.get("span_id") if isinstance(meta.get("span_id"), str) else "",
            })
    return out


def _involves(row: dict[str, Any], bot_id: str) -> bool:
    return row["sender"] == bot_id or row["partner"] == bot_id


def _other(row: dict[str, Any], bot_id: str) -> str:
    return row["partner"] if row["sender"] == bot_id else row["sender"]


def collect_exchange_rooms(sessions: list[Any]) -> list[list[Any]]:
    """Per-session message lists for pairing (rooms + DM-origin rows).

    Pairing filters by the involved pair, so unrelated pairs never
    surface; every session contributes its transcript."""
    return [list(getattr(session, "messages", None) or []) for session in sessions]


def partners_for(bot_id: str, rooms: list[list[Any]]) -> list[dict[str, Any]]:
    """Exchange partners of one bot: partner id, row count, latest ts.

    ``rooms`` is a list of per-room message lists (already privacy
    filtered by the caller). Sorted by latest exchange first.
    """
    found: dict[str, dict[str, Any]] = {}
    for messages in rooms:
        for row in exchange_rows(messages):
            if not _involves(row, bot_id):
                continue
            partner = _other(row, bot_id)
            entry = found.setdefault(partner, {"partner": partner, "count": 0, "last_ts": ""})
            entry["count"] += 1
            if row["ts"] > entry["last_ts"]:
                entry["last_ts"] = row["ts"]
    return sorted(found.values(), key=lambda e: (e["last_ts"], e["partner"]), reverse=True)


def pair_thread(bot_a: str, bot_b: str, rooms: list[list[Any]]) -> list[dict[str, Any]]:
    """Every recorded message between A and B (both directions), oldest first."""
    wanted = {bot_a, bot_b}
    out: list[dict[str, Any]] = []
    for messages in rooms:
        for row in exchange_rows(messages):
            if {row["sender"], row["partner"]} == wanted:
                out.append(row)
    out.sort(key=lambda r: (r["ts"], r["span_id"]))
    return out
