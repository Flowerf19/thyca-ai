"""Trace scan + payload assembly for ``/api/traces*``.

Split from ``serve.py``: the newest-200 store scan, filter handling, and
the three endpoint payloads. HTTP routing stays in ``serve.py``; nothing
here touches the handler or sockets.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from thyca.serve.trace import TurnSummary, aggregate, scope_turn_to_bot
from thyca.conversations.runtime.wire import message_dict

if TYPE_CHECKING:
    from thyca.app.chat_app import ChatApp
    from thyca.conversations.runtime.store import ConversationStore

# Trace scans read at most this many newest store doors per pass.
_TRACE_SCAN_CAP = 200
# limit=all/0 still pages server-side: one response never carries more than
# this many turns (200 files x all turns is unbounded otherwise). `total`
# keeps the full count so clients page on with offset. A bound, not a
# rejection: 2000 is far above any dashboard page.
_TRACE_ALL_CAP = 2000
_trace_scan_lock = threading.Lock()


# Load trace turns for one explicit conversation reference.
def cached_turns_for_ref(store: ConversationStore, ref) -> list[TurnSummary]:
    """Turns for one door addressed by its explicit ``ConversationRef``.

    The HTTP layer builds the ref from path segments, so kind is never
    guessed here. A missing door raises the store's local error, which
    the route maps to 404 (never a generic 503).
    """
    # Serve-layer trace reads share one lock: concurrent store opens
    # race the root namespace sidecar checks, so list and detail
    # must not open the store at the same moment in this process.
    with _trace_scan_lock:
        return _turns_for_ref_runtime(store, ref)


# Read one store door's trace turns (caller holds the trace scan lock).
def _turns_for_ref_runtime(store: ConversationStore, ref) -> list[TurnSummary]:
    """Projected trace turns for one runtime store door."""
    from thyca.conversations.store.read_adapter import assemble_history
    from thyca.conversations.store.runtime import (
        RuntimeReadIntent,
        runtime_reader_for,
    )
    from thyca.conversations.store.turn_pipeline import (
        trace_turns_projected as _projected_turns_for,
    )
    from thyca.conversations.runtime.store import _runtime_root_for

    runtime_root = _runtime_root_for(store.conversations_dir)
    assert runtime_root is not None
    from thyca.conversations.store.database import (
        _ensure_root_ready,
        _root_instance_id_from_conn,
        _transaction_context,
        open_existing_conversation,
    )
    from thyca.conversations.store.contracts import _decode_metadata

    conn = open_existing_conversation(runtime_root, lock_mode="shared")
    try:
        with _transaction_context(conn, "BEGIN"):
            _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
            row = conn.execute(
                "SELECT metadata FROM conversations WHERE kind=? AND id=?",
                (ref.kind, ref.id),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        from thyca.conversations.store.contracts import ConversationStoreError

        raise ConversationStoreError("conversation unavailable", scope="local")
    participants = _decode_metadata(row[0]).get("participants", [])
    # Read-side member only; trace reads never authorize writes.
    actor = runtime_reader_for(ref, tuple(participants))
    conv = assemble_history(
        runtime_root, ref, actor,
        intent=RuntimeReadIntent("runtime-trace-detail"),
    )
    return _projected_turns_for(conv)


# Collect the newest trace turns across every conversation door.
def collect_turns(chat: ChatApp) -> list[TurnSummary]:
    out: list[TurnSummary] = []
    store = chat.trace_store()
    # Failures are trailing system markers (append-only), excluded from
    # turn_slices, so project them back onto their user row in memory
    # (project_for_trace) before slicing. Detail still expands canonical
    # tool rows via trace_execution_messages (unchanged).
    # Same scan lock as detail reads (see cached_turns_for_ref).
    with _trace_scan_lock:
        return _collect_turns_runtime(chat, store, out)


# Collect trace turns from the runtime store (caller holds no lock).
def _collect_turns_runtime(chat: ChatApp, store: ConversationStore, out: list) -> list[TurnSummary]:
    """Newest-first projected turns over every ready store door.

    A locally corrupt door is skipped, while a global store failure
    propagates so the route can answer 503 instead of reading damage
    as an empty history.
    """
    from thyca.conversations.store.admin import AdminStore
    from thyca.conversations.store.contracts import ConversationStoreError
    from thyca.conversations.store.read_adapter import assemble_history
    from thyca.conversations.store.runtime import (
        RuntimeReadIntent,
        runtime_reader_for,
    )
    from thyca.conversations.store.turn_pipeline import (
        trace_turns_projected as _projected_turns_for,
    )
    from thyca.conversations.runtime.store import _runtime_root_for

    runtime_root = _runtime_root_for(store.conversations_dir)
    assert runtime_root is not None
    intent = RuntimeReadIntent("runtime-trace-list")
    admin = AdminStore(runtime_root)
    # Newest-first listing, capped at the scan window above.
    offset = 0
    rows: list = []
    while len(rows) < _TRACE_SCAN_CAP:
        items, has_more = admin.list(limit=200, offset=offset)
        if not items:
            break
        rows.extend(items)
        if not has_more:
            break
        offset += 200
    rows = rows[:_TRACE_SCAN_CAP]
    for row in rows:
        try:
            actor = runtime_reader_for(row.ref, tuple(row.participants))
            conv = assemble_history(
                runtime_root, row.ref, actor, intent=intent
            )
        except ConversationStoreError as exc:
            # Local damage blocks only its own door (decision 15).
            if getattr(exc, "scope", "global") == "local":
                continue
            raise
        out.extend(_projected_turns_for(conv))
    out.sort(key=lambda t: (t.started_at, t.conversation_id, t.turn_index), reverse=True)
    return out


def _apply_trace_filters(turns: list[TurnSummary], qs: dict) -> list[TurnSummary]:
    model = (qs.get("model", [""])[0] or "").strip()
    status = (qs.get("status", [""])[0] or "").strip()
    bot = (qs.get("bot_id", [""])[0] or "").strip()
    q = (qs.get("q", [""])[0] or "").strip().lower()
    frm = (qs.get("from", [""])[0] or "").strip()
    to = (qs.get("to", [""])[0] or "").strip()
    filtered = turns
    if bot:
        # Project before model/status/search/date filtering: delegated child
        # work belongs to its bot even when the turn's first bot differs.
        # ConversationRuntime membership is context, never proof of metric attribution.
        filtered = [
            scoped for turn in filtered
            if (scoped := scope_turn_to_bot(turn, bot)) is not None
        ]
    if model and model != "all":
        filtered = [t for t in filtered if (t.model or "unknown") == model]
    if status and status != "all":
        filtered = [t for t in filtered if t.status == status]
    if q:
        filtered = [t for t in filtered if q in t.title.lower() or q in t.conversation_id.lower()]
    if frm:
        filtered = [t for t in filtered if t.started_at.split("T")[0] >= frm]
    if to:
        filtered = [t for t in filtered if t.started_at.split("T")[0] <= to]
    return filtered


def trace_stats_payload(chat: ChatApp, query: str) -> dict:
    turns = _apply_trace_filters(collect_turns(chat), parse_qs(query))
    return aggregate(turns)


def trace_list_payload(chat: ChatApp, query: str) -> dict:
    qs = parse_qs(query)
    limit_raw = (qs.get("limit", ["50"])[0] or "50").strip()
    offset_raw = (qs.get("offset", ["0"])[0] or "0").strip()
    try:
        offset = max(0, int(offset_raw))
    except ValueError:
        offset = 0
    turns = _apply_trace_filters(collect_turns(chat), qs)
    total = len(turns)
    if limit_raw in ("0", "all"):
        page = turns[offset : offset + _TRACE_ALL_CAP]
    else:
        try:
            limit = max(1, min(int(limit_raw), 200))
        except ValueError:
            limit = 50
        page = turns[offset : offset + limit]
    return {"traces": [t.to_payload() for t in page], "total": total}


# Build one trace detail for an explicit conversation reference.
def trace_detail_payload_ref(chat: ChatApp, ref, turn_index: int) -> dict:
    """Trace detail addressed by kind; canonical span rows still expand."""
    turns = cached_turns_for_ref(chat.trace_store(), ref)
    for t in turns:
        if t.turn_index == turn_index:
            payload = t.to_payload()
            messages = []
            for message in t.messages:
                meta = message.meta or {}
                if (
                    message.role == "assistant"
                    and meta.get("kind") == "message"
                    and meta.get("bus_event") is not True
                    and isinstance(meta.get("bot_id"), str)
                    and meta.get("bot_id")
                    and isinstance(meta.get("span_id"), str)
                    and meta.get("span_id")
                ):
                    messages.extend(
                        chat.trace_execution_messages(
                            ref.id, meta["bot_id"], meta["span_id"]
                        )
                    )
                else:
                    messages.append(message)
            payload["messages"] = [
                message_dict(m, chat.skills_root, include_args=True)
                for m in messages
            ]
            return payload
    raise ValueError("trace not found")
