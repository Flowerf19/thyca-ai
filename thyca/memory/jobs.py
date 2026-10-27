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

from thyca.core.protocol import Message
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
    # Phase 1C task/habit linkage (all optional; sanitized on parse).
    task: str | None = None
    # Title of a genuinely new multi-step goal (NOT an id); the apply
    # step mints one shared id per title group.
    new_task: str | None = None
    cont: str | None = None
    kind: str | None = None
    status: str | None = None
    habit: str | None = None
    # Phase 1E: the user explicitly stated this habit (not inferred).
    habit_explicit: bool = False


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


def _open_tasks_block(open_tasks) -> str:
    """Prompt block listing open memory tasks for task-linking."""
    lines = []
    for entry in open_tasks or ():
        if not isinstance(entry, dict):
            continue
        task_id = entry.get("task_id")
        if not task_id:
            continue
        lines.append(
            f"- {task_id} — {entry.get('title', '')} "
            f"(latest entry {entry.get('latest_session_id', '?')}, "
            f"last active {entry.get('last_day', '?')})"
        )
    if not lines:
        return "OPEN MEMORY TASKS: none."
    return "OPEN MEMORY TASKS:\n" + "\n".join(lines)


async def extract_from_transcript(connect, model: str, transcript: str,
                                   open_tasks=()) -> list[ExtractedMemory]:
    """Ask the model to list durable memories in a strict JSON schema.

    ``open_tasks`` is an iterable of ``facade.tasks(status="open")``
    dicts so the model reuses task ids instead of opening duplicates.
    Any model failure (or unparseable reply) yields an empty list: the job
    is best-effort and must never break the turn or the compaction that
    follows it.
    """
    prompt = (
        "You extract durable memories from a chat transcript for a personal "
        "assistant's notebook. Reply with a JSON array only, no prose. Each item: "
        '{"topic": "...", "summary": "...", "content": "...", "importance": 1-5, '
        '"bots": ["<bot-id>"] or null, '
        '"task": "<task_id from OPEN MEMORY TASKS>" or null, '
        '"new_task": "<new goal title>" or null, '
        '"cont": "<latest_session_id of the continued task>" or null, '
        '"kind": "habit" or null, '
        '"status": "open"|"done"|null, '
        '"habit": "<short label>" or null, '
        '"habit_explicit": true|false}. '
        "bots names the single bot this memory belongs to (its private notebook); "
        "null means general/user-level. "
        "Link a memory to a task only when it is one step of an ongoing effort; "
        "when it matches an open task below you MUST reuse that id, never open "
        "a duplicate. new_task only when a genuinely new multi-step goal starts. "
        'status="done" only when the transcript says the WHOLE goal is finished, '
        "not when one step is done. "
        'kind="habit" only for a repeated habit/preference/working style, with a '
        'short habit label in the content\'s own language (e.g. "uong tra buoi sang"); '
        "a one-off event is never a habit. "
        '"habit_explicit": true only when the user themselves states a lasting '
        'habit/preference in the transcript ("I usually/always/like..."); '
        "false for behavior-inferred observations. "
        "Skip small talk, transient plans, and anything "
        "already phrased as a memory entry. Empty array when nothing is worth keeping.\n\n"
        f"{_open_tasks_block(open_tasks)}\n\n"
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
    """Parse the extractor's JSON array; invalid items are dropped.

    Phase-1C side fields are sanitized individually: a bad side field
    never drops the memory itself. The six side fields are validated as
    one effective set; when invalid, all six are dropped and the core
    topic/summary/... item is kept.``task`` + ``new_task`` together drop
    both (ambiguous intent)."""
    from thyca.memory.heading import validate_task_meta

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
        task = item.get("task")
        new_task = item.get("new_task")
        cont = item.get("cont")
        kind = item.get("kind")
        status = item.get("status")
        habit = item.get("habit")
        task = task if isinstance(task, str) and task.strip() else None
        new_task = new_task.strip() if isinstance(new_task, str) and new_task.strip() else None
        if task is not None and new_task is not None:
            task = new_task = None
        cont = cont if isinstance(cont, str) and cont.strip() else None
        kind = kind if isinstance(kind, str) and kind.strip() else None
        # Checkpoints are deliberate in-turn memory writes, never an
        # extractor output; a checkpoint kind here keeps only its task link.
        if kind == "checkpoint":
            kind = None
        status = status if isinstance(status, str) and status.strip() else None
        habit = habit.strip() if isinstance(habit, str) and habit.strip() else None
        habit_explicit = item.get("habit_explicit") is True
        # A new goal's id is minted at apply time; validate the rest of
        # the combination against a probe id so value/shape errors are
        # still caught without losing the new-task intent.
        probe = task if task is not None else (
            "task-00000000" if new_task is not None else None
        )
        try:
            validate_task_meta(probe, cont, kind, status, habit, habit_explicit)
        except ValueError:
            task = new_task = cont = kind = status = habit = None
            habit_explicit = False
        out.append(
            ExtractedMemory(
                topic=topic.strip(),
                summary=summary.strip(),
                content=content if isinstance(content, str) else "",
                importance=importance if importance in (1, 2, 3, 4, 5) else 3,
                bots=tuple(bots) if isinstance(bots, list) and bots else None,
                task=task,
                new_task=new_task,
                cont=cont,
                kind=kind,
                status=status,
                habit=habit,
                habit_explicit=habit_explicit,
            )
        )
    return out


def apply_extracted(facade, items: list[ExtractedMemory], *, default_bot: str = "thyca",
                    open_tasks=(), chat: str | None = None) -> dict:
    """Write extracted memories as the sidecar (Thyca privileges).

    Per-bot entries land in that bot's notebook; shared knowledge is the
    profile aggregation turn's job, so nothing is proposed or queued here.
    Task linkage: an explicit ``task`` is honored only when present in
    ``open_tasks`` (the catalog passed in); anything else is treated as
    a hallucinated id and dropped with its status/cont. ``new_task``
    titles group case-insensitively within one apply: one minted id per
    group, later entries chaining ``cont`` onto the previously written
    entry of the same task. Returns ``{"written": [...sids]}``.
    """
    catalog = [entry for entry in (open_tasks or ()) if isinstance(entry, dict)]
    known = {str(entry["task_id"]): entry for entry in catalog if entry.get("task_id")}
    minted: dict[str, str] = {}
    last_written: dict[str, str] = {}
    written: list[str] = []
    token = bind_bot_id("thyca")
    try:
        for item in items:
            owners: list[str] | None = list(item.bots) if item.bots else [default_bot]
            task = item.task
            cont = item.cont
            status = item.status
            if task is not None and task not in known:
                task = cont = status = None
            if task is None and item.new_task is not None:
                # Group key: strip + collapse inner whitespace + casefold,
                # so "Fix  Sink " and "fix sink" mint one id.
                key = " ".join(item.new_task.strip().split()).casefold()
                if key not in minted:
                    minted[key] = facade.new_task_id()
                task = minted[key]
                if status is None:
                    status = "open"
            if task is not None:
                if cont is None:
                    cont = last_written.get(task)
                    if cont is None and task in known:
                        cont = known[task].get("latest_session_id")
            sid = facade.remember(
                item.topic, item.summary, content=item.content,
                importance=item.importance, bots=owners, chat=chat,
                task=task, cont=cont, kind=item.kind,
                status=status, habit=item.habit,
                habit_explicit=item.habit_explicit,
            )
            written.append(sid)
            if task is not None:
                last_written[task] = sid
    finally:
        reset_bot_id(token)
    return {"written": written}
