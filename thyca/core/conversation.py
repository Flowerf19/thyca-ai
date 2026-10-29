"""Target user-facing conversation identity, independent of execution IDs.

This contract is not yet wired to the legacy session runtime.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Literal

_BOT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_GROUP_ID = re.compile(r"group-[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class ConversationRef:
    kind: Literal["dm", "group"]
    id: str

    def __post_init__(self) -> None:
        if self.kind == "dm":
            valid = isinstance(self.id, str) and len(self.id) <= 64 and _BOT_ID.fullmatch(self.id)
        elif self.kind == "group":
            valid = isinstance(self.id, str) and _GROUP_ID.fullmatch(self.id)
        else:
            valid = False
        if not valid:
            raise ValueError("invalid conversation reference")

    @classmethod
    def dm(cls, bot_id: str) -> ConversationRef:
        return cls("dm", bot_id)

    @classmethod
    def group(cls, group_id: str) -> ConversationRef:
        return cls("group", group_id)

    @classmethod
    def new_group(cls) -> ConversationRef:
        return cls.group("group-" + secrets.token_hex(16))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def from_dict(cls, data: object) -> ConversationRef:
        """Parse an explicit kind+id payload; kind is never guessed
        from the identifier text."""
        if not isinstance(data, dict) or set(data) != {"kind", "id"}:
            raise ValueError("invalid conversation reference")
        return cls(data["kind"], data["id"])


def bot_is_participant(
    ref: ConversationRef, participants: object, bot_id: str
) -> bool:
    """Whether a trusted bot actor is a current participant.

    DM state must name exactly its own bot; group membership is the
    current participant list, so a former participant returns False.
    Invalid participant state or actor identity raises ValueError
    instead of guessing."""
    if not isinstance(ref, ConversationRef):
        raise ValueError("expected conversation reference")
    ConversationRef.dm(bot_id)
    if isinstance(participants, str) or not isinstance(participants, (list, tuple)):
        raise ValueError("invalid participants")
    members = tuple(participants)
    for member in members:
        ConversationRef.dm(member)
    if not members or len(set(members)) != len(members):
        raise ValueError("invalid participants")
    if ref.kind == "dm":
        if members != (ref.id,):
            raise ValueError("invalid participants")
        return bot_id == ref.id
    return bot_id in members
