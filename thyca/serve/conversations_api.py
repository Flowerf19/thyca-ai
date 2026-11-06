"""Explicit DM/group conversation endpoints (hard-cut replacement).

Every handler builds its ConversationRef from the path segments only:
kind is never guessed from identifier text and never taken from the
request body. The ref id is then handed to the existing ChatApp facade
(``ref.id`` is the stored door id: bot id for a DM, ``group-<hex32>``
for a group). Public payloads carry ``kind`` plus ``bot_id`` (DM) or
``group_id`` (group); the legacy ``id``/``dm``/``window_id`` fields
never leave this module.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from thyca.bots.registry import RegistryError
from thyca.core.conversation import ConversationRef
from thyca.serve.bridge import _log_turn_failure, pump_stream, stream_turn
from thyca.serve.errors import _with_json, parse_turn_body, public_turn_error
from thyca.sessions.wire import session_http_error

if TYPE_CHECKING:
    from thyca.app.chat_app import ChatApp

__all__ = [
    "bot_dm",
    "bot_exchange_thread",
    "bot_exchanges",
    "bots_list",
    "conversation_list",
    "dm_compact",
    "dm_operations",
    "dm_private_followup",
    "dm_tasks",
    "dm_turn",
    "dm_turn_cancel",
    "dm_turn_follow",
    "dm_turn_stream",
    "group_bots",
    "group_compact",
    "group_create",
    "group_delete",
    "group_detail",
    "group_operations",
    "group_rename",
    "group_tasks",
    "group_turn",
    "group_turn_cancel",
    "group_turn_follow",
    "group_turn_stream",
]


# Send one public conversation error (kind is stated, never guessed).
def _conv_error(handler, exc: Exception) -> None:
    mapped = session_http_error(exc)
    if mapped is not None:
        handler._json(mapped[0], {"error": mapped[1].replace("session", "conversation")})
        return
    from thyca.conversations.store.contracts import ConversationStoreError

    if isinstance(exc, ConversationStoreError):
        status = 404 if getattr(exc, "scope", "global") == "local" else 503
        handler._json(status, {"error": "conversation not found" if status == 404 else "conversation unavailable"})
        return
    traceback.print_exc(file=sys.stderr)
    handler._json(503, {"error": "chat unavailable"})


# Report a missing chat app without touching the handler further.
def _missing_chat(handler, app: "ChatApp | None") -> bool:
    """True when the server was started without a chat app (404 sent)."""
    if app is None:
        handler._json(404, {"error": "chat unavailable"})
        return True
    return False


# List every public bot card for the bots endpoint.
def bots_list(handler, app: "ChatApp | None") -> None:
    if _missing_chat(handler, app):
        return
    try:
        handler._json(200, {"bots": app.bots_list()})
    except Exception as exc:
        _conv_error(handler, exc)


# List the bots one bot has exchanged messages with.
def bot_exchanges(handler, app: "ChatApp | None", bot_id: str) -> None:
    if _missing_chat(handler, app):
        return
    try:
        handler._json(200, {"exchanges": app.dm_exchange_partners(bot_id)})
    except RegistryError:
        handler._json(404, {"error": "bot not found"})
    except Exception as exc:
        _conv_error(handler, exc)


# List every recorded message between two bots, oldest first.
def bot_exchange_thread(
    handler, app: "ChatApp | None", bot_id: str, partner_id: str
) -> None:
    if _missing_chat(handler, app):
        return
    try:
        handler._json(
            200, {"messages": app.dm_exchange_thread(bot_id, partner_id)}
        )
    except RegistryError:
        handler._json(404, {"error": "bot not found"})
    except Exception as exc:
        _conv_error(handler, exc)


# Read one bot's operational view in one exact conversation.
def bot_operations(
    handler, app: "ChatApp | None", bot_id: str, session_id: str, query: str,
) -> None:
    """Read one bot's operational view in one exact room."""
    from thyca.app.turn_options import InvalidTurnOption, validate_turn_options
    from thyca.bots.timeline import TimelineError
    from thyca.sessions import SessionCorrupt, SessionError, SessionNotFound

    if _missing_chat(handler, app):
        return
    try:
        params = parse_qs(
            query, keep_blank_values=True, strict_parsing=True, max_num_fields=2,
        )
    except ValueError:
        handler._json(400, {"error": "invalid query"})
        return
    if set(params) - {"model", "effort"} or any(
        len(values) != 1 for values in params.values()
    ):
        handler._json(400, {"error": "invalid query"})
        return
    try:
        model, effort, _ = validate_turn_options({
            key: values[0] for key, values in params.items()
        })
    except InvalidTurnOption:
        handler._json(400, {"error": "invalid model or effort"})
        return
    try:
        payload = app.bot_operations(
            session_id, bot_id, model=model, effort=effort,
        )
    except InvalidTurnOption:
        handler._json(400, {"error": "invalid model or effort"})
    except RegistryError:
        handler._json(404, {"error": "bot or room not found"})
    except SessionNotFound:
        handler._json(404, {"error": "session not found"})
    except (TimelineError, FileNotFoundError, SessionCorrupt, SessionError, OSError):
        handler._json(503, {"error": "bot information unavailable"})
    except Exception:
        traceback.print_exc(file=sys.stderr)
        handler._json(503, {"error": "bot information unavailable"})
    else:
        handler._json(200, payload)


