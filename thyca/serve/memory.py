"""Memory POST endpoints for the webui server (split from serve.py)."""
from __future__ import annotations

from thyca.memory.archived import ArchiveError
from thyca.memory.candidates import CandidateError, CandidateQueue
from thyca.memory.facade import MemoryFacade


def candidates_list(facade: MemoryFacade) -> tuple[int, dict]:
    """``GET /api/memory/candidates`` — pending team-memory proposals."""
    try:
        queue = CandidateQueue(facade.thyca_dir)
        items = [
            {
                "id": cand.id,
                "bot_id": cand.bot_id,
                "session_id": cand.session_id,
                "status": cand.status,
                "created_at": cand.created_at,
                "decided_by": cand.decided_by,
                "decided_at": cand.decided_at,
            }
            for cand in queue.list_pending()
        ]
    except CandidateError:
        return 503, {"error": "candidates unavailable"}
    except Exception:
        return 503, {"error": "candidates unavailable"}
    return 200, {"candidates": items}


def candidate_decide(facade: MemoryFacade, candidate_id: str, approved: bool, decided_by: str) -> tuple[int, dict]:
    """``POST /api/memory/candidates/<id>/approve|reject`` (user path).

    Approve promotes the entry to team memory with a stable id; reject
    leaves no residue. Both record ``decided_by`` for the audit trail."""
    try:
        queue = CandidateQueue(facade.thyca_dir)
        pending = {cand.id: cand for cand in queue.list_pending()}
        candidate = pending.get(candidate_id)
        if candidate is None:
            return 404, {"error": "candidate not found"}
        # One ordering for every caller: record the decision first
        # (with the caller-supplied decided_by; the HTTP user route
        # defaults it to "user"), then promote on approve. A failed
        # promotion reopens the candidate so queue state never claims
        # a promotion that did not happen.
        queue.decide(candidate_id, approved, decided_by)
        if approved:
            try:
                facade.promote_to_team(candidate.session_id)
            except ArchiveError:
                queue.reopen(candidate_id)
                return 404, {"error": "entry not found"}
            except Exception:
                queue.reopen(candidate_id)
                return 503, {"error": "promote failed"}
    except CandidateError as exc:
        return 404, {"error": str(exc)}
    except Exception:
        return 503, {"error": "decision failed"}
    return 200, {"ok": True, "id": candidate_id,
                  "status": "approved" if approved else "rejected"}


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
