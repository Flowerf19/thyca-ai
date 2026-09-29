"""Shared turn-context policy thresholds (stdlib-only leaf).

The pre-turn compactor (:mod:`thyca.sessions.compaction`) and the mid-turn
guard (:mod:`thyca.agent.shrink` / :mod:`thyca.agent.loop`) must agree on
when a turn no longer fits: compaction fires at the same ratio the guard
backstops at, so a turn that would die at the backstop compacts first.
Neither side may import the other (sessions <- agent would cycle), so the
ratio lives here next to the other wire-level constants.
"""
from __future__ import annotations

#: Fraction of ``contextTokens`` at which the pre-turn compactor fires and
#: the mid-turn guard stops the turn. One number, two consumers: a turn
#: that would hit the backstop must have compacted first (dead zone otherwise).
BACKSTOP_RATIO = 0.95
