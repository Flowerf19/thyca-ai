"""Loopback HTTP for webui, memory stats, and chat (bootstrap).

Routing lives in ``routes``; this module only binds loopback, builds the
server, and runs it. The loopback-only bind is invariant: ``make_server``
rejects any non-loopback host.
"""
from __future__ import annotations

import signal
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from thyca.serve.routes import make_handler
from thyca.tools.memory import MemoryFacade

if TYPE_CHECKING:
    from thyca.app.chat_app import ChatApp

LOOPBACK = frozenset({"127.0.0.1", "localhost"})


class ServeError(RuntimeError):
    """Web UI server could not start or bind."""


def _raise_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def default_webui() -> Path:
    return Path(__file__).resolve().parent.parent / "webui"


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
        (host, port), make_handler(root, facade, chat, config_file)
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
