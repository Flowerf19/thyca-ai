"""Discord bridge prompt display (read-only investigation fix).

thyca-discord-mcp build_conversation_prompt posts one user turn shaped
``{trusted}\\n{policy}\\n{task}\\nUNTRUSTED_CONVERSATION job_ref=R START\\n``
``{compact conversation JSON}\\nUNTRUSTED_CONVERSATION job_ref=R END`` —
JSON snapshot: {assistant_uid, owner_uid, participants[{uid,
display_name}], messages[{id, sender_uid, text, reply_to?, timestamp?}],
new_message_id}; mentions arrive as ``<@uid>``. The chat display layer
renders only the framed conversation (names resolved, mentions mapped),
never the scaffolding or raw JSON; unparseable frames fall back to the
escaped inner text. Stored payloads and the LLM path are untouched.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_TUI_TAIL = """return { renderTranscript };"""

_FRAME = {
    "assistant_uid": "999",
    "owner_uid": "111",
    "participants": [
        {"uid": "111", "display_name": "Chủ kênh"},
        {"uid": "222", "display_name": "Bạn A"},
        {"uid": "999", "display_name": "Bot Thyca"},
    ],
    "messages": [
        {"id": "m1", "sender_uid": "222", "text": "chào <@111> nhé\nxuống dòng nè",
         "timestamp": "2026-10-26T10:00:00Z"},
        {"id": "m2", "sender_uid": "333333333333333333", "text": "tôi là ai <@000>",
         "reply_to": "m1"},
    ],
    "new_message_id": "m1",
}

_PROMPT = (
    "TRUSTED_METADATA sender_uid=222 sender_kind=other channel_id=1\n"
    "POLICY Identity by sender_uid.\n"
    "TASK Process the current message.\n"
    "UNTRUSTED_CONVERSATION job_ref=abc123 START\n"
    + json.dumps(_FRAME, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    + "\nUNTRUSTED_CONVERSATION job_ref=abc123 END"
)

_BROKEN = (
    "TRUSTED_METADATA sender_uid=222\n"
    "UNTRUSTED_CONVERSATION job_ref=abc123 START\n"
    "{not json <b>oops</b>\n"
    "UNTRUSTED_CONVERSATION job_ref=abc123 END"
)

_SCRIPT = r"""
import { installDom, loadScripts, CHAT_CHAIN } from './tests/webui_dom.mjs';
const doc = installDom();
const ui = loadScripts(CHAT_CHAIN, TAIL);
const list = doc.querySelector('#message-list');
list.innerHTML = ui.renderTranscript({ messages: [
  { role: 'user', content: PROMPT },
  { role: 'user', content: 'tin thường <b>nguyên</b>\nxuống dòng' },
  { role: 'user', content: BROKEN },
] });
console.log(JSON.stringify({ html: list.innerHTML }));
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if binary is None:
        pytest.skip("node not installed")
    return binary


@pytest.fixture(scope="module")
def framed(node: str) -> dict:
    script = (
        _SCRIPT.replace("TAIL", json.dumps(_TUI_TAIL))
        .replace("PROMPT", json.dumps(_PROMPT))
        .replace("BROKEN", json.dumps(_BROKEN))
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True, cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_framed_conversation_renders_names_not_json(framed: dict) -> None:
    html = framed["html"]
    assert "Bạn A" in html and "Chủ kênh" in html
    assert "@Chủ kênh" in html
    assert "xuống dòng nè" in html
    # Unknown sender falls back to a shortened uid; unknown mention likewise.
    assert "333333" in html and "@000" in html
    assert "Từ Discord" in html
    assert "UNTRUSTED_CONVERSATION" not in html
    assert "TRUSTED_METADATA" not in html
    assert '"sender_uid"' not in html and "job_ref" not in html


def test_plain_user_message_unchanged(framed: dict) -> None:
    html = framed["html"]
    assert "tin thường &lt;b&gt;nguyên&lt;/b&gt;<br>xuống dòng" in html


def test_broken_frame_falls_back_escaped_without_markers(framed: dict) -> None:
    html = framed["html"]
    assert "{not json &lt;b&gt;oops&lt;/b&gt;" in html
    assert html.count("UNTRUSTED_CONVERSATION") == 0
