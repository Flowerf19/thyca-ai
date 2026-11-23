"""Shared admin-tool wiring: deps + caller gates."""
from __future__ import annotations

from thyca.memories.identity import current_bot_id


class AdminDeps:
    """Everything admin handlers need (wired once by ChatApp)."""

    def __init__(self, bots, runtime, facade_fn, dm_post=None, card_post=None, room_post=None, dm_resolve=None, channel_actions=None, room_bots=None, room_dm_owner=None, prune_groups_for_bot=None, delete_group=None) -> None:
        self.channel_actions = channel_actions
        self.bots = bots
        self.runtime = runtime
        self.facade_fn = facade_fn
        self.dm_resolve = dm_resolve
        self.room_post = room_post
        self.dm_post = dm_post
        self.dm_card_post = None
        self.card_post = card_post
        self.room_bots = room_bots
        self.room_dm_owner = room_dm_owner
        self.prune_groups_for_bot = prune_groups_for_bot
        self.delete_group = delete_group


def _caller() -> str | None:
    return current_bot_id()


def _deny_child() -> str | None:
    caller = _caller()
    if caller is not None and caller != "thyca":
        return f"denied: bot administration is Thyca-only (caller: {caller})"
    return None
