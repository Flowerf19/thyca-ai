"""Guards for dropped live-copy modules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_staff_module_is_gone() -> None:
    assert not (ROOT / "thyca" / "webui" / "backend" / "staff").exists()
    assert not (ROOT / "thyca" / "webui" / "staff").exists()


def test_ambient_module_is_gone() -> None:
    assert not (ROOT / "thyca" / "webui" / "backend" / "chat-ambient.js").exists()
