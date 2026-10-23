"""Static webui file serving (split from server.py)."""
from __future__ import annotations

from mimetypes import guess_type
from pathlib import Path
from urllib.parse import unquote

_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
}


def content_type(path: Path) -> str:
    known = _TYPES.get(path.suffix.lower())
    if known:
        return known
    mime, _ = guess_type(str(path))
    return mime or "application/octet-stream"


_SPA_ENTRY = "ui/chat/index.html"
_SPA_PATHS = {
    "/", "/index.html",
    *(f"/ui/{page}{suffix}" for page in ("chat", "memories", "profile", "provider", "dashboard", "trace", "settings")
      for suffix in ("", "/", "/index.html")),
}


def safe_file(webui: Path, url_path: str) -> Path | None:
    decoded_path = unquote(url_path)
    if "\x00" in decoded_path or ".." in Path(decoded_path).parts:
        # Reject NUL and traversal explicitly, including percent-encoded forms.
        return None
    if decoded_path in _SPA_PATHS:
        rel = _SPA_ENTRY
    else:
        rel = unquote(url_path).lstrip("/")
    if not rel or rel.endswith("/"):
        rel = (rel + "index.html") if rel else "index.html"
    target = (webui / rel).resolve()
    try:
        target.relative_to(webui)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target