# Read one bot's live tasks in one exact conversation.
def bot_tasks(handler, app: "ChatApp | None", bot_id: str, session_id: str, query: str) -> None:
    """Open runtime tasks plus live calls for one room participant."""
    from thyca.bots.timeline import TimelineError
    from thyca.sessions import SessionCorrupt, SessionError, SessionNotFound

    if _missing_chat(handler, app):
        return
    if query:
        handler._json(400, {"error": "invalid query"})
        return
    try:
        payload = app.bot_room_tasks(session_id, bot_id)
    except RegistryError:
        handler._json(404, {"error": "bot or room not found"})
    except SessionNotFound:
        handler._json(404, {"error": "session not found"})
    except (TimelineError, FileNotFoundError, SessionCorrupt, SessionError, OSError):
        handler._json(503, {"error": "bot information unavailable"})
    except Exception:
        traceback.print_exc(file=sys.stderr)
        handler._json(503, {"error": "bot information unavailable"})
    else:
        handler._json(200, payload)


# Reject bodies that try to name a conversation by legacy id fields.
def _reject_legacy_id_fields(handler, payload: dict) -> bool:
    if "session_id" in payload or "id" in payload:
        handler._json(400, {"error": "invalid body"})
        return True
    return False


# Read the stored kind of one payload without guessing from its ids.
def _payload_kind(payload: dict) -> str:
    kind = payload.get("kind")
    if kind not in ("dm", "group"):
        raise ValueError("conversation kind missing")
    return kind


# Convert one summary payload to the public conversation shape.
def to_summary(payload: dict) -> dict:
    kind = _payload_kind(payload)
    out = {
        "kind": kind,
        "title": payload.get("title"),
        "updated_at": payload.get("updated_at"),
        "message_count": payload.get("message_count"),
        "turns": payload.get("turns"),
        "bots": payload.get("bots"),
        "channel": payload.get("channel", "webui"),
    }
    if kind == "dm":
        out["bot_id"] = payload.get("dm") or payload.get("id")
    else:
        out["group_id"] = payload.get("id")
    return out


# Convert one detail payload to the public conversation shape.
def to_detail(payload: dict) -> dict:
    kind = _payload_kind(payload)
    out = dict(payload)
    for key in ("id", "dm", "window_id", "session_id"):
        out.pop(key, None)
    out["kind"] = kind
    if kind == "dm":
        out["bot_id"] = payload.get("dm") or payload.get("id")
    else:
        out["group_id"] = payload.get("id")
    return out


