"""Merge funnel (BE-4): exact-hash → lexical candidates → LLM review → edges.

Merging never deletes entries: it links clusters with ``related`` edges
(Union-Find over the edge graph) and reports the bot-union per cluster.
Applying a union to entries is an explicit Thyca-privileged call — never
automatic — so the user can veto any ownership change.

The LLM reviewer is injected (no heuristic verdicts ship here, per the
master plan's ban on undecided heuristics). The funnel calls it only when
lexical candidates exist; with no candidates there is no LLM call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from thyca.core.protocol import Message
from thyca.memory.identity import bind_bot_id, reset_bot_id

VERDICTS = ("trùng", "liên quan", "không")


class UnionFind:
    """Disjoint clusters over memory session ids."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, node: str) -> str:
        parent = self._parent.setdefault(node, node)
        while self._parent[parent] != parent:
            self._parent[parent] = self._parent[self._parent[parent]]
            parent = self._parent[parent]
        return parent

    def union(self, left: str, right: str) -> None:
        lroot, rroot = self.find(left), self.find(right)
        if lroot != rroot:
            self._parent[rroot] = lroot

    def clusters(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for node in list(self._parent):
            groups.setdefault(self.find(node), []).append(node)
        return {root: sorted(members) for root, members in sorted(groups.items())}


@dataclass(frozen=True)
class ClusterReport:
    root: str
    members: list[str]
    bots_union: list[str]


def normalize(text: str) -> str:
    from thyca.memory.chunk import Chunker

    return Chunker.normalize(text)


def exact_dup_sessions(facade, session_id: str) -> list[str]:
    """Session ids with a leaf normalizing exactly like one of ours.

    The hard-dup stage of the funnel: same ``text_norm`` on any leaf pair
    means same words modulo case/accents — no LLM needed to notice.
    Only daily entries participate; canonical profile leaves never dup."""
    from thyca.memory.heading import format_ts, utc_now

    own_rows = facade.archive.store.get_session_any(session_id)
    own_norms = {
        normalize(str(row["text_raw"]))
        for row in own_rows
        if str(row["source_kind"]) == "daily"
    }
    own_norms.discard("")
    if not own_norms:
        return []
    now_ts = format_ts(utc_now(None))
    found = []
    for row in facade.archive.store.visible_chunk_maps(now_ts):
        if row.get("source_kind") != "daily":
            continue
        if str(row.get("session_id", "")) == session_id:
            continue
        if normalize(str(row.get("text_raw", ""))) in own_norms:
            sid = str(row.get("session_id", ""))
            if sid and sid not in found:
                found.append(sid)
    return sorted(found)


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


def apply_review(facade, a_sid: str, b_sid: str, verdict: str, *, decided_by: str = "thyca") -> bool:
    """Link a reviewed pair; True when an edge was recorded."""
    from thyca.memory.heading import format_ts, utc_now

    if verdict not in ("trùng", "liên quan"):
        return False
    token = bind_bot_id("thyca")
    try:
        now_ts = format_ts(utc_now(None))
        return facade.archive.store.add_related(
            a_sid, b_sid,
            f"{verdict} (reviewed by {decided_by})", now_ts,
        )
    finally:
        reset_bot_id(token)


async def run_merge_pass(facade, session_ids: list[str], reviewer) -> dict:
    """One funnel pass over fresh session ids (Thyca sidecar privileges).

    ``reviewer(a_text, b_text)`` is awaited only for pairs with a lexical
    candidate; exact dups link without any model call. Returns
    ``{"linked": [[a, b, verdict]...], "review_calls": n}``.
    """
    token = bind_bot_id("thyca")
    try:
        return await _run_merge_pass(facade, session_ids, reviewer)
    finally:
        reset_bot_id(token)


async def _run_merge_pass(facade, session_ids: list[str], reviewer) -> dict:
    from thyca.memory.heading import format_ts, utc_now

    linked: list[list[str]] = []
    review_calls = 0
    now_ts = format_ts(utc_now(None))
    for sid in session_ids:
        try:
            text = facade.get(session_id=sid)
        except Exception:
            continue
        for dup in exact_dup_sessions(facade, sid):
            if facade.archive.store.add_related(sid, dup, "trùng (exact hash)", now_ts):
                linked.append([sid, dup, "trùng"])
        try:
            result = facade.search(text[:500], limit=5)
        except Exception:
            continue
        seen = {sid}
        for hit in result.hits:
            if hit.session_id in seen:
                continue
            seen.add(hit.session_id)
            try:
                other = facade.get(session_id=hit.session_id)
            except Exception:
                continue
            review_calls += 1
            try:
                verdict = await reviewer(text[:2000], other[:2000])
            except Exception:
                continue
            if verdict in ("trùng", "liên quan"):
                if facade.archive.store.add_related(
                    sid, hit.session_id, f"{verdict} (reviewed)", now_ts
                ):
                    linked.append([sid, hit.session_id, verdict])
    return {"linked": linked, "review_calls": review_calls}


def cluster_report(facade) -> list[ClusterReport]:
    """Union-Find clusters over all ``related`` edges + bot union per cluster."""
    token = bind_bot_id("thyca")
    try:
        edges = facade.archive.store.all_related()
        union = UnionFind()
        nodes: set[str] = set()
        for left, right in edges:
            union.union(left, right)
            nodes.add(left)
            nodes.add(right)
        reports = []
        for root, members in union.clusters().items():
            bots: set[str] = set()
            for member in members:
                owners = facade.archive.store.session_bots(member)
                if owners:
                    bots.update(owners)
                else:
                    bots.add("team")
            reports.append(
                ClusterReport(root=root, members=members, bots_union=sorted(bots))
            )
        # Singleton sessions (no edges) are their own cluster of one.
        return reports
    finally:
        reset_bot_id(token)


def apply_cluster_union(facade, members: list[str]) -> int:
    """Rewrite every member's owners to the cluster's bot union (Thyca only).

    Explicit opt-in primitive: the funnel reports, Thyca (or the user)
    applies. Returns the number of entries rewritten.
    """
    from dataclasses import replace as _replace

    from thyca.memory.identity import current_bot_id as _bot

    reader = _bot()
    if reader is not None and reader != "thyca":
        from thyca.memory.archive_store import ArchiveError

        raise ArchiveError(f"bot '{reader}' cannot apply a cluster union")
    owners: set[str] = set()
    for member in members:
        current = facade.archive.store.session_bots(member)
        if current:
            owners.update(current)
        else:
            owners.add("team")
    if not owners:
        return 0
    union = tuple(sorted(owners))
    rewritten = 0
    with facade.writer.mutation_lock():
        for member in members:
            try:
                path, entry = facade.writer.locate(member)
                facade.writer.map_heading(
                    path, entry, lambda meta: _replace(meta, bots=union)
                )
                rewritten += 1
            except Exception:
                continue
        facade._refresh_index()
    return rewritten


def encode_report(reports: list[ClusterReport]) -> str:
    return json.dumps(
        [
            {"root": rep.root, "members": rep.members, "bots_union": rep.bots_union}
            for rep in reports
        ],
        ensure_ascii=False,
    )
