"""Loopback HTTP for webui, memory stats, and chat."""
from __future__ import annotations

import json
import queue
import re
import signal
import sys
import threading
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import unquote, urlparse

from thyca.agent.events import TurnEvent
from thyca.chat_app import ChatApp
from thyca.trace_api import (
    trace_detail_payload,
    trace_list_payload,
    trace_stats_payload,
)
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
_TURN_STREAM_RE = re.compile(
    r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})/turn/stream$"
)
_TRACE_RE = re.compile(r"^/api/traces$")
_TRACE_STATS_RE = re.compile(r"^/api/traces/stats$")
_TRACE_DETAIL_RE = re.compile(
    r"^/api/traces/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})/(\d+)$"
)
_BODY_CAP = 16_384
_SENTINEL = object()

def public_turn_error(exc: Exception) -> tuple[int, str, str]:
    """Map a turn exception to a public ``(status, code, message)``.

    Shared by ``/turn`` and ``/turn/stream`` so the two cannot drift. Never
    leaks stack/path/secret: unexpected errors and :class:`ConfigError` always
    map to the constant ``chat unavailable``; :class:`LLMError` keeps its
    provider-redacted/capped text.
    """
    if isinstance(exc, ValueError):
        return 400, "invalid_text", "invalid text"
    if isinstance(exc, SessionNotFound):
        return 404, "session_not_found", "session not found"
    if isinstance(exc, SessionCorrupt):
        return 503, "session_unreadable", "session unreadable"
    if isinstance(exc, SessionError):
        return 503, "session_unavailable", "session unavailable"
    if isinstance(exc, LLMError):
        return 503, "llm_error", str(exc)
    return 503, "chat_unavailable", "chat unavailable"


def _bridge_sink(queue_: queue.Queue, state: dict) -> None:
    """Queue adapter for ``ChatApp.turn(event_sink=...)``.

    Never raises and never blocks AgentLoop: once the client disconnected
    the sink drops events without touching the queue.
    """
    def sink(event: TurnEvent) -> None:
        if state["disconnected"]:
            return
        try:
            queue_.put(event)
        except Exception:
            pass
    return sink


def _bridge_worker(
    app: ChatApp,
    session_id: str,
    text: str,
    queue_: queue.Queue,
    state: dict,
) -> None:
    """Run one turn, queueing events plus exactly one terminal item.

    The sentinel lands in ``finally`` so the handler cannot hang even if
    completion enqueue/serialization fails. The first queued item before
    ``turn.accepted`` (or the sentinel) is a pre-accept error.
    """
    sink = _bridge_sink(queue_, state)
    try:
        try:
            detail = app.turn(session_id, text, event_sink=sink)
        except Exception as exc:
            queue_.put(("failed", exc))
        else:
            queue_.put(("completed", detail))
    finally:
        queue_.put(_SENTINEL)


def _write_line(wfile, line: dict) -> None:
    wfile.write(json.dumps(line, ensure_ascii=False).encode("utf-8") + b"\n")
    wfile.flush()


class ServeError(RuntimeError):
    """Web UI server could not start or bind."""


def _raise_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


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
    signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(file=stdout)
    finally:
        if chat is not None:
            chat.shutdown()
        httpd.server_close()


