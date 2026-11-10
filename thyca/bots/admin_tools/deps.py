"""Shared admin-tool wiring: deps + caller gates."""
from __future__ import annotations

from thyca.memories.identity import current_bot_id


class AdminDeps:
    """Everything admin handlers need (wired once by ChatApp)."""

    # Capture collaborators once; optional resolvers default to None.
    def __init__(self, bots, runtime, facade_fn, dm_post=None, card_post=None, room_post=None, dm_resolve=None, channel_actions=None, room_bots=None, room_dm_owner=None, prune_groups_for_bot=None, delete_group=None) -> None:
        self.channel_actions = channel_actions
        self.bots = bots
        self.runtime = runtime
        self.facade_fn = facade_fn
        #: ``(bot_id) -> conversation_id`` resolving the bot's fixed DM window
        #: (ask_private targets the DM of record, not the current room).
        self.dm_resolve = dm_resolve
        #: ``(bot_id, text, room_id) -> dict`` landing a plain status row
        #: in the origin room (e.g. the group waiting status).
        self.room_post = room_post
        #: ``(bot_id, text, current_room_id) -> dict`` landing a message
        #: in the bot's own DM (ChatApp owns DM resolution + room writes).
        self.dm_post = dm_post
        #: ``(bot_id, card) -> dict`` landing a structured card in the
        #: bot's own DM (consent preview for private follow-ups).
        self.dm_card_post = None
        #: ``(bot_id, card, current_room_id, content) -> dict`` landing a
        #: structured question/error card in the current room (ChatApp
        #: owns room membership/DM-privacy gates + transcript writes).
        self.card_post = card_post
        #: ``(room_id) -> [bot_id]`` participant lookup for send-time
        #: outsider classification (fail closed when unavailable).
        self.room_bots = room_bots
        #: ``(room_id) -> bot_id | None`` DM-owner lookup for send-time
        #: classification (DM delivery keeps owner-pair behavior).
        self.room_dm_owner = room_dm_owner
        #: ``(bot_id) -> PruneResult`` pruning one bot from every group.
        self.prune_groups_for_bot = prune_groups_for_bot
        #: ``(group_id) -> None`` deleting one group door.
        self.delete_group = delete_group


def _caller() -> str | None:
    return current_bot_id()


def _deny_child() -> str | None:
    caller = _caller()
    if caller is not None and caller != "thyca":
        return f"denied: bot administration is Thyca-only (caller: {caller})"
    return None
