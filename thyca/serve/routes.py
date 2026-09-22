"""HTTP routing for the loopback webui server (split from server.py).

Only dispatch (regex to endpoint) plus the handler's JSON/static plumbing
lives here. Config/onboarding/provider endpoints live in ``config_api``,
static file helpers in ``static``, session/turn endpoints in
``sessions_api`` — all take the handler duck-typed, so this module never
grows endpoint logic back.
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from thyca.serve import config_api
from thyca.serve.sessions_api import (
    session_create,
    session_delete,
    session_get,
    session_list,
    session_rename,
    session_turn,
    session_turn_cancel,
    session_turn_follow,
    session_turn_stream,
)
from thyca.serve.memory import memory_endpoint
from thyca.serve.static import content_type, safe_file
from thyca.serve.trace_api import (
    trace_detail_payload,
    trace_list_payload,
    trace_stats_payload,
)
from thyca.sessions import SessionCorrupt, SessionNotFound
from thyca.tools.memory import MemoryFacade

if TYPE_CHECKING:
    from thyca.app.chat_app import ChatApp

_SESSION_RE = re.compile(
    r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})$"
)
_TURN_RE = re.compile(
    r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})/turn$"
)
_TURN_STREAM_RE = re.compile(
    r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})/turn/stream$"
)
_TURN_CANCEL_RE = re.compile(
    r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})/turn/cancel$"
)
_TRACE_RE = re.compile(r"^/api/traces$")
_TRACE_STATS_RE = re.compile(r"^/api/traces/stats$")
_TRACE_DETAIL_RE = re.compile(
    r"^/api/traces/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})/(\d+)$"
)
_BODY_CAP = 16_384


def _handler(
    webui: Path,
    facade: MemoryFacade,
    chat: ChatApp | None,
    config_file: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/memory/stats":
                self._stats()
                return
            if path == "/api/config/status":
                config_api.config_status(self, config_file)
                return
            if path == "/api/config":
                config_api.config_get(self, config_file)
                return
            if path == "/api/sessions":
                session_list(self, chat)
                return
            if _TRACE_STATS_RE.fullmatch(path):
                self._trace_stats(parsed.query)
                return
            if _TRACE_RE.fullmatch(path):
                self._trace_list(parsed.query)
                return
            match = _TRACE_DETAIL_RE.fullmatch(path)
            if match:
                self._trace_detail(match.group(1), match.group(2))
                return
            match = _SESSION_RE.fullmatch(path)
            if match:
                session_get(self, chat, match.group(1))
                return
            match = _TURN_STREAM_RE.fullmatch(path)
            if match:
                session_turn_follow(self, chat, match.group(1))
                return
            if path.startswith("/api/sessions"):
                self._json(404, {"error": "session not found"})
                return
            if path.startswith("/api/traces"):
                self._json(404, {"error": "trace not found"})
                return
            self._static(path)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/memory/forget":
                self._memory_post("forget")
                return
            if path == "/api/config":
                config_api.config_post(self, config_file)
                return
            if path == "/api/onboarding/verify":
                config_api.onboarding_verify(self, config_file)
                return
            if path == "/api/providers/test":
                config_api.providers_test(self, config_file)
                return
            if path == "/api/memory/reinforce":
                self._memory_post("reinforce")
                return
            if path == "/api/memory/update":
                self._memory_post("update")
                return
            if path == "/api/memory/canonical":
                self._memory_post("canonical")
                return
            if path == "/api/sessions":
                session_create(self, chat)
                return
            match = _TURN_RE.fullmatch(path)
            if match:
                session_turn(self, chat, match.group(1))
                return
            match = _TURN_STREAM_RE.fullmatch(path)
            if match:
                session_turn_stream(self, chat, match.group(1))
                return
            match = _TURN_CANCEL_RE.fullmatch(path)
            if match:
                session_turn_cancel(self, chat, match.group(1))
                return
            if path.startswith("/api/sessions"):
                self._json(404, {"error": "session not found"})
                return
            self._send(405, b"method not allowed", "text/plain; charset=utf-8")

        def do_DELETE(self) -> None:
            app = self._chat()
            if app is None:
                return
            session_delete(self, app, urlparse(self.path).path)

        def do_PATCH(self) -> None:
            app = self._chat()
            if app is None:
                return
            session_rename(self, app, urlparse(self.path).path)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _memory_post(self, kind: str) -> None:
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            status, body = memory_endpoint(facade, kind, payload)
            self._json(status, body)

        def _stats(self) -> None:
            try:
                payload = asdict(facade.stats())
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception:
                body = json.dumps({"error": "memory stats unavailable"}).encode("utf-8")
                self._send(503, body, "application/json; charset=utf-8")
                return
            self._send(200, body, "application/json; charset=utf-8")

        def _trace_stats(self, query: str) -> None:
            app = self._chat()
            if app is None:
                return
            try:
                self._json(200, trace_stats_payload(app, query))
            except Exception:
                traceback.print_exc(file=sys.stderr)
                self._json(503, {"error": "trace unavailable"})

        def _trace_list(self, query: str) -> None:
            app = self._chat()
            if app is None:
                return
            try:
                self._json(200, trace_list_payload(app, query))
            except Exception:
                traceback.print_exc(file=sys.stderr)
                self._json(503, {"error": "trace unavailable"})

        def _trace_detail(self, session_id: str, turn_index: str) -> None:
            app = self._chat()
            if app is None:
                return
            try:
                idx = int(turn_index)
            except ValueError:
                self._json(404, {"error": "trace not found"})
                return
            try:
                self._json(200, trace_detail_payload(app, session_id, idx))
            except SessionNotFound:
                self._json(404, {"error": "session not found"})
            except SessionCorrupt:
                self._json(503, {"error": "session unreadable"})
            except ValueError:
                self._json(404, {"error": "trace not found"})
            except Exception:
                traceback.print_exc(file=sys.stderr)
                self._json(503, {"error": "trace unavailable"})

        def _stream_headers(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-transform")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

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
            target = safe_file(webui, url_path)
            if target is None:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            data = target.read_bytes()
            self._send(200, data, content_type(target))

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Client ngắt giữa response (reload/đóng tab): hết người nghe.
                return

    return Handler
