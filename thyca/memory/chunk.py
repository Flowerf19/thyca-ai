"""Chunk markdown into archived leafs. No I/O."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_HEADING_RE = re.compile(
    r"^##\s+(\d{2}:\d{2})\s*[—\-]\s*(.+?)(?:\s*<!--\s*(.*?)\s*-->)?\s*$"
)
_THYCA_ID = re.compile(r"(?:^|\s)thyca:([0-9a-f]{8})(?:\s|$)")
_ATTR = re.compile(r"\b(imp|exp|forgotten)=(\S+)")
_BULLET_RE = re.compile(r"^(\s*)([-*]|\d+\.)\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?。])\s+")

MAX_LEAF_CHARS = 800
MIN_LEAF_CHARS = 20
PROFILE_PENDING = "pending"


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
    embed_text: str
    content_hash: str
    embedding_hash: str
    profile_id: str = PROFILE_PENDING
    expires_at: str | None = None
    forgotten_at: str | None = None


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
                raw = leaf["text"]
                if not raw.strip():
                    continue
                norm = self.normalize(raw)
                title = session["title"]
                embed = self.normalize(title) + "\n" + norm
                chunk_id = f"{session['session_id']}#{ord_}"
                payload = f"{session['heading']}\n{raw}".encode("utf-8")
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        path=path_s,
                        source_kind=source_kind,
                        timeline_day=timeline_day,
                        session_id=session["session_id"],
                        session_title=title,
                        heading_raw=session["heading"],
                        leaf_ord=ord_,
                        line_start=leaf["start"],
                        line_end=leaf["end"],
                        text_raw=raw,
                        text_norm=norm,
                        embed_text=embed,
                        content_hash=hashlib.sha256(payload).hexdigest(),
                        embedding_hash=hashlib.sha256(
                            PROFILE_PENDING.encode("utf-8") + b"\0" + embed.encode("utf-8")
                        ).hexdigest(),
                        expires_at=session.get("expires_at"),
                        forgotten_at=session.get("forgotten_at"),
                    )
                )
        return chunks

    @staticmethod
    def normalize(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text)
        stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
        return stripped.lower()


def _sessions(
    text: str, source_kind: str, timeline_day: str | None, path: str
) -> list[dict]:
    lines = text.splitlines()
    found: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match:
            found.append((index, match))
    if not found:
        name = Path(path).stem.lower()
        session_id = f"canonical#{name}" if source_kind == "canonical" else f"{timeline_day}#legacy"
        return [
            {
                "heading": "",
                "title": "",
                "session_id": session_id,
                "body": text,
                "body_start": 1,
            }
        ]
    sessions: list[dict] = []
    title_seen: dict[str, int] = {}
    for pos, (line_no, match) in enumerate(found):
        title = match.group(2).strip()
        title_seen[title] = title_seen.get(title, 0) + 1
        comment = match.group(3) or ""
        id_match = _THYCA_ID.search(comment)
        attrs = dict(_ATTR.findall(comment))
        entry = id_match.group(1) if id_match else _legacy_entry(path, title, title_seen[title])
        prefix = timeline_day if source_kind == "daily" else Path(path).stem.lower()
        session_id = f"{prefix}#{entry}"
        end = found[pos + 1][0] if pos + 1 < len(found) else len(lines)
        body = "\n".join(lines[line_no + 1 : end])
        sessions.append(
            {
                "heading": lines[line_no],
                "title": title,
                "session_id": session_id,
                "body": body,
                "body_start": line_no + 2,
                "expires_at": attrs.get("exp"),
                "forgotten_at": attrs.get("forgotten"),
            }
        )
    return sessions


def _legacy_entry(path: str, title: str, occurrence: int) -> str:
    payload = f"{path}\0{title}\0{occurrence}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8]


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
            and not _HEADING_RE.match(lines[index])
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
        if len(text) <= MAX_LEAF_CHARS and (len(text) + 3) // 4 <= 256:
            out.append(leaf)
            continue
        parts = [bit for bit in _SENTENCE_RE.split(text) if bit.strip()]
        if len(parts) <= 1:
            parts = text.splitlines() or [text]
        buf = ""
        start = leaf["start"]
        for part in parts:
            candidate = part if not buf else f"{buf} {part}".strip()
            if buf and (len(candidate) > MAX_LEAF_CHARS or (len(candidate) + 3) // 4 > 256):
                out.append({"text": buf, "start": start, "end": leaf["end"]})
                buf = part
            else:
                buf = candidate
        if buf:
            out.append({"text": buf, "start": start, "end": leaf["end"]})
    return out
