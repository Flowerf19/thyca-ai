"""WorkItem projection (WS-3): unified view over runtime + memory tasks.

Canonical ``work_id`` is the memory ``task-<8 hex>`` id when a link
exists; runtime-only rows keep ``work_id=None`` and expose their runtime
key as a plain execution ref — never promoted to canonical. Rows merge
only on an explicit ``OpenTask.memory_task_id`` link, never by title
inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thyca.bots.tasks import OpenTask
from thyca.core.channel import WORK_REF_RE


@dataclass(frozen=True)
class WorkItem:
    work_id: str | None = None
    memory_task_id: str | None = None
    runtime_refs: tuple[str, ...] = ()
    title: str | None = None
    latest_session_id: str | None = None
    owner_bot: str | None = None
    coordinator: str | None = None
    origin_window: str | None = None
    current_window: str | None = None
    status: str = "open"
    waiting_on: str | None = None
    visibility: str | None = None
    share_policy: str | None = None
    latest_checkpoint_entry: str | None = None
    pending_private_id: str | None = None
    # Source rows the projection rendered from (never duplicated).
    runtime_task: Any = None
    memory_row: Any = None


def project_work_items(
    open_tasks: list[OpenTask],
    memory_rows: list[dict],
    *,
    owner_bot: str | None = None,
) -> list[WorkItem]:
    """Merge runtime open tasks with the memory-task catalog.

    A runtime row merges only via an explicit ``memory_task_id`` link to
    a catalog ``task_id``; otherwise it stays a runtime-only WorkItem.
    ``memory_rows`` are facade ``tasks()`` entries (``task_id``, ``title``,
    ``status``, ``latest_session_id``)."""
    items: list[WorkItem] = []
    for row in memory_rows:
        task_id = row.get("task_id") if isinstance(row, dict) else None
        if not isinstance(task_id, str) or not WORK_REF_RE.fullmatch(task_id):
            continue
        items.append(WorkItem(
            work_id=task_id,
            memory_task_id=task_id,
            owner_bot=owner_bot,
            title=row.get("title"),
            latest_session_id=row.get("latest_session_id"),
            status=str(row.get("status") or "open"),
            memory_row=row,
        ))
    for task in open_tasks:
        linked_id = getattr(task, "memory_task_id", None)
        target = None
        if isinstance(linked_id, str) and WORK_REF_RE.fullmatch(linked_id):
            target = next(
                (item for item in items if item.work_id == linked_id), None)
        if target is not None:
            items[items.index(target)] = WorkItem(
                **{**target.__dict__,
                   "runtime_refs": target.runtime_refs + (task.key or task.task,),
                   "current_window": task.room or target.current_window,
                   "waiting_on": task.waiting_on or target.waiting_on,
                   "runtime_task": task}
            )
            continue
        items.append(WorkItem(
            runtime_refs=(task.key or task.task,),
            owner_bot=owner_bot,
            title=task.task or None,
            current_window=task.room or None,
            status=task.status,
            waiting_on=task.waiting_on,
            runtime_task=task,
        ))
    return items


def prioritize_window(items: list[WorkItem], window_id: str | None) -> list[WorkItem]:
    """Stable sort: current-window items first, order otherwise kept."""
    if not window_id:
        return list(items)
    return sorted(
        items,
        key=lambda item: 0 if item.current_window == window_id else 1,
    )


def overlay_private_followups(
    items: list[WorkItem],
    pending,
    window,
) -> list[WorkItem]:
    """Overlay one bot's active private follow-up onto its WorkItems.

    Pure: input items are never mutated. The origin group sees only the
    generic ``waiting_private`` state — never question, answer, proposal,
    title, or readiness. The owner's own DM sees the received/ready
    state. Any other window gets the items unchanged.
    """
    from dataclasses import replace

    if pending is None or window is None:
        return list(items)
    is_dm = (
        getattr(window, "kind", None) == "dm"
        and pending.dm_window == getattr(window, "window_id", None)
    )
    is_origin = pending.origin_window == getattr(window, "window_id", None)
    if not is_dm and not is_origin:
        return list(items)
    out: list[WorkItem] = []
    matched = False
    for item in items:
        if item.work_id != pending.work_id:
            out.append(item)
            continue
        matched = True
        if is_dm:
            out.append(replace(
                item,
                pending_private_id=pending.pending_id,
                status=(
                    "ready"
                    if pending.requested_outcome == "ready"
                    else pending.status
                ),
                share_policy=pending.share_policy,
            ))
        elif pending.status in ("open", "waiting_private", "received"):
            out.append(replace(
                item,
                pending_private_id=pending.pending_id,
                status="waiting_private",
            ))
        else:
            out.append(replace(item, pending_private_id=pending.pending_id))
    if not matched and is_origin and pending.status in ("open", "waiting_private", "received"):
        out.append(WorkItem(
            work_id=pending.work_id,
            status="waiting_private",
            pending_private_id=pending.pending_id,
        ))
    return out