def _handler(
    webui: Path, facade: MemoryFacade, chat: ChatApp | None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/memory/stats":
                self._stats()
                return
            if path == "/api/sessions":
                self._chat_list()
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
                self._chat_get(match.group(1))
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
                self._forget()
                return
            if path == "/api/memory/reinforce":
                self._reinforce()
                return
            if path == "/api/memory/update":
                self._update_mem()
                return
            if path == "/api/memory/canonical":
                self._write_canonical()
                return
            if path == "/api/sessions":
                self._chat_create()
                return
            match = _TURN_RE.fullmatch(path)
            if match:
                self._chat_turn(match.group(1))
                return
            match = _TURN_STREAM_RE.fullmatch(path)
            if match:
                self._chat_turn_stream(match.group(1))
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

        def _reinforce(self) -> None:
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            sid = payload.get("session_id")
            if not isinstance(sid, str) or not sid.strip():
                self._json(400, {"error": "invalid session_id"})
                return
            importance = payload.get("importance")
            try:
                expires = facade.reinforce(
                    sid.strip(),
                    importance=int(importance) if importance is not None else None,
                )
            except ArchiveError:
                self._json(404, {"error": "session not found"})
                return
            except Exception:
                self._json(503, {"error": "reinforce failed"})
                return
            self._json(200, {"ok": True, "expires_at": expires})

        def _update_mem(self) -> None:
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            sid = payload.get("session_id")
            if not isinstance(sid, str) or not sid.strip():
                self._json(400, {"error": "invalid session_id"})
                return
            topic = payload.get("topic")
            summary = payload.get("summary")
            content = payload.get("content")
            if not isinstance(topic, str) and not isinstance(summary, str):
                self._json(400, {"error": "nothing to update"})
                return
            try:
                facade.update(
                    sid.strip(),
                    topic=topic.strip() if isinstance(topic, str) and topic.strip() else None,
                    summary=summary.strip() if isinstance(summary, str) else None,
                    content=content if isinstance(content, str) else None,
                )
            except ArchiveError:
                self._json(404, {"error": "session not found"})
                return
            except Exception:
                self._json(503, {"error": "update failed"})
                return
            self._json(200, {"ok": True})

        def _write_canonical(self) -> None:
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            name = payload.get("name")
            content = payload.get("content")
            if not isinstance(name, str) or not isinstance(content, str):
                self._json(400, {"error": "invalid body"})
                return
            try:
                facade.write_canonical(name, content)
            except ArchiveError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception:
                self._json(503, {"error": "write failed"})
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

        def _chat_list(self) -> None:
            app = self._chat()
            if app is None:
                return
            try:
                self._json(200, app.list_payload())
            except Exception:
                traceback.print_exc(file=sys.stderr)
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
                traceback.print_exc(file=sys.stderr)
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
            except Exception as exc:
                status, _, message = public_turn_error(exc)
                self._json(status, {"error": message})

        def _chat_turn_stream(self, session_id: str) -> None:
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
            items: queue.Queue = queue.Queue()
            state = {"disconnected": False}
            worker = threading.Thread(
                target=_bridge_worker,
                args=(app, session_id, text, items, state),
                daemon=True,
                name="thyca-turn-stream",
            )
            worker.start()
            try:
                first = items.get()
                if isinstance(first, TurnEvent):
                    if first.type == "turn.accepted":
                        self._stream_headers()
                        _write_line(self.wfile, first.to_dict())
                    else:
                        self._json(503, {"error": "chat unavailable"})
                        return
                elif first is _SENTINEL:
                    self._json(503, {"error": "chat unavailable"})
                    return
                else:
                    # Pre-accept exception: same HTTP error as /turn, no NDJSON.
                    _type, exc = first
                    status, _code, message = public_turn_error(exc)
                    self._json(status, {"error": message})
                    return
                terminal = False
                while True:
                    item = items.get()
                    if item is _SENTINEL:
                        break
                    if terminal:
                        continue
                    if isinstance(item, TurnEvent):
                        _write_line(self.wfile, item.to_dict())
                        continue
                    _kind, value = item
                    if _kind == "completed":
                        _write_line(
                            self.wfile, {"type": "turn.completed", "detail": value}
                        )
                    else:
                        _code, _message = public_turn_error(value)[1:]
                        _write_line(
                            self.wfile, {"type": "turn.failed", "code": _code, "message": _message}
                        )
                    terminal = True
                if not terminal and not state["disconnected"]:
                    # Sentinel with no terminal item: write the constant public
                    # failure so the client never sees a stream without a terminal.
                    _write_line(
                        self.wfile,
                        {
                            "type": "turn.failed",
                            "code": "chat_unavailable",
                            "message": "chat unavailable",
                        },
                    )
                    terminal = True
            except (BrokenPipeError, ConnectionResetError):
                # Client left: drop further events, let persist finish.
                state["disconnected"] = True
            finally:
                worker.join(timeout=60)

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
