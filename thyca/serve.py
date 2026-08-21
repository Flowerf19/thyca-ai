"""Loopback HTTP for webui + memory stats. No writes."""
from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import unquote, urlparse

from thyca.tools.memory import MemoryFacade

LOOPBACK = frozenset({"127.0.0.1", "localhost"})


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
) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ServeError("bind must be loopback")
    root = webui.resolve()
    if not root.is_dir():
        raise ServeError(f"webui missing: {webui}")
    httpd = ThreadingHTTPServer((host, port), _handler(root, facade))
    httpd.allow_reuse_address = True
    return httpd


def run(*, host: str, port: int, webui: Path, facade: MemoryFacade, stdout) -> None:
    httpd = make_server(host=host, port=port, webui=webui, facade=facade)
    bound_host, bound_port = httpd.server_address[:2]
    print(f"http://{bound_host}:{bound_port}/", file=stdout, flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def _handler(webui: Path, facade: MemoryFacade) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/memory/stats":
                self._stats()
                return
            self._static(path)

        def do_POST(self) -> None:
            self._send(405, b"method not allowed", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _stats(self) -> None:
            try:
                payload = asdict(facade.stats())
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception:
                body = json.dumps({"error": "memory stats unavailable"}).encode("utf-8")
                self._send(503, body, "application/json; charset=utf-8")
                return
            self._send(200, body, "application/json; charset=utf-8")

        def _static(self, url_path: str) -> None:
            target = _safe_file(webui, url_path)
            if target is None:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            data = target.read_bytes()
            self._send(200, data, _content_type(target))

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
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
