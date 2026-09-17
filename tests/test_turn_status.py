"""Node tests for live chat status helpers — webui/backend/chat-status.js."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "thyca" / "webui" / "backend" / "chat-status.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        "import { brandState, SEND_ERROR_STATUS, TURN_FAILED_STATUS, usageLine } "
        f"from '{SCRIPT.as_posix()}';\n"
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


def test_brand_state(node: str) -> None:
    assert _eval(node, "brandState(null)") == "busy"
    assert _eval(node, 'brandState({type: "llm.started"})') == "busy"
    assert _eval(node, 'brandState({type: "turn.completed"})') == "idle"
    assert _eval(node, 'brandState({type: "turn.failed"})') == "error"


def test_public_status_constants(node: str) -> None:
    assert _eval(node, "SEND_ERROR_STATUS") == "Không gửi được — thử lại."
    assert _eval(node, "TURN_FAILED_STATUS") == "Lượt đã dừng."


def test_usage_line_plain_list_while_calls_run(node: str) -> None:
    assert _eval(node, 'usageLine([], ["bash"])') == {
        "label": "Đang dùng:",
        "body": "bash",
    }
    assert _eval(node, 'usageLine(["bash"], ["bash", "create-skill"])') == {
        "label": "Đang dùng:",
        "body": "bash, create-skill",
    }


def test_usage_line_tally_once_settled(node: str) -> None:
    assert _eval(
        node, 'usageLine(["bash", "bash", "memory_search", "create-skill", "edit"], [])'
    ) == {
        "label": "Đã dùng:",
        "body": "bash x2, memories x1, create-skill x1, edit x1",
    }


def test_usage_line_is_null_when_nothing_ran(node: str) -> None:
    assert _eval(node, "usageLine([], [])") is None
    assert _eval(node, 'usageLine([""], [null])') is None
