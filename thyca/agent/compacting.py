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

from thyca.core.protocol import Message, utc_now_ts
from thyca.sessions import SessionManager
from thyca.sessions.summary import SUMMARY_MAX_CHARS

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
    sessions: SessionManager,
    *,
    model: str | None,
    pricing: dict | None,
    hot_tokens: int = 0,
    tools_tokens: int = 0,
    pending_user_tokens: int = 0,
    force: bool = False,
    instructions: str | None = None,
    calibration: float = 1.0,
) -> dict:
    """Compact ``sessions.current``, preferring an LLM summary body.

    Returns the manual-compact result shape plus ``summary``:
    ``"llm"`` when the model body landed, ``"excerpt"`` when the
    rule-based fallback compacted, ``None`` when nothing compacted.
    """
    snapshot = list(sessions.current.messages)
    before = len(snapshot)
    plan = sessions.compactor.plan(
        snapshot,
        sessions.limits.contextTokens,
        hot_tokens=hot_tokens,
        tools_tokens=tools_tokens,
        pending_user_tokens=pending_user_tokens,
        force=force,
        calibration=calibration,
        body_chars=SUMMARY_MAX_CHARS,
    )
    if plan is None:
        return {
            "compacted": False,
            "messages_before": before,
            "messages_after": before,
            "summary": None,
        }
    outcome: SummaryOutcome | None = None
    if summarizer is not None:
        try:
            outcome = await summarizer.summarize(
                plan.fresh, plan.prior_text, instructions
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = None
    if outcome is not None:
        compacted = sessions.compactor.render(plan, outcome.text)
        if sessions.apply_compaction(compacted, expected=snapshot):
            try:
                sessions.append(
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
                pass  # compaction already landed; cost record is best-effort
            return {
                "compacted": True,
                "messages_before": before,
                "messages_after": len(sessions.current.messages),
                "summary": "llm",
            }
    # Fallback: excerpt body on fresh disk state (also covers the
    # changed-under-summary case, where the plan is stale by definition).
    if force:
        result = sessions.compact_manual(
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            calibration=calibration,
        )
    else:
        did = sessions.compact_if_needed(
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            pending_user_tokens=pending_user_tokens,
            calibration=calibration,
        )
        result = {
            "compacted": did,
            "messages_before": before,
            "messages_after": len(sessions.current.messages),
        }
    return {**result, "summary": "excerpt" if result["compacted"] else None}
