"""Chunk markdown into archived leafs. No I/O."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from thyca.core.protocol import estimate_tokens
from thyca.memory.heading import (
    iter_session_blocks,
    parse_heading,
    session_id,
    strip_comment,
)

_BULLET_RE = re.compile(r"^(\s*)([-*]|\d+\.)\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?。])\s+")

MAX_LEAF_CHARS = 800
MIN_LEAF_CHARS = 20


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    path: str
    source_kind: str
    timeline_day: str | None
    session_id: str
    session_title: str
    heading_raw: str
    leaf_ord: int
    line_start: int
    line_end: int
    text_raw: str
    text_norm: str
    content_hash: str
    expires_at: str | None = None
    forgotten_at: str | None = None
    project: str | None = None
    chat_session: str | None = None


class Chunker:
    """Split daily ``## HH:mm`` sessions and canonical files into leafs."""

    def chunk_markdown(
        self,
        path: Path | str,
        text: str,
        *,
        source_kind: str,
        timeline_day: str | None,
    ) -> list[Chunk]:
        path_s = str(path)
        sessions = _sessions(text, source_kind, timeline_day, path_s)
        chunks: list[Chunk] = []
        for session in sessions:
            leaves = _split_long(_merge_short(_leaves(session["body"], session["body_start"])))
            for ord_, leaf in enumerate(leaves, start=1):
                # Never blank: _leaves skips blank lines and every split
                # stage only emits non-blank parts, so no guard is needed.
                raw = leaf["text"]
                norm = self.normalize(raw)
                chunk_id = f"{session['session_id']}#{ord_}"
                payload = f"{session['heading']}\n{raw}".encode()
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        path=path_s,
                        source_kind=source_kind,
                        timeline_day=timeline_day,
                        session_id=session["session_id"],
                        session_title=session["title"],
                        heading_raw=session["heading"],
                        leaf_ord=ord_,
                        line_start=leaf["start"],
                        line_end=leaf["end"],
                        text_raw=raw,
                        text_norm=norm,
                        content_hash=hashlib.sha256(payload).hexdigest(),
                        expires_at=session.get("expires_at"),
                        forgotten_at=session.get("forgotten_at"),
                        project=session.get("project"),
                        chat_session=session.get("chat_session"),
                    )
                )
        return chunks

    @staticmethod
    def normalize(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text)
        stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
        # NFD does not decompose đ (U+0111) — map it to d explicitly.
        return stripped.lower().replace("đ", "d")


def _sessions(
    text: str, source_kind: str, timeline_day: str | None, path: str
) -> list[dict]:
    lines = text.splitlines()
    blocks = list(iter_session_blocks(lines, path))
    if not blocks:
        name = Path(path).stem.lower()
        sid = f"canonical#{name}" if source_kind == "canonical" else f"{timeline_day}#legacy"
        return [
            {
                "heading": "",
                "title": "",
                "session_id": sid,
                "body": text,
                "body_start": 1,
            }
        ]
    sessions: list[dict] = []
    for meta, entry, line_no, end in blocks:
        # Canonical sessions share the canonical# root with the no-heading
        # fallback so facade get (canonical#*) and stats accept them.
        prefix = timeline_day if source_kind == "daily" else f"canonical#{Path(path).stem.lower()}"
        body = "\n".join(lines[line_no + 1 : end])
        sessions.append(
            {
                "heading": strip_comment(lines[line_no]),
                "title": meta.title,
                "session_id": session_id(prefix, entry),
                "body": body,
                "body_start": line_no + 2,
                "expires_at": meta.expires_at,
                "forgotten_at": None,
                "project": meta.proj,
                "chat_session": meta.chat,
            }
        )
    return sessions


def _leaves(body: str, body_start: int) -> list[dict]:
    lines = body.splitlines()
    result: list[dict] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith("```"):
            start = index
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                index += 1
            if index < len(lines):
                index += 1
            block = lines[start:index]
            result.append(
                {
                    "text": "\n".join(block),
                    "start": body_start + start,
                    "end": body_start + index - 1,
                }
            )
            continue
        if _BULLET_RE.match(line):
            start = index
            index += 1
            while index < len(lines) and lines[index].startswith((" ", "\t")) and lines[index].strip():
                index += 1
            block = lines[start:index]
            result.append(
                {
                    "text": "\n".join(block),
                    "start": body_start + start,
                    "end": body_start + index - 1,
                }
            )
            continue
        start = index
        index += 1
        while (
            index < len(lines)
            and lines[index].strip()
            and not lines[index].startswith("```")
            and not _BULLET_RE.match(lines[index])
            and parse_heading(lines[index]) is None
        ):
            index += 1
        block = lines[start:index]
        result.append(
            {
                "text": "\n".join(block),
                "start": body_start + start,
                "end": body_start + index - 1,
            }
        )
    return result


def _merge_short(leaves: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for leaf in leaves:
        if merged and len(merged[-1]["text"].strip()) < MIN_LEAF_CHARS:
            prev = merged[-1]
            prev["text"] = prev["text"] + "\n" + leaf["text"]
            prev["end"] = leaf["end"]
        else:
            merged.append(dict(leaf))
    return merged


def _split_long(leaves: list[dict]) -> list[dict]:
    out: list[dict] = []
    for leaf in leaves:
        text = leaf["text"]
        if len(text) <= MAX_LEAF_CHARS and estimate_tokens(text) <= 256:
            out.append(leaf)
            continue
        parts = [bit for bit in _SENTENCE_RE.split(text) if bit.strip()]
        if len(parts) <= 1:
            parts = text.splitlines() or [text]
        if len(parts) <= 1 and len(text) > MAX_LEAF_CHARS:
            # Single-line overflow: hard char-split, or one giant line
            # would sail through as a single unbounded leaf.
            parts = [
                text[index : index + MAX_LEAF_CHARS]
                for index in range(0, len(text), MAX_LEAF_CHARS)
            ]
        # Line starts within the leaf text, so each emitted part carries
        # its own span instead of the whole leaf's.
        starts = [0]
        for match in re.finditer("\n", text):
            starts.append(match.end())

        def line_of(offset: int) -> int:
            return leaf["start"] + bisect_right(starts, offset) - 1

        buf = ""
        buf_start = 0
        buf_end = 0
        cursor = 0
        for part in parts:
            at = text.find(part, cursor)
            if at < 0:
                at = cursor
            if not buf:
                buf, buf_start, buf_end = part, at, at + len(part)
            else:
                candidate = f"{buf} {part}".strip()
                if len(candidate) > MAX_LEAF_CHARS or estimate_tokens(candidate) > 256:
                    if buf.strip():
                        out.append(
                            {"text": buf, "start": line_of(buf_start), "end": line_of(buf_end - 1)}
                        )
                    buf, buf_start, buf_end = part, at, at + len(part)
                else:
                    buf, buf_end = candidate, at + len(part)
            cursor = at + len(part)
        if buf.strip():
            out.append(
                {"text": buf, "start": line_of(buf_start), "end": line_of(buf_end - 1)}
            )
    return out
