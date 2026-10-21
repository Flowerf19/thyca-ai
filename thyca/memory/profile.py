"""Shared USER.md profile block: auto-managed ``## Profile`` section.

The block is the write side of the profile aggregation (TURN-CURATE):
Thyca's scheduled turn decides the full desired line set, then calls the
``profile_update`` tool; this module owns the safe file mechanics — the
LLM never touches USER.md directly. Merge semantics: the auto block is
replaced wholesale (add/replace/remove), manual user content outside the
markers is never touched, and legacy ``## Habits`` auto blocks (the old
name) stay readable and are stripped only by the explicit cleanup pass.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from thyca.config import atomic_write_text
from thyca.memory.habits import (
    AUTO_END as LEGACY_AUTO_END,
)
from thyca.memory.habits import (
    AUTO_START as LEGACY_AUTO_START,
)

PROFILE_AUTO_START = "<!-- profile:auto:start -->"
PROFILE_AUTO_END = "<!-- profile:auto:end -->"

PROFILE_COMMENT = (
    "<!-- Profile Thyca tự tổng hợp từ quan sát của các bot (auto); "
    "nội dung user tự khai luôn thắng. -->"
)

LEGACY_HEADING = "## Habits"
PROFILE_HEADING = "## Profile"


def parse_profile_lines(text: str) -> list[str]:
    """Auto profile lines from the ``## Profile`` block, the legacy
    ``## Habits`` auto block, or both. ``- label`` lines inside the
    markers only; the legacy markers keep old data readable until the
    cleanup pass runs."""
    return profile_block_lines(text) + legacy_profile_lines(text)


def profile_block_lines(text: str) -> list[str]:
    """Lines from the ``## Profile`` auto block only."""
    return _block_lines(text, PROFILE_AUTO_START, PROFILE_AUTO_END)


def legacy_profile_lines(text: str) -> list[str]:
    """Lines from the legacy ``## Habits`` auto block only."""
    return _block_lines(text, LEGACY_AUTO_START, LEGACY_AUTO_END)


def maybe_strip_legacy_block(
    path: Path, lock_for: Callable[[Path], object] | None = None
) -> bool:
    """Strip the legacy ``## Habits`` auto block once every legacy line is
    already present in the ``## Profile`` block (migration complete).

    Deterministic and safe: never removes a line that was not carried
    over. Returns True when the file was rewritten."""
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return False
    legacy = legacy_profile_lines(text)
    if not legacy:
        return False
    profile_keys = {_normalized(line) for line in profile_block_lines(text)}
    if not all(_normalized(line) in profile_keys for line in legacy):
        return False
    stripped = strip_legacy_habits_block(text)
    if stripped == text:
        return False
    lock_ctx = lock_for(path) if lock_for is not None else nullcontext()
    with lock_ctx:
        atomic_write_text(path, stripped)
    return True


def update_profile_block(
    path: Path,
    lines: list[str],
    lock_for: Callable[[Path], object] | None = None,
) -> tuple[int, int]:
    """Replace the auto lines of the ``## Profile`` block with ``lines``.

    Creates the section (heading + comment + markers) when missing.
    Returns ``(added, removed)`` — counts of auto lines inserted and
    dropped. Manual lines and other sections are never touched; the
    legacy ``## Habits`` auto block is read-only here (see
    ``strip_legacy_habits_block``).
    """
    cleaned = _clean_lines(lines)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    old = _block_lines(text, PROFILE_AUTO_START, PROFILE_AUTO_END)
    lock_ctx = lock_for(path) if lock_for is not None else nullcontext()
    with lock_ctx:
        if PROFILE_HEADING not in text:
            text = text.rstrip("\n") + (
                f"\n\n## Profile\n{PROFILE_COMMENT}\n"
                f"{PROFILE_AUTO_START}\n{PROFILE_AUTO_END}\n"
            )
        new_text = _replace_block(text, PROFILE_AUTO_START, PROFILE_AUTO_END, cleaned)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, new_text)
    old_keys = {_normalized(line) for line in old}
    new_keys = {_normalized(line) for line in cleaned}
    return len(new_keys - old_keys), len(old_keys - new_keys)


def strip_legacy_habits_block(text: str) -> str:
    """Remove the legacy ``## Habits`` auto block.

    The heading survives when manual bullets remain outside the markers;
    the whole section (heading included) drops when it becomes empty.
    Idempotent — returns ``text`` unchanged when no legacy block exists.
    """
    if LEGACY_AUTO_START not in text:
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    total = len(lines)
    while index < total:
        stripped = lines[index].strip()
        if stripped == LEGACY_HEADING:
            manual: list[str] = []
            skipping = False
            index += 1
            while index < total and not lines[index].strip().startswith("## "):
                item = lines[index].strip()
                if item == LEGACY_AUTO_START:
                    skipping = True
                elif item == LEGACY_AUTO_END:
                    skipping = False
                elif skipping:
                    pass
                elif item.startswith("<!--"):
                    pass  # auto-generated comment, not manual content
                elif item:
                    manual.append(lines[index])
                index += 1
            if manual:
                out.append(f"{LEGACY_HEADING}\n")
                out.extend(manual)
            # Drop blank lines left between this section and the next one.
            while index < total and not lines[index].strip():
                index += 1
            continue
        out.append(lines[index])
        index += 1
    return "".join(out)


def _block_lines(text: str, start: str, end: str) -> list[str]:
    if start not in text:
        return []
    _, _, tail = text.partition(start)
    block, _, _ = tail.partition(end)
    return [
        line.strip()[2:].strip()
        for line in block.splitlines()
        if line.strip().startswith("- ") and not line.strip().startswith("<!--")
    ]


def _clean_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if not isinstance(line, str):
            continue
        cleaned = " ".join(line.split())
        if not cleaned or "<!--" in cleaned or cleaned.startswith("## "):
            continue
        if cleaned not in out:
            out.append(cleaned)
    return out


def _replace_block(text: str, start: str, end: str, lines: list[str]) -> str:
    head, sep, tail = text.partition(start)
    _block, _, rest = tail.partition(end)
    block = "\n" + "".join(f"- {line}\n" for line in lines)
    return head + sep + block + end + rest


def _normalized(line: str) -> str:
    return " ".join(line.split()).casefold()