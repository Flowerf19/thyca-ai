"""Private-followup consent surface in the rebuilt UI — webui/ui/**.

The old UI rendered structured question cards as clickable chips
(pages/chat/team-cards.js questionCard + composer.js sendChoiceText posting
{pending_id, revision, choice} to /api/sessions/<id>/private-followup).
That wiring was NOT ported to webui/ui/**: no questionCard, no team-choice
chips, no private-followup fetch exists in the new client. A stored card
message (role=assistant, meta.kind='card', meta.card={...}) now degrades to
a plain transcript article showing the card title — the consent decision
itself has no UI surface yet.

The backend endpoint and service contract remain live and are covered by
tests/test_private_followups.py and tests/test_private_followup_integration.py;
this file pins the client-side state so a future re-introduction of the
consent UI changes this file loudly instead of silently.
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
  renderTranscript, normalizeBot, getBot, bots, groups,
};"""

_SCRIPT = r"""
import { installDom, loadScripts, CHAT_CHAIN } from './tests/webui_dom.mjs';
const doc = installDom();
const ui = loadScripts(CHAT_CHAIN, TAIL);
const result = {};

ui.bots.push(ui.normalizeBot({ id: 'thyca', name: 'Thyca', role: 'bạn', agent_status: { state: 'idle' } }, 0));

// A stored private-followup consent card as post_bot_dm_card persists it:
// content = card title, meta.kind='card', meta.card carries the payload.
const detail = { messages: [
  { role: 'user', content: 'gửi bản này về nhóm giúp mình' },
  { role: 'assistant',
    content: 'Cho phép gửi bản này về nhóm?',
    meta: { bot_id: 'thyca', kind: 'card', card: {
      type: 'question',
      title: 'Cho phép gửi bản này về nhóm?',
      private_followup: { pending_id: 'pf-9', revision: 2 },
      options: [
        { id: 'approve', label: 'Cho phép gửi bản này' },
        { id: 'keep_private', label: 'Giữ riêng' },
        { id: 'cancel', label: 'Huỷ yêu cầu' },
      ],
    } } },
] };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript(detail);
const articles = [...doc.querySelectorAll('#message-list .message-assistant')];
result.card = {
  assistants: articles.length,
  copy: articles[0]?.querySelector('.live-copy')?.textContent ?? null,
  buttons: doc.querySelectorAll('#message-list .message-assistant button:not([data-copy]):not(.again)').length,
  choiceChips: doc.querySelectorAll('#message-list .team-choice').length,
  pendingHooks: doc.querySelectorAll('#message-list [data-pending-id]').length
    + doc.querySelectorAll('#message-list [data-choice]').length,
};
console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if binary is None:
        pytest.skip("node not installed")
    return binary


@pytest.fixture(scope="module")
def card_render(node: str) -> dict:
    script = _SCRIPT.replace("TAIL", json.dumps(_TUI_TAIL))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True, cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_no_consent_wiring_exists_in_new_ui() -> None:
    """Pin the removal: nothing in webui/ui/** posts to private-followup or
    renders the old questionCard/team-choice consent chips."""
    blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in UI.rglob("*")
        if path.is_file() and path.suffix in {".js", ".html"}
    )
    assert "private-followup" not in blob
    assert "private_followup" not in blob
    assert "questionCard" not in blob
    assert "team-choice" not in blob
    assert "pending_id" not in blob


def test_stored_card_degrades_to_plain_article(card_render: dict) -> None:
    """Until a consent UI is reintroduced, a stored card message renders as
    a normal assistant row carrying the card title — no clickable chips and
    no pending_id/choice hooks that could fire the decision endpoint."""
    assert card_render["card"]["assistants"] == 1
    assert card_render["card"]["copy"] == "Cho phép gửi bản này về nhóm?"
    assert card_render["card"]["buttons"] == 0
    assert card_render["card"]["choiceChips"] == 0
    assert card_render["card"]["pendingHooks"] == 0