# Build the DM ref for one bot path segment (kind comes from the route).
def _dm_ref(bot_id: str) -> ConversationRef:
    return ConversationRef.dm(bot_id)


# Build the group ref for one group path segment (kind comes from the route).
def _group_ref(group_id: str) -> ConversationRef:
    return ConversationRef.group(group_id)


# Check that a loaded door really is the kind its route claims.
def _require_kind(handler, app: "ChatApp", ref: ConversationRef) -> dict | None:
    try:
        detail = app.get_payload(ref.id)
    except Exception as exc:
        _conv_error(handler, exc)
        return None
    # Kind comes from the path-built ref; the payload must agree with it.
    if detail.get("kind") != ref.kind:
        handler._json(404, {"error": "conversation not found"})
        return None
    return detail


# List every conversation, optionally filtered to one explicit kind.
def conversation_list(handler, app: "ChatApp | None", query: str = "") -> None:
    if _missing_chat(handler, app):
        return
    kind = None
    if query:
        params = parse_qs(query, keep_blank_values=True)
        values = params.get("kind")
        if values is not None:
            if len(values) != 1 or values[0] not in ("dm", "group"):
                handler._json(400, {"error": "invalid kind"})
                return
            kind = values[0]
    try:
        payload = app.list_payload()
    except Exception as exc:
        _conv_error(handler, exc)
        return
    items = [to_summary(item) for item in payload.get("sessions", [])]
    if kind is not None:
        items = [item for item in items if item["kind"] == kind]
    handler._json(200, {"model": payload.get("model"), "conversations": items})


# Read one bot's fixed DM, creating it only when genuinely absent.
def bot_dm(handler, app: "ChatApp | None", bot_id: str) -> None:
    if _missing_chat(handler, app):
        return
    try:
        _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    try:
        handler._json(200, to_detail(app.bot_dm(bot_id)))
    except RegistryError:
        handler._json(404, {"error": "bot not found"})
    except Exception as exc:
        _conv_error(handler, exc)


# Create one group from an exact participant list of at least two bots.
def group_create(handler, app: "ChatApp | None") -> None:
    if _missing_chat(handler, app):
        return
    try:
        raw = handler._read_body()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    payload: dict = {}
    if raw.strip():
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            handler._json(400, {"error": "invalid body"})
            return
        if not isinstance(payload, dict):
            handler._json(400, {"error": "invalid body"})
            return
    if _reject_legacy_id_fields(handler, payload):
        return
    bots = payload.get("bots")
    title = payload.get("title")
    if title is not None and not isinstance(title, str):
        handler._json(400, {"error": "invalid title"})
        return
    if (
        not isinstance(bots, list)
        or len(bots) < 2
        or len(set(bots)) != len(bots)
        or any(not isinstance(item, str) or not item for item in bots)
    ):
        handler._json(400, {"error": "invalid bots"})
        return
    try:
        detail = app.create(bots=list(bots))
        ref = _group_ref(detail["id"])
        if title is not None and title.strip():
            app.rename_session(ref.id, title)
            detail = app.get_payload(ref.id)
    except ValueError:
        handler._json(400, {"error": "invalid bots"})
    except RegistryError:
        handler._json(404, {"error": "bot not found"})
    except Exception as exc:
        _conv_error(handler, exc)
    else:
        handler._json(200, to_detail(detail))


# Read one group by its explicit group id.
def group_detail(handler, app: "ChatApp | None", group_id: str) -> None:
    if _missing_chat(handler, app):
        return
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    detail = _require_kind(handler, app, ref)
    if detail is not None:
        handler._json(200, to_detail(detail))


