"""Shared turn-context policy thresholds (stdlib-only leaf).

The pre-turn compactor (:mod:`thyca.conversations.runtime.compaction`) and the mid-turn
guard (:mod:`thyca.agents.shrink` / :mod:`thyca.agents.loop`) must agree on
when a turn no longer fits: compaction fires at the same ratio the guard
backstops at, so a turn that would die at the backstop compacts first.
Neither side may import the other (conversations <- agent would cycle), so the
ratio lives here next to the other wire-level constants.
"""
from __future__ import annotations

BACKSTOP_RATIO = 0.95
