"""Contract §6: frozen FE↔BE surface asserted in one place.

Any change here is a contract break: stop and report instead of editing
these assertions to fit new behavior.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from thyca.app.chat_app import ChatApp
from thyca.config import load, save
from seeded_config import seeded_config
from thyca.serve import default_webui, make_server

WEBUI = default_webui()


def _chat(tmp_path: Path) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"))


def _serve(app: ChatApp, tmp_path: Path):
    import threading

    from thyca.memory.facade import MemoryFacade

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
    app = _chat(tmp_path)
    httpd = _serve(app, tmp_path)
    try:
        bots = _get(httpd, "/api/bots")
        assert isinstance(bots["bots"], list)
        assert bots["bots"][0]["id"] == "thyca"
        # POST /api/sessions accepts bots?.
        created = _post(httpd, "/api/sessions", {"bots": ["thyca"]})
        assert created["bots"] == ["thyca"]
        # Session detail payload gains bots:[] (checked before the next
        # create: blank sessions are pruned by discard_empty on create).
        detail = _get(httpd, f"/api/sessions/{created['id']}")
        assert detail["bots"] == ["thyca"]
        created2 = _post(httpd, "/api/sessions", {})
        assert created2["bots"] == ["thyca"]
        # Session list payloads gain bots:[] (blank rooms are unlisted).
        listed = _get(httpd, "/api/sessions")
        assert all("bots" in session for session in listed["sessions"])
    finally:
        httpd.shutdown()
        app.shutdown()


def test_contract_session_bots_add_remove(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    httpd = _serve(app, tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C",
                                 description="D", persona="P"))
        created = _post(httpd, "/api/sessions", {"bots": ["thyca", "coder"]})
        assert created["kind"] == "group"
        out = _post(httpd, f"/api/sessions/{created['id']}/bots",
                    {"bot_id": "coder", "action": "remove"})
        assert out["bots"] == ["thyca"]
        out = _post(httpd, f"/api/sessions/{created['id']}/bots",
                    {"bot_id": "coder", "action": "add"})
        assert out == {"ok": True, "id": created["id"], "bots": ["thyca", "coder"]}
    finally:
        httpd.shutdown()
        app.shutdown()


def test_contract_message_meta_keys(tmp_path: Path) -> None:
    from thyca.core.protocol import Message

    row = Message(role="assistant", content="hi",
                  meta={"bot_id": "coder", "kind": "message",
                        "trace_id": "tr_x", "parent_span_id": None})
    assert set(row.meta) >= {"bot_id", "kind", "trace_id", "parent_span_id"}
