"""Channel contract types (WS-1): frozen dataclasses, stdlib only.

Channels are communication adapters: inbound traffic arrives as a
normalized :class:`ChannelEvent`, outbound/cross-window work goes out as
a :class:`ChannelAction`. Sender and target-bot identity are established
by the adapter/runtime — never taken from model-supplied tool arguments.

``work_ref`` is the canonical WorkItem id: the memory ``task-<8 hex>``
id. A runtime span is only an execution ref and never valid here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from thyca.core.protocol import parse_ts

WORK_REF_RE = re.compile(r"^task-[0-9a-f]{8}$")

WINDOW_KINDS = ("dm", "group", "system")
VISIBILITIES = ("private", "public", "system")
ACTOR_KINDS = ("user", "bot", "system")
ACTION_KINDS = ("reply", "post", "ask_private", "ask_choice", "status")


def _check_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _check_choice(value: str, name: str, choices: tuple[str, ...]) -> None:
    if value not in choices:
        raise ValueError(f"{name} must be one of {list(choices)}, got {value!r}")


def _check_work_ref(value: str | None) -> None:
    if value is not None and (
        not isinstance(value, str) or WORK_REF_RE.fullmatch(value) is None
    ):
        raise ValueError(f"work_ref must match task-<8 hex>, got {value!r}")


@dataclass(frozen=True)
class ChannelRef:
    """One external channel (e.g. the WebUI, channel_id="webui")."""

    channel_id: str
    kind: str
    external_ref: str | None = None

    def __post_init__(self) -> None:
        _check_id(self.channel_id, "channel_id")
        _check_id(self.kind, "kind")
        if self.external_ref is not None:
            _check_id(self.external_ref, "external_ref")


_WINDOW_VISIBILITY = {"dm": "private", "group": "public", "system": "system"}


@dataclass(frozen=True)
class WindowRef:
    """One communication window of a bot's workspace (DM/group/system).

    Visibility is fixed by window kind (DM=private, group=public,
    system=system); pass ``visibility=None`` to infer it, an explicit
    mismatch raises."""

    window_id: str
    channel_id: str
    kind: Literal["dm", "group", "system"]
    participants: tuple[str, ...] = ()
    visibility: Literal["private", "public", "system"] | None = None

    def __post_init__(self) -> None:
        _check_id(self.window_id, "window_id")
        _check_id(self.channel_id, "channel_id")
        _check_choice(self.kind, "kind", WINDOW_KINDS)
        if isinstance(self.participants, str):
            raise ValueError("participants must be a sequence of ids")
        object.__setattr__(self, "participants", tuple(self.participants))
        for participant in self.participants:
            _check_id(participant, "participants[]")
        required = _WINDOW_VISIBILITY[self.kind]
        visibility = required if self.visibility is None else self.visibility
        _check_choice(visibility, "visibility", VISIBILITIES)
        if visibility != required:
            raise ValueError(
                f"{self.kind} window must be {required}, got {visibility!r}"
            )
        object.__setattr__(self, "visibility", visibility)

    def to_dict(self) -> dict:
        return {
            "window_id": self.window_id,
            "channel_id": self.channel_id,
            "kind": self.kind,
            "participants": list(self.participants),
            "visibility": self.visibility,
        }

    @staticmethod
    def from_dict(data: dict) -> "WindowRef":
        if not isinstance(data, dict):
            raise ValueError(f"window must be a dict, got {data!r}")
        try:
            return WindowRef(
                window_id=data["window_id"],
                channel_id=data["channel_id"],
                kind=data["kind"],
                participants=tuple(data.get("participants") or ()),
                visibility=data.get("visibility", "public"),
            )
        except KeyError as error:
            raise ValueError(f"window missing field {error}") from error


@dataclass(frozen=True)
class ActorRef:
    """Who produced an event; assigned by the adapter, not the model."""

    kind: Literal["user", "bot", "system"]
    id: str

    def __post_init__(self) -> None:
        _check_choice(self.kind, "kind", ACTOR_KINDS)
        _check_id(self.id, "id")


@dataclass(frozen=True)
class ChannelCapabilities:
    """What one channel can do; adapters declare, services consume."""

    can_dm: bool = True
    can_card: bool = False
    can_thread: bool = False
    can_edit: bool = False
    can_react: bool = False
    max_chars: int | None = None
    supports_streaming: bool = False

    def __post_init__(self) -> None:
        for name in ("can_dm", "can_card", "can_thread", "can_edit",
                     "can_react", "supports_streaming"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool")
        if self.max_chars is not None and (
            not isinstance(self.max_chars, int)
            or isinstance(self.max_chars, bool)
            or self.max_chars <= 0
        ):
            raise ValueError("max_chars must be None or a positive int")


@dataclass(frozen=True)
class ChannelEvent:
    """One normalized inbound event from a channel adapter."""

    event_id: str
    channel_id: str
    window_id: str
    actor: ActorRef
    text: str
    refs: tuple[str, ...] = ()
    visibility: Literal["private", "public", "system"] = "public"
    work_ref: str | None = None
    received_at: str = ""
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _check_id(self.event_id, "event_id")
        _check_id(self.channel_id, "channel_id")
        _check_id(self.window_id, "window_id")
        if not isinstance(self.actor, ActorRef):
            raise ValueError("actor must be an ActorRef")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        object.__setattr__(self, "refs", tuple(self.refs))
        for ref in self.refs:
            _check_id(ref, "refs[]")
        _check_choice(self.visibility, "visibility", VISIBILITIES)
        _check_work_ref(self.work_ref)
        parse_ts(self.received_at)
        if self.idempotency_key is not None:
            _check_id(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True)
class ChannelAction:
    """One outbound/cross-window action produced by a turn."""

    kind: Literal["reply", "post", "ask_private", "ask_choice", "status"]
    target_window: str
    text: str = ""
    card: dict | None = None
    work_ref: str | None = None
    share_policy: str | None = None
    metadata: dict | None = None

    def __post_init__(self) -> None:
        _check_choice(self.kind, "kind", ACTION_KINDS)
        _check_id(self.target_window, "target_window")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if self.card is not None and not isinstance(self.card, dict):
            raise ValueError("card must be a dict or None")
        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dict or None")
        _check_work_ref(self.work_ref)
        if self.share_policy is not None:
            _check_id(self.share_policy, "share_policy")
