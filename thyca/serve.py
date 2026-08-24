"""Loopback HTTP for webui, memory stats, and chat."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import unquote, urlparse

from thyca.chat_app import ChatApp
from thyca.config import ConfigError
from thyca.llm.llm_base import LLMError
from thyca.sessions import SessionCorrupt, SessionError, SessionNotFound
from thyca.memory.archived import ArchiveError
from thyca.tools.memory import MemoryFacade

LOOPBACK = frozenset({"127.0.0.1", "localhost"})
_SESSION_RE = re.compile(
    r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})$"
)
_TURN_RE = re.compile(
    r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})/turn$"
)
_BODY_CAP = 16_384


class ServeError(RuntimeError):
    """Web UI server could not start or bind."""


def default_webui() -> Path:
    here = Path(__file__).resolve().parent
    packaged = here / "webui"
    if packaged.is_dir():
        return packaged
    return here.parent / "webui"


def make_server(
    *,
    host: str,
    port: int,
    webui: Path,
    facade: MemoryFacade,
    chat: ChatApp | None = None,
) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ServeError("bind must be loopback")
    root = webui.resolve()
    if not root.is_dir():
        raise ServeError(f"webui missing: {webui}")
    httpd = ThreadingHTTPServer((host, port), _handler(root, facade, chat))
    httpd.allow_reuse_address = True
    return httpd


def run(
    *,
    host: str,
    port: int,
    webui: Path,
    facade: MemoryFacade,
    stdout,
    chat: ChatApp | None = None,
) -> None:
    httpd = make_server(host=host, port=port, webui=webui, facade=facade, chat=chat)
    bound_host, bound_port = httpd.server_address[:2]
    print(f"http://{bound_host}:{bound_port}/", file=stdout, flush=True)
    try:
        httpd.serve_forever()
    finally:
        if chat is not None:
            chat.shutdown()
        httpd.server_close()


def _handler(
    webui: Path, facade: MemoryFacade, chat: ChatApp | None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/memory/stats":
                self._stats()
                return
            if path == "/api/sessions":
                self._chat_list()
                return
            match = _SESSION_RE.fullmatch(path)
            if match:
                self._chat_get(match.group(1))
                return
            if path.startswith("/api/sessions"):
                self._json(404, {"error": "session not found"})
                return
            self._static(path)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/memory/forget":
                self._forget()
                return
            if path == "/api/sessions":
                self._chat_create()
                return
            match = _TURN_RE.fullmatch(path)
            if match:
                self._chat_turn(match.group(1))
                return
            if path.startswith("/api/sessions"):
                self._json(404, {"error": "session not found"})
                return
            self._send(405, b"method not allowed", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _forget(self) -> None:
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            sid = payload.get("session_id")
            if not isinstance(sid, str) or not sid.strip():
                self._json(400, {"error": "invalid session_id"})
                return
            try:
                facade.forget(sid.strip())
            except ArchiveError:
                self._json(404, {"error": "session not found"})
                return
            except Exception:
                self._json(503, {"error": "forget failed"})
                return
            self._json(200, {"ok": True})

        def _stats(self) -> None:
            try:
                payload = asdict(facade.stats())
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception:
                body = json.dumps({"error": "memory stats unavailable"}).encode("utf-8")
                self._send(503, body, "application/json; charset=utf-8")
                return
            self._send(200, body, "application/json; charset=utf-8")

        def _chat_list(self) -> None:
            app = self._chat()
            if app is None:
                return
            try:
                self._json(200, app.list_payload())
            except Exception:
                self._json(503, {"error": "chat unavailable"})

        def _chat_get(self, session_id: str) -> None:
            app = self._chat()
            if app is None:
                return
            try:
                self._json(200, app.get_payload(session_id))
            except SessionNotFound:
                self._json(404, {"error": "session not found"})
            except SessionCorrupt:
                self._json(503, {"error": "session unreadable"})
            except SessionError:
                self._json(503, {"error": "session unavailable"})
            except Exception:
                self._json(503, {"error": "chat unavailable"})

        def _chat_create(self) -> None:
            app = self._chat()
            if app is None:
                return
            try:
                self._read_body()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            try:
                self._json(200, app.create())
            except SessionError:
                self._json(503, {"error": "session unavailable"})
            except Exception:
                self._json(503, {"error": "chat unavailable"})

        def _chat_turn(self, session_id: str) -> None:
            app = self._chat()
            if app is None:
                return
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            text = payload.get("text")
            if not isinstance(text, str):
                self._json(400, {"error": "invalid text"})
                return
            try:
                self._json(200, app.turn(session_id, text))
            except ValueError:
                self._json(400, {"error": "invalid text"})
            except SessionNotFound:
                self._json(404, {"error": "session not found"})
            except SessionCorrupt:
                self._json(503, {"error": "session unreadable"})
            except SessionError:
                self._json(503, {"error": "session unavailable"})
            except LLMError as exc:
                self._json(503, {"error": str(exc)})
            except ConfigError as exc:
                self._json(503, {"error": str(exc)})
            except Exception:
                self._json(503, {"error": "chat unavailable"})

        def _chat(self) -> ChatApp | None:
            if chat is None:
                self._json(404, {"error": "chat unavailable"})
                return None
            return chat

        def _read_json(self) -> dict:
            raw = self._read_body()
            if not raw.strip():
                raise ValueError("empty body")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid json") from exc
            if not isinstance(payload, dict):
                raise ValueError("invalid json")
            return payload

        def _read_body(self) -> bytes:
            raw_len = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_len)
            except ValueError as exc:
                raise ValueError("invalid length") from exc
            if length < 0 or length > _BODY_CAP:
                raise ValueError("invalid length")
            return self.rfile.read(length) if length else b""

        def _static(self, url_path: str) -> None:
            target = _safe_file(webui, url_path)
            if target is None:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            data = target.read_bytes()
            self._send(200, data, _content_type(target))

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
}


def _content_type(path: Path) -> str:
    known = _TYPES.get(path.suffix.lower())
    if known:
        return known
    mime, _ = guess_type(str(path))
    return mime or "application/octet-stream"


def _safe_file(webui: Path, url_path: str) -> Path | None:
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
