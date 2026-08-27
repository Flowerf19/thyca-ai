"""Trace scan + payload assembly for ``/api/traces*``.

Split from ``serve.py``: the parse cache, the newest-200 scan, filter
handling, and the three endpoint payloads. HTTP routing stays in
``serve.py``; nothing here touches the handler or sockets.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from thyca.trace import TurnSummary, turns_from_session

if TYPE_CHECKING:
    from thyca.chat_app import ChatApp
    from thyca.sessions import Session
    from thyca.sessions.store import SessionStore

# Parse cache for trace scans: path -> (mtime_ns, Session). JSONL stays the
# source of truth — entries are re-parsed only when mtime_ns changes, and the
# cache never holds more files than the newest-200 scan window.
_TRACE_SCAN_CAP = 200
_trace_scan_lock = threading.Lock()
_trace_sessions: dict[Path, tuple[int, Session]] = {}


def _cached_session(store: SessionStore, path: Path):
    """Parsed Session for path, re-reading only when mtime_ns changed."""
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _trace_sessions.pop(path, None)
        return None
    cached = _trace_sessions.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        session = store.load(path.stem)
    except Exception:
        # corrupt or vanished: drop any stale entry, skip the file
        _trace_sessions.pop(path, None)
        return None
    _trace_sessions[path] = (mtime, session)
    return session


def cached_turns_for(store: SessionStore, session_id: str) -> list[TurnSummary]:
    """Turns for one session via the parse cache.

    Cache miss (or file turned corrupt) re-loads through ``store.load`` so the
    caller keeps the real ``SessionNotFound`` / ``SessionCorrupt`` semantics.
    """
    session = _cached_session(store, store.path_for(session_id))
    if session is None:
        session = store.load(session_id)
    return turns_from_session(session)


def collect_turns(chat: ChatApp) -> list[TurnSummary]:
    out: list[TurnSummary] = []
    store = chat.trace_store()
    # cap 200 newest files to keep single-user scan cheap
    paths = store.list_paths()[:_TRACE_SCAN_CAP]
    with _trace_scan_lock:
        for path in paths:
            session = _cached_session(store, path)
            if session is None:
                continue
            try:
                out.extend(turns_from_session(session))
            except Exception:
                continue
        # evict files that fell out of the newest-200 window
        current = set(paths)
        for stale in [p for p in _trace_sessions if p not in current]:
            del _trace_sessions[stale]
    # sort newest first by started_at desc, then session_id desc for stability
    out.sort(key=lambda t: (t.started_at, t.session_id, t.turn_index), reverse=True)
    return out


def _apply_trace_filters(turns: list[TurnSummary], qs: dict) -> list[TurnSummary]:
    model = (qs.get("model", [""])[0] or "").strip()
    status = (qs.get("status", [""])[0] or "").strip()
    q = (qs.get("q", [""])[0] or "").strip().lower()
    frm = (qs.get("from", [""])[0] or "").strip()
    to = (qs.get("to", [""])[0] or "").strip()
    filtered = turns
    if model and model != "all":
        filtered = [t for t in filtered if (t.model or "unknown") == model]
    if status and status != "all":
        filtered = [t for t in filtered if t.status == status]
    if q:
        filtered = [t for t in filtered if q in t.title.lower() or q in t.session_id.lower()]
    if frm:
        filtered = [t for t in filtered if t.started_at.split("T")[0] >= frm]
    if to:
        filtered = [t for t in filtered if t.started_at.split("T")[0] <= to]
    return filtered


def trace_stats_payload(chat: ChatApp, query: str) -> dict:
    from thyca.trace import aggregate

    turns = _apply_trace_filters(collect_turns(chat), parse_qs(query))
    return aggregate(turns)


def trace_list_payload(chat: ChatApp, query: str) -> dict:
    qs = parse_qs(query)
    limit_raw = (qs.get("limit", ["50"])[0] or "50").strip()
    offset_raw = (qs.get("offset", ["0"])[0] or "0").strip()
    try:
        limit = max(1, min(int(limit_raw), 200))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(offset_raw))
    except ValueError:
        offset = 0
    turns = _apply_trace_filters(collect_turns(chat), qs)
    total = len(turns)
    page = turns[offset : offset + limit]
    return {"traces": [t.to_payload() for t in page], "total": total}


def trace_detail_payload(chat: ChatApp, session_id: str, turn_index: int) -> dict:
    turns = cached_turns_for(chat.trace_store(), session_id)
    for t in turns:
        if t.turn_index == turn_index:
            payload = t.to_payload()
            payload["messages"] = [
                {
                    "role": m.role,
                    "content": m.content,
                    "ts": m.ts,
                    "tool_calls": [{"id": c.id, "name": c.name} for c in (m.tool_calls or [])],
                    "tool_call_id": m.tool_call_id,
                    "meta": m.meta,
                }
                for m in t.messages
            ]
            return payload
    raise ValueError("trace not found")