# Rename one group from its explicit group id.
def group_rename(handler, app: "ChatApp | None", group_id: str) -> None:
    if _missing_chat(handler, app):
        return
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    if _require_kind(handler, app, ref) is None:
        return

    # Store the user-typed title for this group only.
    def run(payload: dict) -> None:
        if _reject_legacy_id_fields(handler, payload):
            return
        title = payload.get("title")
        if not isinstance(title, str):
            handler._json(400, {"error": "invalid title"})
            return
        try:
            stored = app.rename_session(ref.id, title)
        except ValueError:
            handler._json(400, {"error": "invalid title"})
        except Exception as exc:
            _conv_error(handler, exc)
        else:
            handler._json(200, {"ok": True, "kind": "group", "group_id": ref.id, "title": stored})

    _with_json(handler, run)


# Delete one group from its explicit group id.
def group_delete(handler, app: "ChatApp | None", group_id: str) -> None:
    if _missing_chat(handler, app):
        return
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    # A missing group deletes idempotently; a present non-group never does.
    try:
        detail = app.get_payload(ref.id)
        if detail.get("kind") != ref.kind:
            handler._json(404, {"error": "conversation not found"})
            return
    except Exception:
        pass
    try:
        app.delete_session(ref.id)
    except Exception as exc:
        from thyca.sessions.wire import delete_error

        mapped = delete_error(exc)
        if mapped is None:
            _conv_error(handler, exc)
            return
        handler._json(mapped[0], {"error": mapped[1].replace("session", "conversation")})
    else:
        handler._json(200, {"ok": True, "kind": "group", "group_id": ref.id})


# Add or remove one participant of one explicit group.
def group_bots(handler, app: "ChatApp | None", group_id: str) -> None:
    if _missing_chat(handler, app):
        return
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    if _require_kind(handler, app, ref) is None:
        return

    # Apply one membership change to this group only.
    def run(payload: dict) -> None:
        if _reject_legacy_id_fields(handler, payload):
            return
        bot_id = payload.get("bot_id")
        action = payload.get("action")
        if not isinstance(bot_id, str) or action not in ("add", "remove"):
            handler._json(400, {"error": "invalid body"})
            return
        try:
            bots = app.set_room_bots(ref.id, bot_id, action)
        except ValueError:
            handler._json(400, {"error": "invalid body"})
        except RegistryError:
            handler._json(404, {"error": "bot not found"})
        except Exception as exc:
            _conv_error(handler, exc)
        else:
            handler._json(200, {"ok": True, "kind": "group", "group_id": ref.id, "bots": bots})

    _with_json(handler, run)


# Resolve the door id for one turn route after kind validation.
def _turn_target(handler, app: "ChatApp", ref: ConversationRef) -> str | None:
    if ref.kind == "dm":
        try:
            app.bot_dm(ref.id)
        except RegistryError:
            handler._json(404, {"error": "bot not found"})
            return None
        except Exception as exc:
            _conv_error(handler, exc)
            return None
        return ref.id
    if _require_kind(handler, app, ref) is None:
        return None
    return ref.id


# Run one non-streaming turn in the addressed conversation.
def _conv_turn(handler, app: "ChatApp | None", ref: ConversationRef) -> None:
    from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText, TurnCancelled

    if _missing_chat(handler, app):
        return
    session_id = _turn_target(handler, app, ref)
    if session_id is None:
        return

    # Parse and run the turn for the already-validated door.
    def run(payload: dict) -> None:
        if _reject_legacy_id_fields(handler, payload):
            return
        try:
            text, model, effort, retry = parse_turn_body(payload)
        except InvalidTurnOption as exc:
            handler._json(400, {"error": str(exc)})
            return
        except InvalidTurnText as exc:
            _status, _code, message = public_turn_error(exc)
            handler._json(_status, {"error": message})
            return
        except ValueError as exc:
            _log_turn_failure(app, session_id, None, exc)
            handler._json(500, {"error": "chat unavailable"})
            return
        try:
            result = app.turn(session_id, text, model=model, effort=effort, retry=retry)
        except TurnCancelled:
            handler._json(200, {"cancelled": True})
        except Exception as exc:
            status, _code, message = public_turn_error(exc)
            _log_turn_failure(app, session_id, model, exc)
            handler._json(status, {"error": message.replace("session", "conversation")})
        else:
            handler._json(200, to_detail(result))

    _with_json(handler, run)


