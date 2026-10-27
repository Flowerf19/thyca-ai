"""Usage/context meter surface in the rebuilt UI — webui/ui/**.

The old composer meter (webui/js/chat/meter.js: sumLastTurnUsage,
meterText, lastTurnTools, #meter/#tool-meter) and shared/js/format.js were
dropped in the UI rebuild. Their replacements:
  - dashboard/js/dashboard.js keeps page-local vi-VN formatters (fmtInt,
    fmtCost, share) for the /api/traces usage screen,
  - share/sidebar.js fillDrawerContext renders the per-bot context meter
    (.ctx-meter/.ctx-bar, bands mirroring context_budget.py) fed by
    share/data.js botContext()'s dm -> room-operations fetch pair.
This file pins the removal, evaluates the real formatters, exercises the
real fillDrawerContext/botContext code paths, and keeps the backend
usage-meta contract that feeds the dashboard.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "thyca" / "webui" / "ui"

_TUI_TAIL = """return {
  fillDrawerContext, capabilitiesHtml, botContext, normalizeBot, getBot,
  updateCard, card, bots, groups,
};"""

_METER_SCRIPT = r"""
import { installDom, loadScripts, CHAT_CHAIN } from './tests/webui_dom.mjs';
const doc = installDom();
const ui = loadScripts(CHAT_CHAIN, TAIL);
const result = { calls: [], bands: {} };

const b = ui.normalizeBot({ id: 'thyca', name: 'Thyca', role: 'bạn', agent_status: { state: 'idle' } }, 0);
ui.bots.push(b);

// botContext: dm -> rooms/<dm.session_id||dm.id>/operations?scope=turn
let ops = { context: { ratio: 0.4, estimated_tokens: 120000, context_tokens: 200000 } };
globalThis.fetch = async (url) => {
  result.calls.push(url);
  if (url.endsWith('/dm')) return { ok: true, json: async () => ({ session_id: 'room-9', id: 'ignored' }) };
  if (url.includes('/operations')) return { ok: true, json: async () => ops };
  return { ok: false, status: 404, json: async () => ({ error: 'no route ' + url }) };
};
result.botContext = await ui.botContext('thyca');

// fillDrawerContext paints the meter inside the capabilities drawer markup.
doc.querySelector('#bot-drawer-body').innerHTML = ui.capabilitiesHtml(b);
for (const ratio of [0.4, 0.83, 0.96, 1.2]) {
  ops = { context: { ratio, estimated_tokens: 120000, context_tokens: 200000 } };
  await ui.fillDrawerContext(b);
  const bar = doc.querySelector('#bot-drawer-body .ctx-bar');
  const slot = doc.querySelector('#bot-drawer-body [data-drawer-context]');
  result.bands[ratio] = { cls: bar.className, width: bar.style.width, title: slot.title };
}
console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


@pytest.fixture(scope="module")
def meter(node: str) -> dict:
    script = _METER_SCRIPT.replace("TAIL", json.dumps(_TUI_TAIL))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_bot_context_fetches_dm_then_room_operations(meter: dict) -> None:
    assert meter["calls"][:2] == [
        "/api/bots/thyca/dm",
        "/api/bots/thyca/rooms/room-9/operations?scope=turn",
    ]
    assert meter["botContext"]["context"]["ratio"] == 0.4


def test_drawer_context_meter_bands_and_width(meter: dict) -> None:
    assert meter["bands"]["0.4"]["cls"] == "ctx-bar ctx-ok"
    assert meter["bands"]["0.4"]["width"] == "40%"
    assert meter["bands"]["0.4"]["title"] == "~120.000 / 200.000 token · 40%"
    assert meter["bands"]["0.83"]["cls"] == "ctx-bar ctx-warn"
    assert meter["bands"]["0.83"]["width"] == "83%"
    assert meter["bands"]["0.96"]["cls"] == "ctx-bar ctx-danger"
    assert meter["bands"]["0.96"]["width"] == "96%"
    # Ratio past 100% still caps the bar at full width.
    assert meter["bands"]["1.2"]["cls"] == "ctx-bar ctx-danger"
    assert meter["bands"]["1.2"]["width"] == "100%"


def test_session_detail_carries_meta_for_meter(tmp_path: Path) -> None:
    from test_serve_chat import FakeLLM, _chat

    from thyca.llm.llm_base import ChatReply

    llm = FakeLLM(
        ChatReply(
            content="pong",
            usage={"prompt_tokens": 100, "cached_tokens": 20, "completion_tokens": 5},
        )
    )
    app = _chat(tmp_path, llm)
    try:
        created = app.create()
        turned = app.turn(created["id"], "ping")
        assistants = [m for m in turned["messages"] if m["role"] == "assistant"]
        assert assistants and assistants[0]["meta"]["usage"]["prompt_tokens"] == 100
        loaded = app.get_payload(created["id"])
        assert loaded["messages"] == turned["messages"]
        assert loaded["messages"][1]["meta"]["usage"]["cached_tokens"] == 20
    finally:
        app.shutdown()
