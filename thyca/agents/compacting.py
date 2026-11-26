"""LLM compaction orchestration shared by the loop (auto) and ChatApp (manual).

The summarizer itself is injected (app layer owns the provider client);
this module owns only the plan → summarize → apply sequence and its
rule-based fallback, so both paths compact identically. A summarizer
failure or a transcript that changed mid-summary never loses data: the
excerpt path runs on fresh state instead.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from thyca.conversations.runtime import ConversationManager
from thyca.conversations.runtime.compaction import PRIOR_CLEARED_MARKER
from thyca.conversations.runtime.summary import SUMMARY_MAX_CHARS
from thyca.core.protocol import Message, utc_now_ts

from .meta import compaction_meta


@dataclass(frozen=True)
class SummaryOutcome:
    text: str
    reply: object = None
    latency_ms: int = 0


class Summarizer(Protocol):
    async def summarize(
        self,
        fresh: list[Message],
        prior_text: str,
        instructions: str | None,
    ) -> SummaryOutcome | None: ...


async def compact_with_llm(
    summarizer: Summarizer | None,
    conversations: ConversationManager,
    *,
    model: str | None,
    pricing: dict | None,
    hot_tokens: int = 0,
    tools_tokens: int = 0,
    pending_user_tokens: int = 0,
    force: bool = False,
    instructions: str | None = None,
    calibration: float = 1.0,
    drop_prior: bool = False,
) -> dict:
    """Compact ``conversations.current``, preferring an LLM summary body.

    Returns the manual-compact result shape plus ``summary``:
    ``"llm"`` when the model body landed, ``"cleared"`` when stale prior
    compaction context was dropped without needing a summary, ``"excerpt"``
    when the rule-based fallback compacted, ``None`` when nothing compacted.
    """
    snapshot = list(conversations.current.messages)
    before = len(snapshot)
    plan = conversations.compactor.plan(
        snapshot,
        conversations.limits.contextTokens,
        hot_tokens=hot_tokens,
        tools_tokens=tools_tokens,
        pending_user_tokens=pending_user_tokens,
        force=force,
        calibration=calibration,
        body_chars=SUMMARY_MAX_CHARS,
        drop_prior=drop_prior,
    )
    if plan is None:
        return {
            "compacted": False,
            "messages_before": before,
            "messages_after": before,
            "summary": None,
        }
    if plan.drop_prior and not plan.fresh:
        compacted = conversations.compactor.render(plan, "")
        if conversations.apply_compaction(compacted, expected=snapshot):
            return {
                "compacted": True,
                "messages_before": before,
                "messages_after": len(conversations.current.messages),
                "summary": "cleared",
            }
    outcome: SummaryOutcome | None = None
    if summarizer is not None and not (plan.drop_prior and not plan.fresh):
        try:
            outcome = await summarizer.summarize(
                plan.fresh, plan.prior_text, instructions
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = None
    if outcome is not None:
        compacted = conversations.compactor.render(plan, outcome.text)
        if conversations.apply_compaction(compacted, expected=snapshot):
            try:
                conversations.append(
                    Message(
                        role="assistant",
                        content=None,
                        ts=utc_now_ts(),
                        meta=compaction_meta(
                            outcome.reply, outcome.latency_ms, model, pricing
                        ),
                    )
                )
            except Exception:
                pass
            return {
                "compacted": True,
                "messages_before": before,
                "messages_after": len(conversations.current.messages),
                "summary": "llm",
            }
    if force or drop_prior:
        result = conversations.compact_manual(
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            calibration=calibration,
            drop_prior=drop_prior,
        )
    else:
        did = conversations.compact_if_needed(
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            pending_user_tokens=pending_user_tokens,
            calibration=calibration,
        )
        result = {
            "compacted": did,
            "messages_before": before,
            "messages_after": len(conversations.current.messages),
        }
    if not result["compacted"]:
        return {**result, "summary": None}
    msgs = conversations.current.messages
    cleared = bool(
        msgs
        and msgs[0].role == "system"
        and (msgs[0].content or "").strip() == PRIOR_CLEARED_MARKER
    )
    return {**result, "summary": "cleared" if cleared else "excerpt"}
