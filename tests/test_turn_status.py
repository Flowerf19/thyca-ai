"""Node tests for the operational event -> status text mapper — webui/js/turn-status.js.

Runs in Node with --input-type=module so no DOM is needed; mirrors the eval
helper style of tests/test_ndjson.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "turn-status.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ statusTextForEvent }} from '{SCRIPT.as_posix()}';\n"
        f"console.log(JSON.stringify({expression}));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_no_event_is_null(node: str) -> None:
    assert _eval(node, "statusTextForEvent(null)") is None
    assert _eval(node, "statusTextForEvent(undefined)") is None
    assert _eval(node, "statusTextForEvent('x')") is None


def test_unknown_type_is_null(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "no.such.event"})') is None
    assert _eval(node, 'statusTextForEvent({})') is None


def test_turn_accepted(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "turn.accepted"})') == "Đã nhận lượt…"


def test_llm_started_round(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "llm.started", round: 1})') == "Đang xử lý vòng 1…"
    assert _eval(node, 'statusTextForEvent({type: "llm.started", round: 3})') == "Đang xử lý vòng 3…"


def test_llm_finished_tool_count(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "llm.finished", round: 1, tool_count: 0})') == "Đang hoàn tất câu trả lời…"
    assert _eval(node, 'statusTextForEvent({type: "llm.finished", round: 1, tool_count: 2})') == "Đã chọn 2 công cụ…"


def test_tool_started(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "tool.started", round: 1, call_id: "call-1", name: "bash"})') == "Đang dùng bash…"


def test_tool_finished_ok(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "tool.finished", round: 1, call_id: "call-1", name: "bash", ok: true})') == "bash đã xong…"


def test_tool_finished_error(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "tool.finished", round: 1, call_id: "call-1", name: "bash", ok: false})') == "bash gặp lỗi, đang xử lý tiếp…"


def test_skill_events(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "skill.started", round: 1, call_id: "call-1", name: "codereview"})') == "Đang mở skill codereview…"
    assert _eval(node, 'statusTextForEvent({type: "skill.finished", round: 1, call_id: "call-1", name: "codereview", ok: true})') == "Đã mở skill codereview…"
    assert _eval(node, 'statusTextForEvent({type: "skill.finished", round: 1, call_id: "call-1", name: "codereview", ok: false})') == "Skill codereview không đọc được, đang xử lý tiếp…"


def test_missing_skill_name_uses_skill_fallback(node: str) -> None:
    # Backend falls back to "skill"; the client must not say "tool" here.
    assert _eval(node, 'statusTextForEvent({type: "skill.started", round: 1, call_id: "call-1"})') == "Đang mở skill skill…"


def test_session_naming(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "session.naming.started"})') == "Đang đặt tên phiên…"
    assert _eval(node, 'statusTextForEvent({type: "session.naming.finished", updated: true})') == "Đang hoàn tất…"
    assert _eval(node, 'statusTextForEvent({type: "session.naming.finished", updated: false})') == "Đang hoàn tất…"


def test_turn_completed_and_failed(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "turn.completed"})') == "Đã xong."
    assert _eval(node, 'statusTextForEvent({type: "turn.failed", code: "llm_error"})') == "Lượt đã dừng."


def test_missing_tool_name_uses_public_fallback(node: str) -> None:
    # Sanitized names arrive as "tool"; a missing name must still produce a
    # string and never crash.
    assert _eval(node, 'statusTextForEvent({type: "tool.started", round: 1, call_id: "call-1"})') == "Đang dùng tool…"
    assert _eval(node, 'statusTextForEvent({type: "tool.finished", round: 1, call_id: "call-1", ok: true})') == "tool đã xong…"
    assert _eval(node, 'statusTextForEvent({type: "tool.finished", round: 1, call_id: "call-1", name: "", ok: false})') == "tool gặp lỗi, đang xử lý tiếp…"


def test_sanitized_name_is_kept_as_public_identifier(node: str) -> None:
    # The server already sanitizes provider text; the client renders it as-is.
    assert _eval(node, 'statusTextForEvent({type: "tool.started", round: 1, call_id: "call-1", name: "web_search_1"})') == "Đang dùng web_search_1…"


def test_llm_started_missing_round_is_null(node: str) -> None:
    assert _eval(node, 'statusTextForEvent({type: "llm.started"})') is None
    assert _eval(node, 'statusTextForEvent({type: "llm.started", round: "x"})') is None
