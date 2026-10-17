"""Duplicate-fact detection → team proposals (phase 1B, replaces merge).

No Union-Find, no owner unions, no edge graph: when Thyca's job finds
two PRIVATE memories of different bots stating the same fact, it files a
proposal to promote the original fact to ``team`` through the existing
``CandidateQueue`` (Thyca/user approves as before). Only a ``trùng``
verdict proposes; ``liên quan`` does nothing. Entries are never edited,
merged, or deleted here.

The LLM reviewer is injected (no heuristic verdicts ship here). It is
called only for lexical candidates; exact dups propose without a call.
"""
from __future__ import annotations

from thyca.core.protocol import Message
from thyca.memory.identity import bind_bot_id, reset_bot_id

VERDICTS = ("trùng", "liên quan", "không")


async def llm_review_pair(connect, model: str, a_text: str, b_text: str) -> str:
    """One LLM verdict for a candidate pair; anything unclear → ``\"không\"``."""
    prompt = (
        "Two memory notes follow. Reply with exactly one word: "
        "TRÙNG (same fact), LIÊN QUAN (related but distinct), or KHÔNG (unrelated). "
        "No other text.\n\nA:\n" + a_text[:2000] + "\n\nB:\n" + b_text[:2000]
    )
    try:
        reply = await connect.chat([Message(role="user", content=prompt)], None)
    except Exception:
        return "không"
    verdict = (getattr(reply, "content", None) or "").strip().lower()
    if "trùng" in verdict or "trung" in verdict:
        return "trùng"
    if "liên quan" in verdict or "lien quan" in verdict:
        return "liên quan"
    return "không"


def normalize(text: str) -> str:
    from thyca.memory.chunk import Chunker

    return Chunker.normalize(text)


def _is_daily_session(facade, session_id: str) -> bool:
    rows = facade.archive.store.get_session_any(session_id)
    if rows:
        return str(rows[0]["source_kind"]) == "daily"
    prefix = session_id.split("#", 1)[0]
    return not (
        session_id.startswith("canonical#")
        or session_id.startswith("memory#")
        or len(prefix) != 10
    )


def _owners(facade, session_id: str) -> tuple[str, ...] | None:
    """Owner list with today's-file fallback (store misses unindexed days)."""
    return facade._lookup_bots(session_id, None)


def pair_eligible(facade, new_sid: str, cand_sid: str) -> bool:
    """True when a (new, candidate) pair may yield a team proposal.

    Both must be daily private entries of different owner-sets, where the
    fact is not shared widely enough yet. Team-visible entries (legacy
    None, ``"team"``) need nothing; Thyca-only entries never go through
    the queue (Thyca writes team directly)."""
    if new_sid == cand_sid:
        return False
    if not _is_daily_session(facade, new_sid):
        return False
    if not _is_daily_session(facade, cand_sid):
        return False
    new_owners = _owners(facade, new_sid)
    cand_owners = _owners(facade, cand_sid)
    for owners in (new_owners, cand_owners):
        if owners is None or "team" in owners:
            return False
        if owners and all(owner == "thyca" for owner in owners):
            return False
    if not new_owners or not cand_owners:
        return False
    # Same owner-set, or no bot on either side outside the other: the
    # fact is already shared widely enough.
    if not (set(new_owners) ^ set(cand_owners)):
        return False
    return True


def _daily_leaf_texts(facade, session_id: str) -> list[str]:
    """Leaf bodies of one daily session, store first, day file fallback.

    The store misses unindexed (today) sessions; the markdown file is the
    same source the chunker reads, so both paths agree leaf-for-leaf."""
    from thyca.memory.heading import read_text_file

    rows = [
        row for row in facade.archive.store.get_session_any(session_id)
        if str(row["source_kind"]) == "daily"
    ]
    if rows:
        ordered = sorted(rows, key=lambda row: int(row["leaf_ord"]))
        return [str(row["text_raw"]) for row in ordered]
    day = session_id.split("#", 1)[0]
    try:
        text = read_text_file(facade.thyca_dir / "memory" / f"{day}.md")
    except (OSError, UnicodeDecodeError):
        return []
    if not text:
        return []
    chunks = facade.archive.chunker.chunk_markdown(
        str(facade.thyca_dir / "memory" / f"{day}.md"),
        text,
        source_kind="daily",
        timeline_day=day,
    )
    return [
        chunk.text_raw for chunk in chunks if chunk.session_id == session_id
    ]