# Run one streaming turn in the addressed conversation.
def _conv_turn_stream(handler, app: "ChatApp | None", ref: ConversationRef) -> None:
    from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText

    if _missing_chat(handler, app):
        return
    session_id = _turn_target(handler, app, ref)
    if session_id is None:
        return

    # Parse the body, then pump the turn for the validated door.
    def run(payload: dict) -> None:
        if _reject_legacy_id_fields(handler, payload):
            return
        try:
            text, model, effort, retry = parse_turn_body(payload)
        except InvalidTurnOption as exc:
            handler._json(400, {"error": str(exc)})
            return
        except InvalidTurnText as exc:
            _status, _code, message = public_turn_error(exc)
            handler._json(_status, {"error": message})
            return
        except ValueError as exc:
            _log_turn_failure(app, session_id, None, exc)
            handler._json(500, {"error": "chat unavailable"})
            return
        stream_turn(
            handler, app, session_id, text, model=model, effort=effort, retry=retry,
            detail_transform=to_detail,
        )

    _with_json(handler, run, error="invalid text")


# Cancel the in-flight turn of the addressed conversation.
def _conv_turn_cancel(handler, app: "ChatApp | None", ref: ConversationRef) -> None:
    from thyca.app.chat_app import SessionIdle, TurnInFlight

    if _missing_chat(handler, app):
        return
    session_id = _turn_target(handler, app, ref)
    if session_id is None:
        return
    try:
        handler._read_body()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    try:
        app.cancel(session_id)
    except SessionIdle:
        handler._json(409, {"error": "conversation idle"})
    except TurnInFlight:
        handler._json(409, {"error": "turn in flight"})
    except Exception as exc:
        _conv_error(handler, exc)
    else:
        handler._json(200, {"ok": True})


# Follow the in-flight turn of the addressed conversation.
def _conv_turn_follow(handler, app: "ChatApp | None", ref: ConversationRef) -> None:
    if _missing_chat(handler, app):
        return
    session_id = _turn_target(handler, app, ref)
    if session_id is None:
        return
    try:
        hub = app.follow_hub(session_id)
    except Exception as exc:
        _conv_error(handler, exc)
        return
    if hub is None:
        handler._json(409, {"error": "conversation idle"})
        return
    items = hub.subscribe()
    try:
        pump_stream(
            handler, items, {"disconnected": False}, app=app, session_id=session_id,
            model="?", detail_transform=to_detail,
        )
    finally:
        hub.drop(items)


# Compact the addressed conversation for each of its participants.
def _conv_compact(handler, app: "ChatApp | None", ref: ConversationRef) -> None:
    from thyca.app.chat_app import InvalidTurnOption
    from thyca.sessions.summary import INSTRUCTIONS_MAX_CHARS

    if _missing_chat(handler, app):
        return
    session_id = _turn_target(handler, app, ref)
    if session_id is None:
        return

    # Validate the compact body, then compact the validated door.
    def run(payload: dict) -> None:
        if _reject_legacy_id_fields(handler, payload):
            return
        model = payload.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            handler._json(400, {"error": "invalid model"})
            return
        instructions = payload.get("instructions")
        if instructions is not None and (
            not isinstance(instructions, str) or len(instructions) > INSTRUCTIONS_MAX_CHARS
        ):
            handler._json(400, {"error": "invalid instructions"})
            return
        if isinstance(instructions, str) and not instructions.strip():
            instructions = None
        try:
            result = app.compact_session(session_id, model=model, instructions=instructions)
        except InvalidTurnOption as exc:
            handler._json(400, {"error": str(exc)})
        except Exception as exc:
            _conv_error(handler, exc)
        else:
            handler._json(200, to_detail(result))

    _with_json(handler, run)


