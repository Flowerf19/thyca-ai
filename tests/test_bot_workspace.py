"""WS-1/WS-2: channel contract + workspace registry + turn lease."""
from __future__ import annotations

import asyncio
import contextvars
import json
import threading
from pathlib import Path

import pytest

from thyca.bots.bus import Bus
from thyca.bots.registry import BotRegistry
from thyca.bots.runtime import BotRuntime
from thyca.bots.workspace import (
    BotWorkspace,
    WorkspaceRegistry,
    WorkspaceTurnConflict,
)
from thyca.bots.workspace_store import WorkspaceStore, WorkspaceStoreError
from thyca.core.channel import (
    ActorRef,
    ChannelAction,
    ChannelCapabilities,
    ChannelEvent,
    ChannelRef,
    WindowRef,
)
from thyca.core.protocol import Message, utc_now_ts
from thyca.sessions import SessionManager


def _runtime(tmp_path: Path) -> BotRuntime:
    return BotRuntime(tmp_path, BotRegistry(tmp_path), Bus())


def test_contract_validation_and_window_serialization() -> None:
    window = WindowRef(
        window_id="w1", channel_id="webui", kind="dm",
        participants=("user", "coder"),
    )
    assert window.visibility == "private"
    assert WindowRef(window_id="g", channel_id="webui",
                     kind="group").visibility == "public"
    assert WindowRef(window_id="s", channel_id="webui",
                     kind="system").visibility == "system"
    assert WindowRef.from_dict(window.to_dict()) == window
    assert ChannelRef(channel_id="webui", kind="webui").external_ref is None
    assert ActorRef(kind="bot", id="coder").id == "coder"
    caps = ChannelCapabilities(can_dm=True, max_chars=None)
    assert caps.can_dm and not caps.supports_streaming
    event = ChannelEvent(
        event_id="e1", channel_id="webui", window_id="w1",
        actor=ActorRef(kind="user", id="u1"), text="hi",
        refs=("task-1234abcd",), work_ref="task-1234abcd",
        received_at=utc_now_ts(), idempotency_key="key-1",
    )
    assert event.refs == ("task-1234abcd",)
    action = ChannelAction(kind="ask_private", target_window="w1",
                           work_ref="task-1234abcd", share_policy="consent")
    assert action.kind == "ask_private"
    with pytest.raises(ValueError):
        WindowRef(window_id="w", channel_id="webui", kind="channel")
    with pytest.raises(ValueError):
        WindowRef(window_id="w", channel_id="webui", kind="dm",
                  visibility="public")
    with pytest.raises(ValueError):
        WindowRef(window_id="w", channel_id="webui", kind="group",
                  visibility="private")
    with pytest.raises(ValueError):
        WindowRef(window_id="w", channel_id="webui", kind="dm",
                  participants="coder")
    with pytest.raises(ValueError):
        ChannelEvent(event_id="e", channel_id="webui", window_id="w",
                     actor=ActorRef(kind="user", id="u"), text="x",
                     received_at="not-a-ts")
    with pytest.raises(ValueError):
        ChannelEvent(event_id="e", channel_id="webui", window_id="w",
                     actor=ActorRef(kind="user", id="u"), text="x",
                     work_ref="span-1234abcd", received_at=utc_now_ts())
    with pytest.raises(ValueError):
        ChannelCapabilities(max_chars=0)
    with pytest.raises(ValueError):
        ChannelAction(kind="delete", target_window="w1")
    with pytest.raises(ValueError):
        WindowRef.from_dict({"window_id": "w"})


def test_registry_single_current_shape_and_strictness(tmp_path: Path) -> None:
    from thyca.core.channel import WindowRef as _WR

    store = WorkspaceStore(tmp_path)
    # One current shape only: a legacy room_sessions.json file is ignored.
    legacy = tmp_path / "bots" / "coder" / "room_sessions.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "room-old": "sess-1",
        "room-new": {"session": "sess-2", "room_cursor": 4,
                     "room_fingerprint": "fp4"},
    }), encoding="utf-8")
    assert store.load("coder") == {}
    binding = store.bind_window("coder", _WR(
        window_id="w1", channel_id="webui", kind="dm",
        participants=("coder",)), cursor=(4, "fp4"))
    assert binding.internal_session_id
    assert binding.cursor == (4, "fp4")
    # Malformed registry entries raise instead of being tolerated.
    path = tmp_path / "bots" / "coder" / "workspace" / "registry.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["windows"]["w-bad"] = "sess-string"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(WorkspaceStoreError):
        store.load("coder")


