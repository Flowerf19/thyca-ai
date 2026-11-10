"""Title removal contract: no title anywhere, identity carries the display.

The title module is gone (clean cutover, no compatibility layer):
- wire summary/detail carry no ``title`` key;
- DM/group/empty doors still expose identity (kind, bot/group id,
  message/turn counts) so consumers label each from what it is;
- no write or read path accepts title / naming_attempted / kind=naming.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from seeded_config import seeded_config

from thyca.conversations.runtime import ConversationRuntime
from thyca.conversations.runtime.wire import is_sidecar_message, conversation_detail, conversation_summary
from thyca.conversations.store import (
    AdminStore,
    AgentStore,
    DMBootstrapIntent,
    RootInitializationIntent,
    RuntimeReadIntent,
    assemble_history,
    detail_for,
    summary_for,
    validate_metadata,
)
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message


def _root_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.absolute())))


def _init(root: Path) -> None:
    AdminStore(root).initialize(RootInitializationIntent(_root_id(root)))


def test_wire_summary_and_detail_have_no_title() -> None:
    cfg = seeded_config()
    spoken = ConversationRuntime(
        "s1", Path("store://dm/coder"),
        [Message(role="user", content="alo", ts="2026-01-01T00:00:00Z")],
        bots=["coder"], dm_bot="coder",
    )
    assert "title" not in conversation_summary(spoken)
    detail = conversation_detail(spoken, cfg)
    assert "title" not in detail
    assert detail["kind"] == "dm" and detail["dm"] == "coder"


def test_read_adapter_summary_and_detail_have_no_title(tmp_path: Path) -> None:
    _init(tmp_path)
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f"{tmp_path.absolute()}:coder").hex
    ref = AdminStore(tmp_path).bootstrap_initial_dm(
        "coder", intent=DMBootstrapIntent("coder", attempt)).ref
    AgentStore(tmp_path).append(
        ref, "coder", Message(role="user", content="ping", ts="2020-01-01T00:00:00Z"))
    conversation = assemble_history(
        tmp_path, ref, "coder", intent=RuntimeReadIntent("runtime-read"))
    listing, _ = AdminStore(tmp_path).list()
    created = next(row.created_at for row in listing if row.ref == ref)
    summary = summary_for(conversation, created)
    detail = detail_for(conversation, seeded_config())
    assert "title" not in summary and "title" not in detail
    # Identity carries the display for a non-empty DM.
    assert summary["kind"] == "dm" and summary["dm"] == "coder"
    assert summary["message_count"] == 1 and summary["turns"] == 1


def test_empty_door_still_lists_with_identity(tmp_path: Path) -> None:
    _init(tmp_path)
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f"{tmp_path.absolute()}:coder").hex
    ref = AdminStore(tmp_path).bootstrap_initial_dm(
        "coder", intent=DMBootstrapIntent("coder", attempt)).ref
    conversation = assemble_history(
        tmp_path, ref, "coder", intent=RuntimeReadIntent("runtime-read"))
    listing, _ = AdminStore(tmp_path).list()
    created = next(row.created_at for row in listing if row.ref == ref)
    summary = summary_for(conversation, created)
    assert conversation.messages == ()
    assert "title" not in summary
    # The empty door keeps its identity label from kind/dm/message_count.
    assert summary["kind"] == "dm" and summary["dm"] == "coder"
    assert summary["message_count"] == 0 and summary["updated_at"] == created


def test_group_summary_has_no_title_and_shows_group_identity(tmp_path: Path) -> None:
    _init(tmp_path)
    group = AdminStore(tmp_path).create_group(["coder", "thyca"]).ref
    conversation = assemble_history(
        tmp_path, group, "coder", intent=RuntimeReadIntent("runtime-read"))
    listing, _ = AdminStore(tmp_path).list()
    created = next(row.created_at for row in listing if row.ref == group)
    summary = summary_for(conversation, created)
    assert "title" not in summary
    assert summary["kind"] == "group"
    assert summary["bots"] == ["coder", "thyca"]


def test_metadata_rejects_title_and_naming_keys(tmp_path: Path) -> None:
    _init(tmp_path)
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f"{tmp_path.absolute()}:coder").hex
    ref = AdminStore(tmp_path).bootstrap_initial_dm(
        "coder", intent=DMBootstrapIntent("coder", attempt)).ref
    for data in (
        {"title": None, "title_source": None, "participants": ["coder"]},
        {"participants": ["coder"], "naming_attempted": True},
        {"title": "x", "participants": ["coder"]},
    ):
        with pytest.raises(ValueError):
            validate_metadata(ref, data)


def test_naming_kind_is_not_a_sidecar() -> None:
    naming = Message(role="assistant", content=None, meta={"kind": "naming"})
    assert is_sidecar_message(naming) is False
    compaction = Message(role="assistant", content=None, meta={"kind": "compaction"})
    assert is_sidecar_message(compaction) is True


def test_removed_interfaces_stay_removed() -> None:
    from thyca.app.chat_app import ChatApp
    from thyca.conversations.runtime import ConversationManager, ConversationStore

    assert not hasattr(ConversationStore, "append_meta")
    assert not hasattr(ConversationManager, "set_title")
    assert not hasattr(ConversationManager, "rename")
    assert not hasattr(ChatApp, "rename_conversation")
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("thyca.conversations.runtime.title")