"""A bot's open tasks as a pure view over event logs (CBE-4, layer 4).

No new mutable store: a task exists only as events that already live in
the bot's internal session transcript (tool calls/results, attributed
meta rows) plus the envelopes still queued on the bus. This module scans
both and derives what is still open, so the prompt index, Thyca, and any
watchdog all read the same source.

What opens and closes a task:

- a ``bot_delegate`` call, or a ``bot_send`` call with ``kind=delegate``,
  opens a task keyed by its tool-call id; a delivered reply closes it,
  an error result closes it as failed, and a ``queued`` result leaves it
  open waiting on the recipient;
- a ``task_open`` call opens a task that outlives the turn: its ack tool
  result deliberately does *not* close it. The ack carries the task's
  key (the span id ``task_open`` returned), and the task is keyed by
  that span, so only a later ``result`` / ``error`` event linked to it
  (``parent_span_id`` == the key, in a meta row or a tool result event)
  closes it;
- attributed meta rows (``kind=delegate|task_open``) open by ``span_id``
  and ``kind=result|error`` rows close by ``parent_span_id``;
- the *latest* event wins for status: a ``kind=status|step`` row (or
  tool result event) whose ``parent_span_id`` links to an open task
  replaces that task's status with its own text instead of opening or
  closing anything;
- envelopes still queued for the bot are open work owed by the bot;
  envelopes the bot sent that are still queued elsewhere make it wait
  on that recipient.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace

from thyca.bots.bus import Envelope
from thyca.core.protocol import Message

_OPEN_KINDS = frozenset({"delegate", "task_open"})
_CLOSE_KINDS = frozenset({"result", "error"})
_STATUS_KINDS = frozenset({"status", "step"})


@dataclass(frozen=True)
class OpenTask:
    """One still-open unit of work, prompt-index sized."""

    task: str
    status: str
    room: str = ""
    waiting_on: str | None = None
    key: str = ""

    def index_line(self) -> str:
        line = f"- {self.task} · {self.status} · {self.room or '-'}"
        if self.waiting_on:
            line += f" · waiting: {self.waiting_on}"
        return line


def format_task_index(tasks: list[OpenTask]) -> str:
    """Compact index text for the hot prompt: task · status · room only."""
    return "\n".join(task.index_line() for task in tasks)


def _clip(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _json_object(text: str | None) -> dict:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def derive_open_tasks(
    messages: list[Message],
    pending: list[Envelope] | tuple[Envelope, ...] = (),
    *,
    bot_id: str | None = None,
) -> list[OpenTask]:
    """Derive still-open tasks from a transcript + queued envelopes.

    ``bot_id`` is the transcript owner: outgoing envelopes are matched
    against it for ``waiting_on``. Without it, every queued envelope is
    treated as work owed to its recipient."""
    open_tasks: dict[str, OpenTask] = {}
    order: list[str] = []
    # Any identifier an event may use for a task (tool-call id, the span
    # key task_open returned, an envelope span) -> canonical dict key.
    aliases: dict[str, str] = {}

    def open_task(key: str, task: OpenTask, *, alias: tuple[str, ...] = ()) -> None:
        if key not in open_tasks:
            order.append(key)
        open_tasks[key] = task
        for name in alias:
            if name:
                aliases[name] = key

    def resolve(ref: object) -> str | None:
        if not isinstance(ref, str) or not ref:
            return None
        if ref in aliases:
            return aliases[ref]
        for candidate in (ref, f"span:{ref}", f"call:{ref}", f"task:{ref}"):
            if candidate in open_tasks:
                return candidate
        return None

    def close_task(key: str) -> None:
        if key in open_tasks:
            del open_tasks[key]
            order.remove(key)

    def close_ref(ref: object) -> None:
        canonical = resolve(ref)
        if canonical is not None:
            close_task(canonical)

    def update_status(ref: object, status: str, room: str = "") -> None:
        canonical = resolve(ref)
        if canonical is None or canonical not in open_tasks:
            return
        current = open_tasks[canonical]
        open_tasks[canonical] = replace(
            current, status=_clip(status) or current.status,
            room=room or current.room,
        )

    # Tool-call ids of task_open calls: their ack results are not results
    # of the work itself, so the result-event matching below skips them
    # (an ack says the task opened, never that the work finished).
    task_open_calls: set[str] = set()
    for message in messages:
        for call in message.tool_calls or []:
            if call.name == "task_open":
                task_open_calls.add(call.id)

    results_seen: dict[str, Message] = {}
    for message in messages:
        if message.role == "tool" and message.tool_call_id:
            results_seen[message.tool_call_id] = message

    for message in messages:
        meta = message.meta or {}
        kind = meta.get("kind")
        span = meta.get("span_id")
        parent = meta.get("parent_span_id")
        # Attributed bus/room rows open, update, and close by span linkage.
        if isinstance(kind, str):
            if kind in _OPEN_KINDS and isinstance(span, str) and span:
                open_task(
                    f"span:{span}",
                    OpenTask(
                        task=_clip(message.content or kind),
                        status=kind,
                        room=str(meta.get("room") or ""),
                        key=f"span:{span}",
                    ),
                    alias=(span,),
                )
            elif kind in _CLOSE_KINDS:
                close_ref(parent)
            elif kind in _STATUS_KINDS:
                # Latest event wins: a status/step row linked to an open
                # task replaces its status, it does not open a new one.
                update_status(
                    parent, _clip(message.content or "") or kind,
                    room=str(meta.get("room") or ""),
                )
        if message.role == "tool" and message.tool_call_id not in task_open_calls:
            # Result/error/status events carried as tool results close or
            # update the task they link to (task_open acks are excluded
            # above via task_open_calls; delegate results are handled by
            # the call matching below and simply no-op here when they
            # carry no event kind).
            event = _json_object(message.content)
            event_kind = event.get("kind") if isinstance(event.get("kind"), str) else kind
            ref = (
                event.get("parent_span_id") or event.get("task_key")
                or event.get("key") or parent
            )
            if event_kind in _CLOSE_KINDS:
                close_ref(ref)
            elif event_kind in _STATUS_KINDS:
                status_text = event.get("status") or event.get("message")
                update_status(
                    ref,
                    status_text if isinstance(status_text, str) and status_text
                    else str(event_kind),
                )
        for call in message.tool_calls or []:
            args = call.arguments or {}
            if call.name == "task_open":
                title = args.get("title") or args.get("task") or "task"
                room = args.get("room") if isinstance(args.get("room"), str) else ""
                ack = results_seen.get(call.id)
                if ack is not None and (ack.meta or {}).get("is_error"):
                    continue  # the open itself failed: no task was opened
                # Link by the key task_open returned (its span id, in the
                # ack result) when present; the call id is the fallback
                # key for transcripts whose ack predates the key field.
                ack_payload = _json_object(ack.content if ack else None)
                linked = (
                    ack_payload.get("span_id") or ack_payload.get("key")
                    or ack_payload.get("task_key")
                )
                linked = linked if isinstance(linked, str) and linked else call.id
                open_task(
                    f"call:{call.id}",
                    OpenTask(task=_clip(title), status="open", room=room,
                             key=linked),
                    alias=(call.id, linked),
                )
                continue
            is_delegate = call.name == "bot_delegate" or (
                call.name == "bot_send" and args.get("kind") == "delegate"
            )
            if not is_delegate:
                continue
            recipient = args.get("bot_id") if isinstance(args.get("bot_id"), str) else ""
            title = args.get("task") or args.get("message") or "delegate"
            result = results_seen.get(call.id)
            if result is None:
                open_task(
                    f"call:{call.id}",
                    OpenTask(task=_clip(title), status="delegated",
                             waiting_on=recipient or None, key=f"call:{call.id}"),
                )
                continue
            if (result.meta or {}).get("is_error"):
                continue  # failed delivery closes the task
            payload = _json_object(result.content)
            if payload.get("queued"):
                span_alias = payload.get("span_id")
                open_task(
                    f"call:{call.id}",
                    OpenTask(task=_clip(title), status="queued",
                             waiting_on=recipient or None, key=f"call:{call.id}"),
                    alias=(call.id, span_alias if isinstance(span_alias, str) else ""),
                )
            # delivered / plain reply / anything else: the work answered.

    for envelope in pending:
        key = f"span:{envelope.span_id}" if envelope.span_id else ""
        if bot_id is not None and envelope.sender == bot_id and envelope.recipient != bot_id:
            # Unanswered outgoing mail: whatever task it belongs to (or a
            # bare entry when the transcript has none) waits on them.
            matched = False
            for existing_key, task in list(open_tasks.items()):
                if task.waiting_on == envelope.recipient:
                    matched = True
                    break
            if not matched and key and key not in open_tasks:
                order.append(key)
                open_tasks[key] = OpenTask(
                    task=_clip(envelope.payload or envelope.kind),
                    status=envelope.kind,
                    room=envelope.session_id,
                    waiting_on=envelope.recipient,
                    key=key,
                )
            continue
        if bot_id is not None and envelope.recipient != bot_id:
            continue
        if key and key not in open_tasks:
            order.append(key)
            open_tasks[key] = OpenTask(
                task=_clip(envelope.payload or envelope.kind),
                status=envelope.kind,
                room=envelope.session_id,
                key=key,
            )

    return [open_tasks[key] for key in order if key in open_tasks]
