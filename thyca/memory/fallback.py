"""Unindexed daily chunk reads (today's file is never in the FTS index).

A chunk miss for today's date resolves through the writer like a session
miss. The shared read policy applies: symlinks/non-files read as absent,
undecodable files keep their typed error, and expired chunks are rejected
without usage or renewal.
"""
from __future__ import annotations

from datetime import datetime

from thyca.memory.archive_store import ArchiveError
from thyca.memory.heading import is_visible, read_text_file


def read_unindexed_chunk(
    archive, writer, chunk_id: str, now: datetime | None, original: ArchiveError
) -> tuple[str, str]:
    """Leaf text + session id for a chunk missing from the index.

    T8: chunk_id is "{session_id}#{ord}" (chunk.py), so a daily miss
    resolves through the writer like a session miss — today's file is
    not indexed. Canonical chunks are always indexed, so a miss there
    (or an unparseable id) re-raises the index miss unchanged.
    """
    sid, sep, ord_part = chunk_id.rpartition("#")
    if not sep or not sid or not ord_part.isdigit():
        raise original
    if sid.startswith("canonical#"):
        raise original
    try:
        path, _ = writer.locate(sid)
    except ArchiveError:
        raise original from None
    try:
        whole = read_text_file(path)
    except UnicodeDecodeError as exc:
        raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
    except FileNotFoundError:
        raise original from None
    if whole is None:
        # Missing, not-a-file, or symlink (shared read policy): the
        # chunk is not there, and nothing is recorded or renewed.
        raise original from None
    day = sid.split("#", 1)[0]
    # Whole file, not the single block: legacy comment-less headings
    # resolve occurrence counts file-wide, so a lone block would mistag
    # duplicate titles as occurrence 1 and miss their real chunk ids.
    chunks = archive.chunker.chunk_markdown(
        path, whole, source_kind="daily", timeline_day=day
    )
    for chunk in chunks:
        if chunk.chunk_id == chunk_id:
            if not is_visible(chunk.expires_at, now):
                raise ArchiveError(f"chunk expired: {chunk_id}")
            return chunk.text_raw, sid
    raise original from None
