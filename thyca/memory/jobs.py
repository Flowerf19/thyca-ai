"""Thyca memory sidecar job (BE-4): extract → dedupe → write / propose.

Not a separate bot: a hook surface the app layer calls after a task is
done and when a session idles. Extraction runs *before* any compaction so
the transcript is still intact; the caller compacts after this returns.
The LLM reviewer is injected (provider in prod, stub in tests) — no
thresholds or keyword rules ship here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from thyca.core.protocol import Message
from thyca.memory.candidates import CandidateQueue
from thyca.memory.identity import bind_bot_id, reset_bot_id

#: Backend idle trigger (master §4): a session quiet this long gets its
#: memories extracted before any compaction. Technical default, recorded here.
IDLE_SWEEP_SECONDS = 15 * 60


@dataclass(frozen=True)
class ExtractedMemory:
    topic: str
    summary: str
    content: str = ""
    importance: int = 3
    bots: tuple[str, ...] | None = None
    team_proposal: bool = False


def should_idle_sweep(last_activity_ts: str, now_ts: str, idle_s: int = IDLE_SWEEP_SECONDS) -> bool:
    """Pure idle decision: True when the session quieted past ``idle_s``."""
    from thyca.core.protocol import parse_ts

    try:
        last = parse_ts(last_activity_ts)
        now = parse_ts(now_ts)
    except ValueError:
        return False
    return (now - last).total_seconds() >= idle_s


def transcript_tail(messages: list[Message], max_chars: int = 12000) -> str:
    """Newest-first-capped text of user/assistant rows for extraction."""
    lines = []
    for message in messages:
        if message.role not in ("user", "assistant"):
            continue
        if (message.meta or {}).get("kind") in ("naming", "compaction"):
            continue
        text = message.content or ""
        if text.strip():
            lines.append(f"[{message.role} {message.ts}] {text}")
    out = "\n".join(lines)
    return out if len(out) <= max_chars else out[-max_chars:]


async def extract_from_transcript(connect, model: str, transcript: str) -> list[ExtractedMemory]:
    """Ask the model to list durable memories in a strict JSON schema.

    Any model failure (or unparseable reply) yields an empty list: the job
    is best-effort and must never break the turn or the compaction that
    follows it.
    """
    prompt = (
        "You extract durable memories from a chat transcript for a personal "
        "assistant's notebook. Reply with a JSON array only, no prose. Each item: "
        '{"topic": "...", "summary": "...", "content": "...", "importance": 1-5, '
        '"bots": ["<bot-id>"] or null, "team_proposal": true|false}. '
        "bots names the single bot this memory belongs to (its private notebook); "
        "null means general/user-level. team_proposal=true only when the whole "
        "team should remember it. Skip small talk, transient plans, and anything "
        "already phrased as a memory entry. Empty array when nothing is worth keeping.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )
    try:
        reply = await connect.chat(
            [Message(role="user", content=prompt)], None
        )
    except Exception:
        return []
    return parse_extraction(getattr(reply, "content", None) or "")


def parse_extraction(text: str) -> list[ExtractedMemory]:
    """Parse the extractor's JSON array; invalid items are dropped."""
    try:
        data = json.loads(text.strip())
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        topic = item.get("topic")
        summary = item.get("summary")
        if not isinstance(topic, str) or not topic.strip():
            continue
        if not isinstance(summary, str) or not summary.strip():
            continue
        content = item.get("content", "")
        importance = item.get("importance", 3)
        bots = item.get("bots")
        team = item.get("team_proposal", False)
        out.append(
            ExtractedMemory(
                topic=topic.strip(),
                summary=summary.strip(),
                content=content if isinstance(content, str) else "",
                importance=importance if importance in (1, 2, 3, 4, 5) else 3,
                bots=tuple(bots) if isinstance(bots, list) and bots else None,
                team_proposal=team is True,
            )
        )
    return out


def apply_extracted(facade, items: list[ExtractedMemory], *, default_bot: str = "thyca") -> dict:
    """Write extracted memories as the sidecar (Thyca privileges).

    Per-bot entries land in that bot's notebook; team-worthy ones are
    written private first and then proposed through the candidate queue —
    the job never writes ``\"team\"`` directly, mirroring the bot rule.
    Returns ``{"written": [...sids], "proposed": [...candidate ids]}``.
    """
    written: list[str] = []
    proposed: list[str] = []
    token = bind_bot_id("thyca")
    try:
        queue = CandidateQueue(facade.thyca_dir)
        for item in items:
            owners: list[str] | None = list(item.bots) if item.bots else [default_bot]
            sid = facade.remember(
                item.topic, item.summary, content=item.content,
                importance=item.importance, bots=owners,
            )
            written.append(sid)
            if item.team_proposal:
                proposed.append(queue.propose(owners[0], sid).id)
    finally:
        reset_bot_id(token)
    return {"written": written, "proposed": proposed}


def _utc_ts(moment: datetime | None = None) -> str:
    now = moment or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
