"""Node tests for live ambient copy — webui/js/chat/ambient.js.

Runs in Node with --input-type=module; mirrors tests/test_turn_status.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "webui" / "js" / "chat" / "ambient.js"

FIXED_40 = [
    "đang ngân nga…",
    "llm đang lấy hơi",
    "nghe nhịp trong đầu",
    "gạch nháp lên lề",
    "nắn lại giai điệu",
    "nghĩ một nhịp đã",
    "nghỉ beat…",
    "để mực khô một chút",
    "im trên khuông",
    "giữ nhịp lặng",
    "đếm lại từ đầu phách",
    "đánh lại nhịp vừa rồi",
    "lật trang bash",
    "gõ nhịp shell",
    "chạy một phách lệnh",
    "lắng tiếng máy gõ",
    "đọc khuông file",
    "lit lại dòng cũ",
    "soi nốt trên trang",
    "tẩy và viết lại",
    "sửa nhịp trên giấy",
    "ghi đè một câu",
    "kéo mực qua chỗ lệch",
    "dán vào sổ nhớ",
    "ghim mẩu bên lề",
    "cất phách vào ngăn",
    "gọi nhịp phụ ngoài sổ",
    "mở ngăn dụng cụ thêm",
    "chạy tool khách",
    "nối một nhịp MCP",
    "mở bài skill",
    "giở trang skill",
    "luyện một mẫu skill",
    "gấp skill lại",
    "chốt nhịp.",
    "gác bút, xong trang.",
    "phách lệch.",
    "mực lem, dừng lại.",
    "gõ một nhịp hệ thống",
    "chỉnh lại trang trong sổ",
]


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        "import { ambientLineForEvent, bucketForEvent, AMBIENT, BUILTIN_TOOLS } "
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


def test_catalog_is_exactly_the_40_fixed_lines(node: str) -> None:
    catalog = _eval(
        node,
        "Object.values(AMBIENT).flat()",
    )
    assert catalog == FIXED_40
    assert len(set(catalog)) == 40


def test_builtin_allowlist_matches_registry(node: str) -> None:
    assert _eval(node, "BUILTIN_TOOLS") == [
        "bash",
        "read",
        "write",
        "edit",
        "memory_remember",
        "memory_search",
        "memory_recent",
        "memory_get",
        "memory_forget",
        "memory_reinforce",
        "memory_update",
    ]


@pytest.mark.parametrize(
    ("expression", "bucket"),
    [
        ("bucketForEvent(null)", "wait"),
        ("bucketForEvent(undefined)", "wait"),
        ("bucketForEvent('x')", "wait"),
        ("bucketForEvent({})", "wait"),
        ('bucketForEvent({type: "session.naming.started"})', "wait"),
        ('bucketForEvent({type: "llm.started", round: 1})', "llm"),
        ('bucketForEvent({type: "llm.finished", round: 1, tool_count: 0})', "llm"),
        ('bucketForEvent({type: "turn.accepted"})', "llm"),
        ('bucketForEvent({type: "llm.retry", attempt: 1, max_attempts: 3})', "retry"),
        ('bucketForEvent({type: "turn.completed"})', "completed"),
        ('bucketForEvent({type: "turn.failed"})', "failed"),
        ('bucketForEvent({type: "skill.started", name: "codereview"})', "skill"),
        ('bucketForEvent({type: "skill.finished", name: "codereview", ok: true})', "skill"),
        ('bucketForEvent({type: "tool.started", name: "bash"})', "bash"),
        ('bucketForEvent({type: "tool.finished", name: "bash", ok: true})', "bash"),
        ('bucketForEvent({type: "tool.started", name: "read"})', "read"),
        ('bucketForEvent({type: "tool.started", name: "write"})', "write"),
        ('bucketForEvent({type: "tool.started", name: "edit"})', "write"),
        ('bucketForEvent({type: "tool.started", name: "memory_remember"})', "memory"),
        ('bucketForEvent({type: "tool.started", name: "memory_search"})', "memory"),
        ('bucketForEvent({type: "tool.started", name: "memory_recent"})', "memory"),
        ('bucketForEvent({type: "tool.started", name: "web_search"})', "addon"),
        ('bucketForEvent({type: "tool.started", name: "chrome_tabs"})', "addon"),
        ('bucketForEvent({type: "tool.started"})', "sys"),
        ('bucketForEvent({type: "tool.started", name: ""})', "sys"),
    ],
)
def test_bucket_for_event(node: str, expression: str, bucket: str) -> None:
    assert _eval(node, expression) == bucket


def test_line_stays_inside_its_bucket(node: str) -> None:
    cases = [
        ('{type: "llm.started", round: 1}', "llm"),
        ("null", "wait"),
        ('{type: "llm.retry", attempt: 2, max_attempts: 3}', "retry"),
        ('{type: "tool.started", name: "bash", call_id: "c1"}', "bash"),
        ('{type: "tool.started", name: "read", call_id: "c1"}', "read"),
        ('{type: "tool.started", name: "edit", call_id: "c1"}', "write"),
        ('{type: "tool.started", name: "memory_remember", call_id: "c1"}', "memory"),
        ('{type: "tool.started", name: "web_search", call_id: "c1"}', "addon"),
        ('{type: "skill.started", name: "codereview"}', "skill"),
        ('{type: "turn.completed"}', "completed"),
        ('{type: "turn.failed"}', "failed"),
        ('{type: "tool.started"}', "sys"),
    ]
    for event, bucket in cases:
        line = _eval(node, f"ambientLineForEvent({event})")
        allowed = _eval(node, f"AMBIENT.{bucket}")
        assert line in allowed


def test_memory_remember_is_sys_memory_not_addon(node: str) -> None:
    assert _eval(node, 'bucketForEvent({type: "tool.started", name: "memory_remember"})') == (
        "memory"
    )
    line = _eval(
        node,
        'ambientLineForEvent({type: "tool.started", name: "memory_remember", call_id: "c1"})',
    )
    assert line in {
        "dán vào sổ nhớ",
        "ghim mẩu bên lề",
        "cất phách vào ngăn",
    }


def test_unknown_event_never_invents_copy(node: str) -> None:
    line = _eval(node, 'ambientLineForEvent({type: "no.such.event"})')
    assert line in FIXED_40
    assert _eval(node, "AMBIENT.wait").count(line) == 1


def test_staff_module_is_gone() -> None:
    assert not (ROOT / "webui" / "js" / "staff").exists()
    assert not (ROOT / "webui" / "css" / "workspace" / "staff.css").exists()

