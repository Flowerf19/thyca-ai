"""Memory POST endpoints for the webui server (split from serve.py)."""
from __future__ import annotations

from thyca.memory.archived import ArchiveError
from thyca.memory.facade import MemoryFacade


def memory_endpoint(facade: MemoryFacade, kind: str, payload: dict) -> tuple[int, dict]:
    """One ``POST /api/memory/*`` action → ``(status, body)``.

    Shared body-parse/session-id handling lives in the handler; this carries
    only the per-kind facade call and its error mapping.
    """
    if kind == "canonical":
        name = payload.get("name")
        content = payload.get("content")
        if not isinstance(name, str) or not isinstance(content, str):
            return 400, {"error": "invalid body"}
        try:
            facade.write_canonical(name, content)
        except ArchiveError as exc:
            return 400, {"error": str(exc)}
        except Exception:
            return 503, {"error": "write failed"}
        return 200, {"ok": True}

    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid.strip():
        return 400, {"error": "invalid session_id"}
    sid = sid.strip()
    try:
        if kind == "forget":
            facade.forget(sid)
            return 200, {"ok": True}
        if kind == "reinforce":
            importance = payload.get("importance")
            try:
                importance_value = int(importance) if importance is not None else None
            except (TypeError, ValueError):
                return 400, {"error": "invalid importance"}
            expires = facade.reinforce(sid, importance_value)
            return 200, {"ok": True, "expires_at": expires}
        # kind == "update": pass through; MemoryFacade.update owns validation.
        topic = payload.get("topic")
        summary = payload.get("summary")
        content = payload.get("content")
        if topic is None and summary is None:
            return 400, {"error": "nothing to update"}
        facade.update(sid, topic=topic, summary=summary, content=content)
        return 200, {"ok": True}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except ArchiveError:
        return 404, {"error": "session not found"}
    except Exception:
        return 503, {"error": f"{kind} failed"}
