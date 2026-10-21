"""WorkspaceContextBuilder (WS-3): one place that assembles the hot
ActiveSnapshot for a turn, and the ephemeral context-budget signal.

All memory-facing sections are already filtered by the bound
MemoryView; the builder only composes validated strings — it never
sanitizes raw text semantically.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from thyca.core.channel import WindowRef
from thyca.memory.active import ActiveSnapshot


def _layered_soul(shared_soul: str, persona: str) -> str:
    """Compose the ``<role>`` section: shared soul + this twin's overlay.

    Every twin (Thyca included) rides the shared SOUL.md; the card's
    persona body is appended as the bot's own working notes and wins
    where it conflicts. Empty persona simply leaves the shared soul —
    there is deliberately no fallback direction to invert.
    """
    own = (persona or "").strip()
    shared = (shared_soul or "").strip()
    if not own:
        return shared_soul
    overlay = (
        "## Your own working notes\n\n"
        "These come from your own TWIN.md. They refine the shared soul "
        "above for you specifically and win where they conflict.\n\n"
        f"{own}"
    )
    if not shared or shared == "# Soul":
        return overlay
    return f"{shared}\n\n{overlay}"


def _channel_brief(window: WindowRef) -> str:
    return (
        f"channel={window.channel_id} window={window.window_id} "
        f"kind={window.kind} visibility={window.visibility}\n"
        f"participants={', '.join(window.participants)}"
    )


class WorkspaceContextBuilder:
    """Assembles the per-window ActiveSnapshot and budget signal."""

    def __init__(self, thyca_dir: Path) -> None:
        self._dir = Path(thyca_dir)

    def build(
        self,
        hot: ActiveSnapshot,
        *,
        bot_id: str,
        window: WindowRef | None = None,
        card: Any = None,
        runtime_tasks: tuple = (),
        memory_catalog: tuple = (),
        recalled: tuple = (),
        room_delta: str = "",
        task_text: str = "",
        memory_task_text: str = "",
        bot_user: str = "",
        skills: str | None = None,
        team: str | None = None,
        pending_followup: Any = None,
    ) -> ActiveSnapshot:
        from thyca.bots.card import identity_text
        from thyca.bots.registry import SYSTEM_BOT_ID

        memory_ok = card is None or card.memory.read
        if card is not None and not memory_ok:
            # memory.read=false: the hot snapshot carries no memory
            # content at all for this bot (own or team) — suppress every
            # memory section, including any prepopulated strings.
            hot = replace(
                hot, today="", today_elsewhere="",
                bot_user="", memory_tasks="", recalled="")
        elif bot_user.strip():
            # Phase 1E: this bot's own USER.md rides as <bot_user>.
            hot = replace(hot, bot_user=bot_user)
        # Group projections hide only the exact AUTO-generated habits
        # block (derived private labels) — manual profile text stays.
        if window is not None and getattr(window, "kind", None) == "group":
            from thyca.memory.habits import strip_auto_habits

            if hot.user and strip_auto_habits(hot.user) != hot.user:
                hot = replace(hot, user=strip_auto_habits(hot.user))
            if hot.bot_user and strip_auto_habits(hot.bot_user) != hot.bot_user:
                hot = replace(hot, bot_user=strip_auto_habits(hot.bot_user))
        if card is not None:
            # Two-layer identity (user decision 2026-10-13, hardened at the
            # source in phase 1D): the shared soul (SOUL.md — voice/values
            # only, names nobody) stays for every twin; a child bot's
            # persona is its own overlay on top, never a replacement-or-
            # fallback. The snapshot for a child already carries no
            # identity (ActiveMemory only loads IDENTITY.md for privileged
            # readers), and the <identity> slot is set here from its own
            # TWIN.md as defense in depth — Thyca's IDENTITY.md must never
            # ride a child turn (that leak made children answer as Thyca).
            # Thyca itself keeps its real identity section.
            is_manager = card.system or bot_id == SYSTEM_BOT_ID
            hot = replace(
                hot,
                soul=_layered_soul(hot.soul, card.persona),
                identity=hot.identity if is_manager else identity_text(card),
            )
        if skills is not None:
            # Exactly private + team skills for this bot; never fall
            # back to Thyca's private index from the shared snapshot.
            hot = replace(hot, skills=skills)
        if team is not None:
            hot = replace(hot, team=team)
        if task_text:
            hot = replace(hot, tasks=task_text)
        if memory_task_text and memory_ok:
            hot = replace(hot, memory_tasks=memory_task_text)
        if runtime_tasks or (memory_catalog and memory_ok):
            from thyca.bots.work_items import prioritize_window, project_work_items
            from thyca.memory.facade import format_task_catalog

            items = prioritize_window(
                project_work_items(
                    list(runtime_tasks),
                    list(memory_catalog) if memory_ok else [],
                    owner_bot=bot_id,
                ),
                window.window_id if window is not None else None,
            )
            from thyca.bots.work_items import overlay_private_followups

            items = overlay_private_followups(
                items, pending_followup, window
            )
            task_lines = [
                item.runtime_task.index_line()
                for item in items
                if item.runtime_task is not None
            ]
            if task_lines:
                hot = replace(hot, tasks="\n".join(task_lines))
            if memory_ok:
                rows = [
                    item.memory_row
                    for item in items
                    if item.memory_row is not None
                ]
                if rows:
                    hot = replace(hot, memory_tasks=format_task_catalog(rows))
        if recalled and memory_ok:
            hot = replace(hot, recalled="\n".join(str(l) for l in recalled))
        if room_delta:
            # Unread public room history: already view-filtered by the
            # caller, rides the prompt ephemerally (never persisted).
            # Independent of the memory.read gate — a memory-blind bot
            # still needs its room context to function.
            hot = replace(hot, room_history=room_delta)
        if window is not None:
            hot = replace(hot, channel_brief=_channel_brief(window))
        if pending_followup is not None and window is not None:
            p = pending_followup
            if window.kind == "dm" and p.dm_window == window.window_id:
                question = " ".join(str(p.question).split())[:500]
                hot = replace(
                    hot,
                    private_followup=(
                        f"pending_private={p.pending_id} "
                        f"work_id={p.work_id} state={p.status} "
                        f"origin_window={p.origin_window} "
                        f"question={question}"
                    ),
                )
            elif p.origin_window == window.window_id and p.status in ("open", "waiting_private", "received"):
                hot = replace(
                    hot,
                    private_followup=(
                        f"work_id={p.work_id} state=waiting_private"
                    ),
                )
        return hot


    @staticmethod
    def with_context_budget(
        hot: object,
        *,
        messages: list,
        context_tokens: int | None,
        pending_tokens: int,
        calibration: float,
        tools_tokens: int,
        can_read: bool,
        can_write: bool,
    ) -> object:
        """The two-pass ``<context_budget>`` signal (moved verbatim from
        AgentLoop: same timing inputs, same 80/90/95 math and renderer)."""
        from thyca.agent.context_budget import (
            checkpoint_state,
            format_context_budget,
        )
        from thyca.core.protocol import estimate_tokens as _chars_to_tokens
        from thyca.llm.prompt_manager import PromptManager
        from thyca.sessions import context_usage

        if not isinstance(hot, ActiveSnapshot) or context_tokens is None:
            return hot
        baseline = replace(hot, context_budget="")
        baseline_hot = _chars_to_tokens(PromptManager().build(baseline))

        def _usage(signal_text: str) -> dict:
            signal_tokens = (
                _chars_to_tokens(f"<context_budget>\n{signal_text}\n</context_budget>")
                if signal_text
                else 0
            )
            return context_usage(
                messages,
                context_tokens,
                hot_tokens=baseline_hot + signal_tokens,
                tools_tokens=tools_tokens,
                pending_user_tokens=pending_tokens,
                calibration=calibration,
            )

        def _signal(usage: dict) -> str:
            return format_context_budget(
                usage,
                checkpoint=checkpoint_state(
                    messages,
                    context_tokens=context_tokens,
                    current_ratio=float(usage.get("ratio") or 0.0),
                    calibration=calibration,
                ),
                can_read=can_read,
                can_write=can_write,
            )

        first = _signal(_usage(""))
        return replace(baseline, context_budget=_signal(_usage(first)))


def build_workspace_hot(
    hot: ActiveSnapshot,
    *,
    thyca_dir: Path,
    bot_id: str,
    window: WindowRef | None = None,
    card: Any = None,
    runtime_tasks: tuple = (),
    memory_catalog: tuple = (),
    recalled: tuple = (),
    room_delta: str = "",
    task_text: str = "",
    memory_task_text: str = "",
    bot_user: str = "",
    skills: str | None = None,
    team: str | None = None,
    pending_followup: Any = None,
) -> ActiveSnapshot:
    """Compose a hot snapshot from already-prepared, turn-scoped inputs."""
    return WorkspaceContextBuilder(thyca_dir).build(
        hot,
        bot_id=bot_id,
        window=window,
        card=card,
        runtime_tasks=runtime_tasks,
        memory_catalog=memory_catalog,
        recalled=recalled,
        room_delta=room_delta,
        task_text=task_text,
        memory_task_text=memory_task_text,
        bot_user=bot_user,
        skills=skills,
        team=team,
        pending_followup=pending_followup,
    )