def exact_duplicate_sessions(facade, session_id: str) -> list[str]:
    """Session ids whose whole normalized text equals ours (daily only).

    Full-session comparison (not per-leaf): short leaves like "done"
    must not match across unrelated sessions."""
    from thyca.memory.heading import format_ts, utc_now

    try:
        own_leaves = _daily_leaf_texts(facade, session_id)
    except Exception:
        return []
    own_norm = normalize("\n".join(own_leaves))
    if not own_norm:
        return []
    from collections import defaultdict

    now_ts = format_ts(utc_now(None))
    by_session: dict[str, list] = defaultdict(list)
    for row in facade.archive.store.visible_chunk_maps(now_ts):
        if row.get("source_kind") != "daily":
            continue
        by_session[str(row.get("session_id", ""))].append(row)
    found = []
    for sid, rows in by_session.items():
        if sid == session_id:
            continue
        ordered = sorted(rows, key=lambda row: int(row.get("leaf_ord", 0)))
        text = normalize(
            "\n".join(str(row.get("text_raw", "")) for row in ordered)
        )
        if text and text == own_norm and sid not in found:
            found.append(sid)
    return sorted(found)


def find_duplicate_candidates(facade, session_id: str) -> list[str]:
    """Session ids that may duplicate one fresh entry (phase 1B).

    Two stages: exact whole-session normalized-text match, then lexical
    candidates via ``facade.search`` (limit 5, self excluded). Pair
    eligibility is NOT applied here — the caller filters before acting.
    """
    from thyca.memory.heading import format_ts, utc_now

    try:
        own_text = facade.get(session_id=session_id)
    except Exception:
        return []
    found = exact_duplicate_sessions(facade, session_id)
    try:
        result = facade.search(own_text[:500], limit=5)
    except Exception:
        return sorted(found)
    for hit in result.hits:
        if hit.session_id == session_id:
            continue
        if str(hit.source_kind) != "daily":
            continue
        if hit.session_id not in found:
            found.append(hit.session_id)
    return sorted(found)


def _order_key(facade, session_id: str) -> tuple[str, int] | None:
    rows = [
        row
        for row in facade.archive.store.get_session_any(session_id)
        if str(row["source_kind"]) == "daily"
    ]
    if not rows:
        return None
    days = [str(row["timeline_day"]) for row in rows if row["timeline_day"]]
    if not days:
        return None
    return (min(days), min(int(row["line_start"]) for row in rows))


def _propose_target(facade, new_sid: str, cand_sid: str) -> str:
    """The older entry of a pair; the non-new one when indistinguishable."""
    new_key = _order_key(facade, new_sid)
    cand_key = _order_key(facade, cand_sid)
    if new_key is not None and cand_key is not None and new_key != cand_key:
        return new_sid if new_key < cand_key else cand_sid
    return cand_sid


def _proposer_bot_id(facade, target_sid: str) -> str | None:
    owners = _owners(facade, target_sid)
    if not owners:
        return None
    for owner in sorted(owners):
        if owner not in ("thyca", "team"):
            return owner
    return None


async def propose_duplicates(facade, session_ids: list[str], reviewer, queue=None) -> dict:
    """One dedupe pass over fresh session ids (Thyca sidecar privileges).

    Exact dups propose without a model call; lexical candidates go
    through ``reviewer`` and only a ``"trùng"`` verdict proposes. The
    proposed entry is the older one of each pair. Returns
    ``{"proposed": [session_id...], "review_calls": n}``.
    """
    from thyca.memory.candidates import CandidateQueue

    if queue is None:
        queue = CandidateQueue(facade.thyca_dir)
    token = bind_bot_id("thyca")
    try:
        return await _propose_duplicates(facade, session_ids, reviewer, queue)
    finally:
        reset_bot_id(token)


async def _propose_duplicates(facade, session_ids: list[str], reviewer, queue) -> dict:
    proposed: list[str] = []
    proposed_here: set[str] = set()
    already = {c.session_id for c in queue.list_all()}
    review_calls = 0
    for sid in session_ids:
        try:
            own_text = facade.get(session_id=sid)
        except Exception:
            continue
        try:
            exact = set(exact_duplicate_sessions(facade, sid))
        except Exception:
            exact = set()
        for cand in find_duplicate_candidates(facade, sid):
            if not pair_eligible(facade, sid, cand):
                continue
            target = _propose_target(facade, sid, cand)
            if target in already or target in proposed_here:
                continue
            if cand not in exact:
                # Lexical candidate: needs a "trùng" verdict first.
                try:
                    other = facade.get(session_id=cand)
                except Exception:
                    continue
                review_calls += 1
                try:
                    verdict = await reviewer(own_text[:2000], other[:2000])
                except Exception:
                    continue
                if verdict != "trùng":
                    continue
            # Else: exact whole-session match — propose with no LLM call.
            bot_id = _proposer_bot_id(facade, target)
            if bot_id is None:
                continue
            try:
                queue.propose(bot_id, target)
            except Exception:
                continue
            proposed.append(target)
            proposed_here.add(target)
            already.add(target)
    return {"proposed": proposed, "review_calls": review_calls}