# Decide one pending private follow-up inside its owner's DM.
def dm_private_followup(handler, app: "ChatApp | None", bot_id: str) -> None:
    if _missing_chat(handler, app):
        return
    try:
        ref = _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    if _turn_target(handler, app, ref) is None:
        return
    from thyca.app.private_followups import ConsentError, PrivateFollowupService

    # Pass only the consent fields to the follow-up service.
    def run(payload: dict) -> None:
        if _reject_legacy_id_fields(handler, payload):
            return
        try:
            out = PrivateFollowupService(app).decide(
                ref.id, payload.get("pending_id"), payload.get("revision"), payload.get("choice")
            )
        except ConsentError as exc:
            # Constant public text per status; service detail never leaks.
            _consent_text = {
                400: "invalid body",
                404: "private follow-up not found",
                409: "private follow-up conflict",
                503: "private follow-up unavailable",
            }.get(exc.status, "private follow-up unavailable")
            handler._json(exc.status, {"error": _consent_text})
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            handler._json(503, {"error": "chat unavailable"})
            return
        handler._json(200, out)

    _with_json(handler, run)


# Read one bot's operational view inside its own DM.
def dm_operations(handler, app: "ChatApp | None", bot_id: str, query: str) -> None:
    try:
        _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    bot_operations(handler, app, bot_id, bot_id, query)


# Read one bot's live tasks inside its own DM.
def dm_tasks(handler, app: "ChatApp | None", bot_id: str, query: str) -> None:
    try:
        _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    bot_tasks(handler, app, bot_id, bot_id, query)


# Read one bot's operational view inside one explicit group.
def group_operations(handler, app: "ChatApp | None", bot_id: str, group_id: str, query: str) -> None:
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    bot_operations(handler, app, bot_id, ref.id, query)


# Read one bot's live tasks inside one explicit group.
def group_tasks(handler, app: "ChatApp | None", bot_id: str, group_id: str, query: str) -> None:
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    bot_tasks(handler, app, bot_id, ref.id, query)


# Run one DM turn for the bot named in the path.
def dm_turn(handler, app: "ChatApp | None", bot_id: str) -> None:
    try:
        ref = _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    _conv_turn(handler, app, ref)


# Stream one DM turn for the bot named in the path.
def dm_turn_stream(handler, app: "ChatApp | None", bot_id: str) -> None:
    try:
        ref = _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    _conv_turn_stream(handler, app, ref)


# Cancel the DM turn for the bot named in the path.
def dm_turn_cancel(handler, app: "ChatApp | None", bot_id: str) -> None:
    try:
        ref = _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    _conv_turn_cancel(handler, app, ref)


# Follow the DM turn for the bot named in the path.
def dm_turn_follow(handler, app: "ChatApp | None", bot_id: str) -> None:
    try:
        ref = _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    _conv_turn_follow(handler, app, ref)


# Compact the DM of the bot named in the path.
def dm_compact(handler, app: "ChatApp | None", bot_id: str) -> None:
    try:
        ref = _dm_ref(bot_id)
    except ValueError:
        handler._json(404, {"error": "bot not found"})
        return
    _conv_compact(handler, app, ref)


# Run one group turn for the group named in the path.
def group_turn(handler, app: "ChatApp | None", group_id: str) -> None:
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    _conv_turn(handler, app, ref)


# Stream one group turn for the group named in the path.
def group_turn_stream(handler, app: "ChatApp | None", group_id: str) -> None:
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    _conv_turn_stream(handler, app, ref)


# Cancel the group turn for the group named in the path.
def group_turn_cancel(handler, app: "ChatApp | None", group_id: str) -> None:
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    _conv_turn_cancel(handler, app, ref)


# Follow the group turn for the group named in the path.
def group_turn_follow(handler, app: "ChatApp | None", group_id: str) -> None:
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    _conv_turn_follow(handler, app, ref)


# Compact the group named in the path.
def group_compact(handler, app: "ChatApp | None", group_id: str) -> None:
    try:
        ref = _group_ref(group_id)
    except ValueError:
        handler._json(404, {"error": "conversation not found"})
        return
    _conv_compact(handler, app, ref)