def test_restart_loads_exact_internal_ids_and_cursors(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    internal = runtime.internal_session("coder", "room1")
    runtime.set_room_cursor("coder", "room1", (7, "fp7"))
    store = WorkspaceStore(tmp_path)
    binding = store.get_binding("coder", "room1")
    assert binding is not None
    assert binding.internal_session_id == internal.id
    assert binding.cursor == (7, "fp7")
    registry = WorkspaceRegistry(tmp_path)
    again = registry.workspace("coder").get_window("room1")
    assert again is not None
    assert again.internal_session_id == internal.id
    assert again.cursor == (7, "fp7")


def test_transcript_rows_never_seed_the_cursor(tmp_path: Path) -> None:
    from thyca.bots.timeline import WorkspaceTimeline
    from thyca.bots.timeline import CanonicalSessionStore
    from thyca.core.channel import WindowRef as _WR

    runtime = _runtime(tmp_path)
    internal = runtime.internal_session("coder", "room1")
    window = _WR(window_id="room1", channel_id="webui", kind="dm",
                  participants=("coder",))
    store = runtime._canonical_store("coder", window, internal.id)
    manager = SessionManager(store=store)
    manager.load(internal.id)
    # Even a legacy-shaped synthetic row in the transcript must not
    # seed the cursor: the registry binding is the only source.
    manager.append(Message(role="user", content="delta",
                           meta={"kind": "room_context", "room_cursor": 9,
                                 "room_fingerprint": "fp9"}))
    assert runtime.room_cursor("coder", "room1") is None
    runtime.set_room_cursor("coder", "room1", (9, "fp9"))
    assert runtime.room_cursor("coder", "room1") == (9, "fp9")


def test_bind_dm_and_group_window_metadata(tmp_path: Path) -> None:
    workspace = BotWorkspace("coder", WorkspaceStore(tmp_path))
    dm = WindowRef(window_id="w-dm", channel_id="webui", kind="dm",
                   participants=("user", "coder"))
    group = WindowRef(window_id="w-grp", channel_id="webui", kind="group",
                      participants=("thyca", "coder"))
    workspace.bind_window(dm, internal_session_id="sess-dm")
    workspace.bind_window(group, cursor=(2, "fpg"))
    assert workspace.get_window("w-dm").window == dm
    assert workspace.get_window("w-dm").internal_session_id == "sess-dm"
    assert workspace.get_window("w-grp").window == group
    assert workspace.get_window("w-grp").cursor == (2, "fpg")


def test_bind_window_mints_session_id_for_new_window(tmp_path: Path) -> None:
    import re

    from thyca.sessions.store import SESSION_ID_PATTERN

    store = WorkspaceStore(tmp_path)
    binding = store.bind_window("coder", WindowRef(
        window_id="w1", channel_id="webui", kind="dm",
        participants=("coder",)))
    assert binding.internal_session_id
    assert re.fullmatch(SESSION_ID_PATTERN, binding.internal_session_id)
    assert binding.cursor is None
    loaded = store.get_binding("coder", "w1")
    assert loaded.internal_session_id == binding.internal_session_id
    assert loaded.cursor is None
    assert loaded.window.kind == "dm"
    # An explicit session id is preserved, never reminted.
    second = store.bind_window(
        "coder", WindowRef(
            window_id="w1", channel_id="webui", kind="dm",
            participants=("coder",)),
        internal_session_id="sess-explicit",
        cursor=(5, "fp5"))
    assert second.internal_session_id == "sess-explicit"
    assert second.cursor == (5, "fp5")


def test_updates_preserve_other_bindings_and_unknown_metadata(
        tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    store.bind_window("coder", WindowRef(
        window_id="w1", channel_id="webui", kind="dm",
        participants=("coder",)))
    path = tmp_path / "bots" / "coder" / "workspace" / "registry.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["future_field"] = {"keep": True}
    doc["windows"]["w1"]["future_entry_field"] = 42
    path.write_text(json.dumps(doc), encoding="utf-8")
    store.bind_window("coder", WindowRef(
        window_id="w2", channel_id="webui", kind="group",
        participants=("thyca", "coder")))
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["future_field"] == {"keep": True}
    assert doc["windows"]["w1"]["future_entry_field"] == 42
    assert doc["windows"]["w1"]["window"]["kind"] == "dm"
    assert doc["windows"]["w2"]["window"]["kind"] == "group"


def test_concurrent_binds_both_survive(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    barrier = threading.Barrier(2)
    windows = [
        WindowRef(window_id="w-a", channel_id="webui", kind="dm",
                  participants=("coder",)),
        WindowRef(window_id="w-b", channel_id="webui", kind="group",
                  participants=("coder", "helper")),
    ]
    errors: list[BaseException] = []

    def bind(window: WindowRef) -> None:
        barrier.wait()
        try:
            store.bind_window("coder", window)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=bind, args=(window,))
               for window in windows]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    bindings = store.load("coder")
    assert bindings["w-a"].window == windows[0]
    assert bindings["w-b"].window == windows[1]


def test_corrupted_registry_raises_and_resets_nothing(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    store.bind_window("coder", WindowRef(
        window_id="w1", channel_id="webui", kind="dm",
        participants=("coder",)))
    path = tmp_path / "bots" / "coder" / "workspace" / "registry.json"
    path.write_text("{broken json", encoding="utf-8")
    with pytest.raises(WorkspaceStoreError):
        store.load("coder")
    with pytest.raises(WorkspaceStoreError):
        store.bind_window("coder", WindowRef(
            window_id="w2", channel_id="webui", kind="group",
            participants=("coder",)))
    assert path.read_text(encoding="utf-8") == "{broken json"


def test_path_traversal_bot_id_rejected(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    for bad in ("../x", "a/b", "..", "Coder", "", "x y"):
        with pytest.raises(ValueError):
            store.load(bad)
        with pytest.raises(ValueError):
            BotWorkspace(bad, store)


def test_runtime_binding_roundtrip_in_registry_no_legacy_files(
        tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    internal = runtime.internal_session("coder", "room1")
    runtime.set_room_cursor("coder", "room1", (3, "fp3"))
    raw = json.loads(
        (tmp_path / "bots" / "coder" / "workspace" / "registry.json")
        .read_text(encoding="utf-8"))
    entry = raw["windows"]["room1"]
    assert entry["session"] == internal.id
    assert entry["room_cursor"] == 3
    assert entry["room_fingerprint"] == "fp3"
    # No legacy mirror or backup is ever written.
    assert not (tmp_path / "bots" / "coder" / "room_sessions.json").exists()
    assert not (tmp_path / "bots" / "coder"
                / "room_sessions.json.bak").exists()


def test_workspace_registry_shares_one_workspace_per_bot(
        tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path)
    first = registry.workspace("coder")
    assert registry.workspace("coder") is first
    assert registry.workspace("helper") is not first


def test_no_legacy_files_created_by_store(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    store.bind_window("coder", WindowRef(
        window_id="room1", channel_id="webui", kind="dm",
        participants=("coder",)))
    store.ensure_session("coder", "room-synthetic")
    assert not (tmp_path / "bots" / "coder" / "room_sessions.json").exists()
    assert not (tmp_path / "bots" / "coder"
                / "room_sessions.json.bak").exists()
    assert store.get_binding("coder", "room-synthetic").window is None


def test_lease_serializes_same_bot_allows_other_bots(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path)
    order: list[str] = []
    gate = asyncio.Event()

    async def main() -> None:
        async def hold(bot: str, marker: str, wait: bool) -> None:
            async with registry.lease(bot):
                order.append(f"{marker}-start")
                if wait:
                    await gate.wait()
                order.append(f"{marker}-end")

        first = asyncio.create_task(hold("coder", "dm", wait=True))
        await asyncio.sleep(0)
        queued = asyncio.create_task(hold("coder", "grp", wait=False))
        other = asyncio.create_task(hold("helper", "oth", wait=False))
        await asyncio.wait_for(other, timeout=5)
        assert order == ["dm-start", "oth-start", "oth-end"]
        gate.set()
        await asyncio.wait_for(asyncio.gather(first, queued), timeout=5)
        assert order[-2:] == ["grp-start", "grp-end"]
        assert "dm-end" in order

    asyncio.run(main())


def test_lease_reentry_same_bot_conflicts(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path)

    async def main() -> None:
        async with registry.lease("alpha"):
            async with registry.lease("beta"):
                with pytest.raises(WorkspaceTurnConflict):
                    async with registry.lease("alpha"):
                        pass

    asyncio.run(main())


def test_inherited_owner_second_acquire_conflicts(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path)

    async def main() -> None:
        async with registry.lease("alpha"):
            first_in = asyncio.Event()
            release = asyncio.Event()
            outcomes: list[str] = []

            async def child(marker: str) -> None:
                try:
                    async with registry.lease("beta"):
                        outcomes.append(f"{marker}-acquired")
                        first_in.set()
                        await release.wait()
                except WorkspaceTurnConflict:
                    outcomes.append(f"{marker}-conflict")

            c1 = asyncio.create_task(child("c1"))
            c2 = asyncio.create_task(child("c2"))
            await asyncio.wait_for(first_in.wait(), timeout=5)
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(asyncio.gather(c1, c2), timeout=5)
            assert sorted(outcomes) == ["c1-acquired", "c2-conflict"] or \
                sorted(outcomes) == ["c1-conflict", "c2-acquired"]

    asyncio.run(main())


def test_wait_graph_fanout_and_cancel_keeps_other_edges(
        tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path)

    async def main() -> None:
        async def wait_until(pred) -> None:
            for _ in range(10_000):
                if pred():
                    return
                await asyncio.sleep(0)
            raise AssertionError("timed out waiting for wait-graph state")

        conflicts: list[BaseException] = []
        acquired: list[str] = []
        beta_ready = asyncio.Event()
        gamma_ready = asyncio.Event()
        proceed = asyncio.Event()
        release_beta = asyncio.Event()
        release_gamma = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def hold_beta() -> None:
            async with registry.lease("beta"):
                beta_ready.set()
                await release_beta.wait()

        async def hold_gamma() -> None:
            async with registry.lease("gamma"):
                gamma_ready.set()
                await proceed.wait()
                try:
                    async with registry.lease("alpha"):
                        pass
                except WorkspaceTurnConflict as error:
                    conflicts.append(error)
                await release_gamma.wait()

        t_beta = loop.create_task(hold_beta(), context=contextvars.Context())
        t_gamma = loop.create_task(hold_gamma(), context=contextvars.Context())
        try:
            async with registry.lease("alpha"):
                await asyncio.wait_for(
                    asyncio.gather(beta_ready.wait(), gamma_ready.wait()),
                    timeout=5)

                async def grab(bot: str) -> None:
                    async with registry.lease(bot):
                        acquired.append(bot)

                w_beta = asyncio.create_task(grab("beta"))
                w_gamma = asyncio.create_task(grab("gamma"))
                await wait_until(
                    lambda: any(
                        {"beta", "gamma"} <= set(targets)
                        for targets in registry._waiting.values()))
                proceed.set()
                await wait_until(lambda: conflicts)
                assert len(conflicts) == 1
                w_beta.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await w_beta
                assert any(
                    "gamma" in targets
                    for targets in registry._waiting.values())
                assert all(
                    "beta" not in targets
                    for targets in registry._waiting.values())
                release_gamma.set()
                await asyncio.wait_for(w_gamma, timeout=5)
            assert acquired == ["gamma"]
        finally:
            proceed.set()
            release_beta.set()
            release_gamma.set()
            await asyncio.gather(t_beta, t_gamma)

    asyncio.run(main())


def test_lease_cycle_raises_conflict_not_deadlock(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path)

    async def main() -> None:
        barrier = asyncio.Event()
        conflict: list[BaseException] = []

        async def bot_a() -> None:
            async with registry.lease("alpha"):
                barrier.set()
                await asyncio.sleep(0)
                try:
                    async with registry.lease("beta"):
                        pass
                except WorkspaceTurnConflict as error:
                    conflict.append(error)

        async def bot_b() -> None:
            async with registry.lease("beta"):
                await barrier.wait()
                async with registry.lease("alpha"):
                    pass

        await asyncio.wait_for(asyncio.gather(bot_a(), bot_b()), timeout=5)
        assert len(conflict) == 1

    asyncio.run(main())


def test_lease_cancel_while_queued_clears_wait_edge(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path)

    async def main() -> None:
        async with registry.lease("coder"):
            loop = asyncio.get_running_loop()
            waiter = loop.create_task(
                _nested(registry), context=contextvars.Context())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert registry._waiting == {}

    async def _nested(reg: WorkspaceRegistry) -> None:
        async with reg.lease("helper"):
            async with reg.lease("coder"):
                pass

    asyncio.run(main())


def test_claimed_flag_validates_against_lease_owner(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    async def main() -> None:
        card = type("C", (), {"bot_id": "coder"})()
        with pytest.raises(WorkspaceTurnConflict):
            await runtime.run_bot_turn_detailed(
                scope=None, card=card, room_id="r", text="x",
                turn_cfg=None, workspace_claimed=True,
            )

    asyncio.run(main())
