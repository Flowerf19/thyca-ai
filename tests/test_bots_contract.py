"""Contract §6: frozen FE↔BE surface asserted in one place.

The DM/group hard cut replaced /api/sessions* with explicit doors; this
file pins the new surface with the same strength as the old one.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

from thyca.app.chat_app import ChatApp
from thyca.configs import load, save
from seeded_config import seeded_config
from thyca.serve import default_webui, make_server

WEBUI = default_webui()


def _chat(tmp_path: Path) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"))


def _serve(app: ChatApp, tmp_path: Path):
    import threading

    from thyca.memories.facade import MemoryFacade

    httpd = make_server(host="127.0.0.1", port=0, webui=WEBUI,
                        facade=MemoryFacade(tmp_path), chat=app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _url(httpd, path: str) -> str:
    return f"http://127.0.0.1:{httpd.server_address[1]}{path}"


def _get(httpd, path: str) -> dict:
    with urlopen(_url(httpd, path), timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(httpd, path: str, payload: dict) -> dict:
    request = Request(_url(httpd, path), data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_contract_bots_endpoints(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    httpd = _serve(app, tmp_path)
    try:
        bots = _get(httpd, "/api/bots")
        assert isinstance(bots["bots"], list)
        assert bots["bots"][0]["id"] == "thyca"
        # The fixed DM door: GET /api/bots/<bot>/dm, typed identity out.
        dm = _get(httpd, "/api/bots/thyca/dm")
        assert dm["kind"] == "dm" and dm["bot_id"] == "thyca"
        assert dm["bots"] == ["thyca"]
        assert "id" not in dm and "session_id" not in dm
        # Conversation list payloads carry kind + typed identity + bots.
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C",
                                 description="D", persona="P"))
        _post(httpd, "/api/groups", {"bots": ["thyca", "coder"]})
        listed = _get(httpd, "/api/conversations")
        assert listed["conversations"]
        for conv in listed["conversations"]:
            assert "bots" in conv and conv["kind"] in ("dm", "group")
            assert ("bot_id" in conv) != ("group_id" in conv)
    finally:
        httpd.shutdown()
        app.shutdown()


def test_contract_group_bots_add_remove(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    httpd = _serve(app, tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C",
                                 description="D", persona="P"))
        created = _post(httpd, "/api/groups", {"bots": ["thyca", "coder"]})
        assert created["kind"] == "group" and created["group_id"].startswith("group-")
        out = _post(httpd, f"/api/groups/{created['group_id']}/bots",
                    {"bot_id": "coder", "action": "remove"})
        assert out["bots"] == ["thyca"]
        out = _post(httpd, f"/api/groups/{created['group_id']}/bots",
                    {"bot_id": "coder", "action": "add"})
        assert out == {"ok": True, "kind": "group", "group_id": created["group_id"],
                       "bots": ["thyca", "coder"]}
    finally:
        httpd.shutdown()
        app.shutdown()


def test_contract_message_meta_keys(tmp_path: Path) -> None:
    from thyca.core.protocol import Message

    row = Message(role="assistant", content="hi",
                  meta={"bot_id": "coder", "kind": "message",
                        "trace_id": "tr_x", "parent_span_id": None})
    assert set(row.meta) >= {"bot_id", "kind", "trace_id", "parent_span_id"}
