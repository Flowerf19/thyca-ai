"""Bot roster and DM routes use isolated Thyca roots."""
from __future__ import annotations

import asyncio
import contextvars
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.bots.registry import RegistryError
from thyca.config import default_config, load, save
from thyca.llm.llm_base import ChatReply
from thyca.memory.facade import MemoryFacade
from thyca.serve import default_webui, make_server


@dataclass
class IdleLLM:
    async def chat(self, messages, tools=None):
        return ChatReply(content="unused")


def _app(root: Path) -> ChatApp:
    save(default_config(), root / "config.json")
    app = ChatApp(root, load(root / "config.json"), connect=IdleLLM())
    app._bots.create(BotCard(
        bot_id="coder", name="coder", role="Coder", description="Writes code.",
        persona="You write code.",
    ))
    app._bots.create(BotCard(
        bot_id="designer", name="designer", role="Designer",
        description="Designs interfaces.", persona="You design interfaces.",
    ))
    return app


def _start(root: Path, app: ChatApp):
    server = make_server(
        host="127.0.0.1", port=0, webui=default_webui(),
        facade=MemoryFacade(root, timezone_name="Asia/Ho_Chi_Minh"), chat=app,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread: threading.Thread, app: ChatApp) -> None:
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()
    app.shutdown()


def _request(server, path: str, method: str = "GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(
        f"http://127.0.0.1:{server.server_address[1]}{path}",
        data=data, method=method, headers=headers,
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_bot_write_routes_are_removed_but_roster_and_dm_remain(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    card_path = app._bots.card_path("coder")
    before = card_path.read_bytes()
    server, thread = _start(tmp_path, app)
    try:
        for method, payload in (("PATCH", {"name": "Changed"}), ("DELETE", None)):
            status, body = _request(server, "/api/bots/coder", method, payload)
            assert status == 404
            assert body == {"error": "bot not found"}
        status, roster = _request(server, "/api/bots")
        assert status == 200
        assert {bot["id"] for bot in roster["bots"]} >= {"thyca", "coder"}
        assert card_path.read_bytes() == before
        assert app._bots.exists("coder")
        status, dm = _request(server, "/api/bots/coder/dm")
        assert status == 200
        assert dm["dm"] == "coder"
        assert app._bots.exists("coder")
    finally:
        _stop(server, thread, app)


def test_runtime_queued_turn_does_not_recreate_deleted_bot_workspace(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    card = app._bots.require("coder")
    bot_dir = app._bots.bot_dir("coder")

    async def queued_turn() -> None:
        async with app._runtime.workspaces.lease("coder"):
            task = asyncio.create_task(
                app._runtime.run_bot_turn_detailed(
                    scope=None,
                    card=card,
                    room_id="queued-runtime-room",
                    text="queued",
                    turn_cfg=app._cfg,
                ),
                context=contextvars.Context(),
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            app._bots.delete("coder")
        with pytest.raises(RegistryError):
            await task

    try:
        app._submit(queued_turn())
        assert not bot_dir.exists()
        assert not (bot_dir / "workspace" / "registry.json").exists()
    finally:
        app.shutdown()
