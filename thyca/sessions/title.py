"""Display and naming policy for chat session titles."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from thyca.core.protocol import Message

from .errors import SessionError
from .models import Session

if TYPE_CHECKING:
    from .manager import SessionManager
    from .store import SessionStore

ChatFn = Callable[[list[Message], list | None], Awaitable]

TITLE_MAX = 32
# Marker stored in the session's meta line for a title the user typed.
USER_TITLE_SOURCE = "user"
# A title the user typed in the sidebar is shown as written — only the noise of
# a multiline paste is cleaned, and the ceiling is a whole phrase, not a label.
USER_TITLE_MAX = 120
# Automatic naming fires once, after this many successfully completed turns.
NAMING_TURNS = 2
_SNIPPET = 400
_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "seeds" / "prompts"
# Cached naming instruction. Only successful reads land here: a transient
# failure must be retried, not frozen in for the process lifetime.
_INSTRUCTION_CACHE: str | None = None


def naming_instruction() -> str | None:
    """Standalone naming system message (never part of the chat prompt).

    None when the packaged prompt is missing, blank, or unreadable: the
    caller must skip the model call. There is no inline fallback — the
    packaged ``naming.md`` is the only instruction source.
    """
    global _INSTRUCTION_CACHE
    if _INSTRUCTION_CACHE is not None:
        return _INSTRUCTION_CACHE
    try:
        text = (_PROMPTS_DIR / "naming.md").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    _INSTRUCTION_CACHE = text
    return text


def sanitize_title(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    text = text.splitlines()[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”‘’":
        text = text[1:-1].strip()
    if text.startswith("**") and text.endswith("**") and len(text) > 4:
        text = text[2:-2].strip()
    text = " ".join(text.split())
    text = text.rstrip(".!?…。")
    if not text:
        return None
    if len(text) > TITLE_MAX:
        return text[: TITLE_MAX - 1] + "…"
    return text


def sanitize_user_title(raw: str) -> str | None:
    text = " ".join(str(raw).split())
    if not text:
        return None
    if len(text) > USER_TITLE_MAX:
        return text[: USER_TITLE_MAX - 1] + "…"
    return text


def fallback_title(session_id: str) -> str:
    try:
        date, rest = session_id.split("T", 1)
        _year, month, day = date.split("-")
        hour = int(rest.split("-")[0])
    except (ValueError, IndexError):
        return "Phiên gần đây"
    if hour < 12:
        period = "Sáng"
    elif hour < 18:
        period = "Chiều"
    else:
        period = "Tối"
    return f"{period} {int(day)} thg {int(month)}"


def accept_title(raw: str, session: Session) -> str | None:
    cleaned = sanitize_title(raw)
    if cleaned is None:
        return None
    folded = cleaned.casefold()
    for role in ("user", "assistant"):
        for text in _first_texts(session.messages, role):
            echoed = sanitize_title(text.splitlines()[0])
            if echoed and echoed.casefold() == folded:
                return None
    return cleaned


def is_blank(session: Session) -> bool:
    return not any(
        item.role == "user" and item.content and item.content.strip()
        for item in session.messages
    )


def display_title(session: Session) -> str:
    if session.title:
        # A user-written title is the authority on its own notebook; the
        # naming policy only vets what the model proposes.
        if session.title_source == USER_TITLE_SOURCE:
            return session.title
        accepted = accept_title(session.title, session)
        if accepted:
            return accepted
    if not is_blank(session):
        return fallback_title(session.id)
    return "Phiên trống"


def completed_turn_count(messages: list[Message]) -> int:
    """Successfully completed turns: the automatic naming threshold input."""
    return len(_completed_turns(messages))


def naming_messages(session: Session) -> list[Message] | None:
    instruction = naming_instruction()
    if instruction is None:
        return None
    snippet = _turns_snippet(_completed_turns(session.messages)[:NAMING_TURNS])
    if snippet is None:
        # No completed turn yet: an explicit retitle still gets the first
        # user text as context. The automatic path never reaches this — it
        # is gated on NAMING_TURNS completed turns before attempting.
        user = _first_text(session.messages, "user")
        if user is None:
            return None
        snippet = f"User: {_clip(user, _SNIPPET)}"
    return [
        Message(role="system", content=instruction),
        Message(role="user", content=snippet),
    ]


def _is_naming_row(item: Message) -> bool:
    # Local copy of the sessions/wire predicate (which imports this module):
    # naming rows are transcript-only records, never turn outcomes.
    return (item.meta or {}).get("kind") == "naming"


def _completed_turns(messages: list[Message]) -> list[list[Message]]:
    """One slice per user message, keeping only completed turns."""
    slices: list[list[Message]] = []
    cur: list[Message] | None = None
    for item in messages:
        if item.role == "system" or _is_naming_row(item):
            continue
        if item.role == "user":
            if cur is not None:
                slices.append(cur)
            cur = [item]
        elif cur is not None:
            cur.append(item)
    if cur is not None:
        slices.append(cur)
    return [slice_msgs for slice_msgs in slices if _slice_completed(slice_msgs)]


def _slice_completed(slice_msgs: list[Message]) -> bool:
    # Stricter than trace._turn_status (kept local: trace imports wire, which
    # imports this module). Trace counts a turn with a terminal assistant
    # message; naming needs a real reply to title from, so the terminal
    # message itself must be an assistant's tool-free answer with text: a
    # trailing tool message, a terminal assistant still holding tool_calls
    # (results pending), or an empty reply means the turn never landed one.
    # Unmatched call ids cannot reach here: scan rejects them on load and
    # observe orders results exactly against calls before persisting.
    if any(isinstance((item.meta or {}).get("error"), dict) for item in slice_msgs):
        return False
    # The context guard's synthetic stop never landed a reply: a turn
    # ending (or interrupted by) one is not a completed turn, even when a
    # real reply sits earlier in the same slice.
    if any((item.meta or {}).get("status") == "context_limit" for item in slice_msgs):
        return False
    last = slice_msgs[-1]
    if last.role != "assistant" or last.tool_calls:
        return False
    if not (last.content and last.content.strip()):
        return False
    meta = last.meta or {}
    if meta.get("status") == "loop_limit" or meta.get("finish_reason") == "error":
        return False
    if last.content.strip() == "loop limit reached":
        return False
    return True


def _turns_snippet(turns: list[list[Message]]) -> str | None:
    parts: list[str] = []
    for turn in turns:
        user = _slice_text(turn, "user")
        if user is not None:
            parts.append(f"User: {_clip(user, _SNIPPET)}")
        assistant = _slice_text(turn, "assistant")
        if assistant is not None:
            parts.append(f"Thyca: {_clip(assistant, _SNIPPET)}")
    if not parts:
        return None
    return "\n".join(parts)


def _slice_text(turn: list[Message], role: str) -> str | None:
    # The naming context wants the final answer, not intermediate tool-call
    # commentary ("let me check that...") from earlier in the turn.
    items = reversed(turn) if role == "assistant" else iter(turn)
    for item in items:
        if item.role == role and item.content and item.content.strip():
            return item.content.strip()
    return None


def _first_text(messages: list[Message], role: str) -> str | None:
    found = _first_texts(messages, role, limit=1)
    return found[0] if found else None


def _first_texts(messages: list[Message], role: str, limit: int = 2) -> list[str]:
    found: list[str] = []
    for item in messages:
        if item.role == role and item.content and item.content.strip():
            found.append(item.content.strip())
            if len(found) >= limit:
                break
    return found


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def refresh_title(store: SessionStore, session: Session | None) -> None:
    """Re-read the title a stored meta line carries into ``session``.

    A turn holds its own ``Session`` snapshot from load time; a title the
    user typed in the meantime is on disk only. Without this the agent's
    naming step would append its own meta line over the user's name.
    The caller holds the manager lock.
    """
    if session is None:
        return
    found = store.read_title(session.path)
    if found is None:
        return
    title, title_source = found
    if title:
        session.title = title
        session.title_source = title_source


def mark_naming_attempted(store: SessionStore, session: Session) -> None:
    """Persist the automatic naming step's one attempt (success or not).

    Only the automatic path calls this: explicit operations (sidebar
    rename, batch retitle) never consume or check the flag.
    The caller holds the manager lock.
    """
    if session.naming_attempted:
        return
    store.append_naming_attempted(session.path)
    session.naming_attempted = True


def set_title_if_missing(
    store: SessionStore, session: Session, title: str
) -> str | None:
    """Store the automatic title only when no title is on disk.

    Atomic against a concurrent sidebar rename (same process): when the
    store reports a title already present, nothing is written and the
    in-memory title is synced from disk so the turn answers with it.
    The caller holds the manager lock.
    """
    cleaned = sanitize_title(title)
    if cleaned is None:
        return None
    if store.append_title_if_missing(session.path, cleaned, None):
        session.title = cleaned
        session.title_source = None
        return cleaned
    found = store.read_title(session.path)
    if found is not None and found[0]:
        session.title, session.title_source = found
    return None


def set_title(
    store: SessionStore, session: Session, title: str, *, source: str | None = None
) -> str | None:
    """Append a title meta line; the caller holds the manager lock."""
    cleaned = (
        sanitize_user_title(title)
        if source == USER_TITLE_SOURCE
        else sanitize_title(title)
    )
    if cleaned is None:
        return None
    store.append_meta(session.path, cleaned, source)
    session.title = cleaned
    session.title_source = source
    return cleaned


def rename_session(
    store: SessionStore,
    session_id: str,
    title: str,
    *,
    current: Session | None = None,
) -> str:
    """Set the title of any stored session, syncing ``current`` on match.

    The caller holds the manager lock.
    """
    cleaned = sanitize_user_title(title)
    if cleaned is None:
        raise ValueError("empty title")
    session = store.load(session_id)
    store.append_meta(session.path, cleaned, USER_TITLE_SOURCE)
    session.title = cleaned
    session.title_source = USER_TITLE_SOURCE
    if current is not None and current.id == session_id:
        current.title = cleaned
        current.title_source = USER_TITLE_SOURCE
    return cleaned


async def propose_title(chat: ChatFn, session: Session) -> str | None:
    prompt = naming_messages(session)
    if prompt is None:
        return None
    named = await chat(prompt, None)
    return accept_title(getattr(named, "content", None) or "", session)


async def retitle_missing(
    chat: ChatFn, manager: SessionManager
) -> list[tuple[Session, str, str]]:
    named: list[tuple[Session, str, str]] = []
    # Same package: save the live current object so the batch cannot hijack
    # it, and restore it without disk I/O (which could itself go stale).
    with manager._lock:
        saved_current = manager._session
    try:
        for session in manager.list_sessions():
            if is_blank(session) or naming_messages(session) is None:
                continue
            if session.title_source == USER_TITLE_SOURCE and session.title:
                continue
            if session.title and accept_title(session.title, session):
                continue
            old = display_title(session)
            try:
                title = await propose_title(chat, session)
            except Exception:
                continue  # one bad proposal must not abort the batch
            if title is None:
                continue
            try:
                manager.load(session.id)
            except SessionError:
                continue  # deleted mid-batch: skip, do not abort
            try:
                stored = manager.set_title(title)
            except SessionError:
                continue
            if stored is None:
                continue
            session.title = stored
            named.append((session, old, stored))
    finally:
        with manager._lock:
            manager._session = saved_current
    return named
