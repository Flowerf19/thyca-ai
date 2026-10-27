"""Peer A2A tools: bot_send / bot_fetch (+ Thyca-only bot_delegate)."""
from __future__ import annotations

import json

from thyca.bots.context import current_bot_turn_context
from thyca.memory.identity import current_bot_id
from thyca.tools.registry import ToolSpec

from .deps import AdminDeps, _deny_child


async def _send_and_deliver(
    deps: AdminDeps,
    *,
    sender: str,
    recipient: str,
    kind: str,
    payload: str,
    room: str = "",
    trace_id: str | None = None,
    refs: tuple[str, ...] = (),
) -> str:
    """Enqueue one envelope and, in a live turn, run the recipient now.

    Delivery is the production path: the recipient bot's own AgentLoop
    turn runs (bounded by the bus per-trace hop limit applied at send),
    its attributed result lands in the room transcript via ChatApp's
    deliverer, and its reply is returned here for the sender's flow.
    Outside a live turn (no deliverer/context) the envelope stays queued
    for the recipient's next turn, reported honestly as ``queued``."""
    ctx = current_bot_turn_context()
    session_id = room or (ctx.room_id if ctx is not None else "")
    envelope = deps.runtime.send(
        sender=sender,
        recipient=recipient,
        kind=kind,
        session_id=session_id,
        payload=payload,
        trace_id=trace_id or (ctx.trace_id if ctx is not None else None),
        parent_span_id=ctx.span_id if ctx is not None else None,
        refs=refs,
    )
    reply = await deps.runtime.deliver(envelope)
    out: dict = {
        "ok": True,
        "to": recipient,
        "trace_id": envelope.trace_id,
        "span_id": envelope.span_id,
    }
    if reply is not None:
        out["delivered"] = True
        out["reply"] = reply
    else:
        out["queued"] = deps.runtime._bus.pending(recipient)
    return json.dumps(out, ensure_ascii=False)


def _send_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Sender identity comes only from the in-flight turn context /
        # bot ContextVar — never from arguments (there is no sender arg).
        ctx = current_bot_turn_context()
        sender = ctx.sender_id if ctx is not None else current_bot_id()
        if sender is None:
            return "denied: bot_send requires an in-flight bot turn"
        recipient = args.get("bot_id")
        message = args.get("message")
        if not isinstance(recipient, str) or not recipient.strip():
            return "missing argument: bot_id"
        if not isinstance(message, str) or not message.strip():
            return "missing argument: message"
        kind = args.get("kind") if isinstance(args.get("kind"), str) else "message"
        raw_refs = args.get("refs")
        refs = (
            tuple(ref for ref in raw_refs if isinstance(ref, str) and ref.strip())
            if isinstance(raw_refs, list)
            else ()
        )
        try:
            card = deps.bots.require(recipient)
            if card.paused:
                return f"bot_send failed: bot is paused: {recipient}"
            return await _send_and_deliver(
                deps, sender=sender, recipient=recipient, kind=kind,
                payload=message.strip(), refs=refs,
            )
        except Exception as exc:
            return f"bot_send failed: {exc}"

    return ToolSpec(
        name="bot_send",
        description=(
            "Send a message/task to another bot and get its reply. The "
            "recipient bot runs its own turn immediately; its reply is "
            "returned to you (the exchange is also shown grouped in "
            "that room/DM under both names — open it for the full "
            "thread). "
            "Your identity as sender is taken from your turn, never from "
            "arguments. kind: delegate|message|help_request (default message). "
            "Large content should travel by reference: pass refs (file paths, "
            "memory entry ids, span ids) and keep message short — bodies over "
            "1000 chars are excerpted in the recipient's prompt, with the "
            "full text fetchable via bot_fetch."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "message": {"type": "string"},
                "kind": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["bot_id", "message"],
            "additionalProperties": False,
        },
        handler=handler,
        # parallel_safe: no gateway resource lock is held across the
        # nested recipient turn, so multi-hop bot->bot cannot deadlock
        # on the send tool's own lock.
        parallel_safe=True,
    )


def _fetch_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Authorization (context review): the caller — from the in-flight
        # turn context / bot ContextVar, never from arguments — must be
        # the envelope's sender or recipient (recorded at Bus.send).
        # Anyone else gets an honest denial with no content, exactly as
        # if they had guessed a span id belonging to another pair.
        ctx = current_bot_turn_context()
        caller = ctx.sender_id if ctx is not None else current_bot_id()
        if caller is None:
            return "denied: bot_fetch requires an in-flight bot turn"
        span_id = args.get("span_id")
        if not isinstance(span_id, str) or not span_id.strip():
            return "missing argument: span_id"
        span_id = span_id.strip()
        parties = deps.runtime.artifact_parties(span_id)
        if parties is None:
            return json.dumps(
                {"ok": False, "span_id": span_id,
                 "error": "artifact not found (unknown or evicted)"},
                ensure_ascii=False,
            )
        if caller not in parties:
            return json.dumps(
                {"ok": False, "span_id": span_id,
                 "error": "denied: artifact was not sent by or to this bot"},
                ensure_ascii=False,
            )
        body = deps.runtime.fetch_artifact(span_id, caller=caller)
        if body is None:  # evicted between the two lookups
            return json.dumps(
                {"ok": False, "span_id": span_id,
                 "error": "artifact not found (unknown or evicted)"},
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": True, "span_id": span_id, "content": body},
            ensure_ascii=False,
        )

    return ToolSpec(
        name="bot_fetch",
        description=(
            "Fetch the full body of an A2A envelope by span_id. Envelope "
            "bodies in your prompt are excerpts (max 1000 chars); when an "
            "envelope is marked truncated, call this with its span_id to "
            "read the complete text. Content sent as refs (file paths / "
            "memory ids) should be read with read/memory_get instead."
        ),
        parameters={
            "type": "object",
            "properties": {"span_id": {"type": "string"}},
            "required": ["span_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _delegate_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        bot_id = args["bot_id"]
        task = args.get("task", "")
        if not isinstance(task, str) or not task.strip():
            return "missing argument: task"
        try:
            card = deps.bots.require(bot_id)
            if card.paused:
                return f"bot_delegate failed: bot is paused: {bot_id}"
        except Exception as exc:
            return f"bot_delegate failed: {exc}"
        room = args.get("room") if isinstance(args.get("room"), str) else ""
        ctx = current_bot_turn_context()
        sender = ctx.sender_id if ctx is not None else "thyca"
        try:
            return await _send_and_deliver(
                deps, sender=sender, recipient=bot_id, kind="delegate",
                payload=task.strip(), room=room,
                trace_id=args.get("trace_id") if isinstance(args.get("trace_id"), str) else None,
            )
        except Exception as exc:
            return f"bot_delegate failed: {exc}"

    return ToolSpec(
        name="bot_delegate",
        description=(
            "Give a task to a bot (Thyca only). The recipient bot runs its "
            "own turn immediately and its reply is returned to you and "
            "recorded in the room under its name; returns the trace_id too."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "task": {"type": "string"},
                "room": {"type": "string"},
                "trace_id": {"type": "string"},
            },
            "required": ["bot_id", "task"],
            "additionalProperties": False,
        },
        handler=handler,
        # No gateway lock held across the nested recipient turn.
        parallel_safe=True,
    )
