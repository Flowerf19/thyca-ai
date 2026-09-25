"""Display and naming policy for chat session titles."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from thyca.core.protocol import Message

from .errors import SessionError
from .models import Session

if TYPE_CHECKING:
    from .manager import SessionManager

ChatFn = Callable[[list[Message], list | None], Awaitable]

TITLE_MAX = 32
# Marker stored in the session's meta line for a title the user typed.
USER_TITLE_SOURCE = "user"
# A title the user typed in the sidebar is shown as written — only the noise of
# a multiline paste is cleaned, and the ceiling is a whole phrase, not a label.
USER_TITLE_MAX = 120
_SNIPPET = 400
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_NAMING_PROMPT = (
    "Đặt tiêu đề sổ tay 3–6 chữ tiếng Việt, tối đa 32 ký tự, cho cuộc trò chuyện này. "
    "Chỉ trả về tiêu đề tiếng Việt. Không chữ Hán, không ngoại ngữ, "
    "không ngoặc kép, không dấu câu cuối, không giải thích."
)


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
    if cleaned is None or _CJK_RE.search(cleaned):
        return None
    folded = cleaned.casefold()
    for role in ("user", "assistant"):
        text = _first_text(session.messages, role)
        if text is None:
            continue
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


def naming_messages(session: Session) -> list[Message] | None:
    user = _first_text(session.messages, "user")
    if user is None:
        return None
    snippet = f"User: {_clip(user, _SNIPPET)}"
    assistant = _first_text(session.messages, "assistant")
    if assistant is not None:
        snippet += f"\nThyca: {_clip(assistant, _SNIPPET)}"
    return [
        Message(role="system", content=_NAMING_PROMPT),
        Message(role="user", content=snippet),
    ]


def _first_text(messages: list[Message], role: str) -> str | None:
    for item in messages:
        if item.role == role and item.content and item.content.strip():
            return item.content.strip()
    return None


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


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
