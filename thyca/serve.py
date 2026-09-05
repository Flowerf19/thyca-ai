"""Loopback HTTP for webui, memory stats, and chat."""
from __future__ import annotations

import json
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

from thyca.bridge import public_turn_error, stream_turn
from thyca.chat_app import ChatApp
from thyca.config import ConfigError, load, save
from thyca.config_schema import config_schema
from thyca.onboarding import (
    ProviderProbeError,
    provider_ready,
    validate_provider,
)
from thyca.serve_memory import memory_endpoint
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


def _config_values(cfg) -> dict:
    """Config as UI values; the API key never leaves the server."""
    values = cfg.to_dict()
    values["provider"]["apiKey"] = ""
    return values


def _config_meta(cfg) -> dict:
    """Non-secret status the settings panel can show (validity via verify)."""
    return {"hasApiKey": bool(cfg.provider.apiKey)}


def _merge_saved_key(raw: dict, cfg) -> dict:
    """Empty provider.apiKey in the payload means keep the stored key."""
    provider = raw.get("provider")
    if isinstance(provider, dict) and provider.get("apiKey") == "":
        provider = dict(provider)
        provider["apiKey"] = cfg.provider.apiKey
        raw = dict(raw)
        raw["provider"] = provider
    return raw


def _parse_config_payload(payload: dict, cfg):
    from thyca.config import _parse_dict

    return _parse_dict(_merge_saved_key(payload, cfg))


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


class _QuietHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer im lặng khi client đi giữa response.

    _send đã nuốt pipe ở đường write, nhưng wfile.flush() cuối
    handle_one_request (và body POST bị abort) vẫn dâng BrokenPipe /
    ConnectionReset lên BaseServer.handle_error — mặc định in traceback.
    Đổi Trace↔Chat mid-turn abort các GET in-flight nên chỉ nuốt 2 loại
    này; lỗi khác vẫn log như cũ.
    """

    def handle_error(self, request, client_address) -> None:
        _, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def make_server(
    *,
    host: str,
    port: int,
    webui: Path,
    facade: MemoryFacade,
    chat: ChatApp | None = None,
    config_file: Path | None = None,
) -> _QuietHTTPServer:
    if host not in LOOPBACK:
        raise ServeError("bind must be loopback")
    root = webui.resolve()
    if not root.is_dir():
        raise ServeError(f"webui missing: {webui}")
    httpd = _QuietHTTPServer(
        (host, port), _handler(root, facade, chat, config_file)
    )
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
    config_file: Path | None = None,
) -> None:
    httpd = make_server(
        host=host,
        port=port,
        webui=webui,
        facade=facade,
        chat=chat,
        config_file=config_file,
    )
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
                self._config_status()
                return
            if path == "/api/config":
                self._config_get()
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
                self._memory_post("forget")
                return
            if path == "/api/config":
                self._config_post()
                return
            if path == "/api/onboarding/verify":
                self._onboarding_verify()
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

        def _memory_post(self, kind: str) -> None:
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            status, body = memory_endpoint(facade, kind, payload)
            self._json(status, body)

        def _config(self):
            try:
                return load(config_file) if config_file else load()
            except ConfigError:
                return None

        def _config_status(self) -> None:
            cfg = self._config()
            ready = cfg is not None and provider_ready(cfg)
            self._json(200, {"ready": ready})

        def _config_get(self) -> None:
            cfg = self._config()
            if cfg is None:
                self._json(503, {"error": "config unavailable"})
                return
            self._json(
                200,
                {
                    "schema": config_schema(),
                    "values": _config_values(cfg),
                    "meta": _config_meta(cfg),
                },
            )

        def _config_post(self) -> None:
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            cfg = self._config()
            if cfg is None:
                self._json(503, {"error": "config unavailable"})
                return
            try:
                updated = _parse_config_payload(payload, cfg)
                save(updated, config_file) if config_file else save(updated)
            except ConfigError as exc:
                self._json(422, {"error": str(exc)})
                return
            self._json(200, {"ok": True, "ready": provider_ready(updated)})

        def _onboarding_verify(self) -> None:
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "invalid body"})
                return
            base_url = payload.get("baseUrl")
            if not isinstance(base_url, str) or not base_url.strip():
                self._json(400, {"error": "invalid baseUrl"})
                return
            api_key = payload.get("apiKey")
            if not isinstance(api_key, str) or not api_key.strip():
                cfg = self._config()
                if cfg is None:
                    self._json(503, {"error": "config unavailable"})
                    return
                try:
                    api_key = cfg.provider.api_key()
                except ConfigError:
                    self._json(422, {"error": "chưa có API key"})
                    return
            try:
                models = validate_provider(base_url.strip(), api_key)
            except ProviderProbeError as exc:
                self._json(422, {"error": str(exc)})
                return
            self._json(200, {"models": models, "apiKeyOk": True})

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
                self._json(400, {"error": "invalid text"})
                return
            text = payload.get("text")
            if not isinstance(text, str):
                self._json(400, {"error": "invalid text"})
                return
            stream_turn(self, app, session_id, text)

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
