"""Loopback memory stats HTTP — memory-usage-stats GOAL-003/004."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from thyca.cli import Cli, build_parser
from thyca.serve import ServeError, default_webui, make_server
from thyca.tools.memory import MemoryFacade

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
WEBUI = default_webui()


def _url(httpd, path: str) -> str:
    port = httpd.server_address[1]
    return f"http://127.0.0.1:{port}{path}"


def _start(tmp_path: Path, facade: MemoryFacade | None = None):
    memory = facade or MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(host="127.0.0.1", port=0, webui=WEBUI, facade=memory)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, memory


def _stop(httpd, thread: threading.Thread) -> None:
    httpd.shutdown()
    thread.join(timeout=2)
    httpd.server_close()


def test_rejects_non_loopback(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    try:
        make_server(host="0.0.0.0", port=0, webui=WEBUI, facade=facade)
    except ServeError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("expected refuse")


def test_stats_json_and_static(tmp_path: Path) -> None:
    httpd, thread, facade = _start(tmp_path)
    try:
        now = datetime(2026, 8, 17, 10, 0, tzinfo=TZ)
        sid = facade.remember("cafe", "likes ca phe den enough", target="memory", now=now)
        facade.get(session_id=sid, now=now)
        with urlopen(_url(httpd, "/api/memory/stats"), timeout=2) as response:
            assert response.headers.get_content_type() == "application/json"
            assert "no-store" in (response.headers.get("Cache-Control") or "")
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["total"] == 1
        assert payload["used"] == 1
        assert payload["unused"] == 0
        assert payload["searched"] == 0
        assert payload["untouched"] == 0
        assert payload["leaves"][0]["get_count"] == 1
        assert payload["leaves"][0]["search_count"] == 0
        with urlopen(_url(httpd, "/"), timeout=2) as response:
            html = response.read().decode("utf-8")
        assert 'data-mode="memories"' in html
        with urlopen(_url(httpd, "/js/memories.js"), timeout=2) as response:
            assert "javascript" in response.headers.get_content_type()
            assert b"pagesFromStats" in response.read()
    finally:
        _stop(httpd, thread)


def test_forget_endpoint(tmp_path: Path) -> None:
    httpd, thread, facade = _start(tmp_path)
    try:
        now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
        sid = facade.remember("cafe", "likes ca phe den enough", target="memory", now=now)
        assert facade.stats(now=now).total == 1
        payload = json.dumps({"session_id": sid}).encode("utf-8")
        request = Request(
            _url(httpd, "/api/memory/forget"),
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body == {"ok": True}
        assert facade.stats(now=now).total == 0
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/memory/forget"),
                    data=b'{"session_id":"memory#ffffffff"}',
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")
    finally:
        _stop(httpd, thread)


def test_post_and_traversal_rejected(tmp_path: Path) -> None:
    httpd, thread, _facade = _start(tmp_path)
    try:
        try:
            urlopen(Request(_url(httpd, "/api/memory/stats"), method="POST", data=b"{}"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 405
        else:
            raise AssertionError("expected 405")
        try:
            urlopen(_url(httpd, "/../pyproject.toml"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")
    finally:
        _stop(httpd, thread)


def test_stats_error_is_503(tmp_path: Path) -> None:
    class Boom:
        def stats(self, now=None):
            raise RuntimeError("sqlite down")

    httpd, thread, _ = _start(tmp_path, facade=Boom())  # type: ignore[arg-type]
    try:
        try:
            urlopen(_url(httpd, "/api/memory/stats"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 503
            body = json.loads(exc.read().decode("utf-8"))
            assert body == {"error": "memory stats unavailable"}
            assert "sqlite" not in str(body)
        else:
            raise AssertionError("expected 503")
    finally:
        _stop(httpd, thread)


def test_default_webui_has_index() -> None:
    assert (WEBUI / "index.html").is_file()
    assert (WEBUI / "js" / "memories.js").is_file()
    raw = (WEBUI / "js" / "memories.js").read_text(encoding="utf-8")
    assert "Theo ngày" in raw
    assert "data-day-filter" in raw
    assert "data-forget" in raw
    assert "pagesFromStats" in raw
    assert "title: escapeHtml(key)" not in raw


def test_index_html_parses() -> None:
    raw = (WEBUI / "index.html").read_text(encoding="utf-8")
    HTMLParser().feed(raw)
    assert 'data-mode="memories"' in raw
    assert "./js/app.js" in raw


def test_cli_serve_flag_conflicts(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--serve"])
    assert args.serve
    assert args.port == 8765
    out, err = StringIO(), StringIO()
    cli = Cli(thyca_dir=tmp_path, stdin=StringIO(""), stdout=out, stderr=err)
    assert cli.main(["--serve", "-p", "hi"]) == 2
    assert "--serve" in err.getvalue()
    assert cli.main(["--serve", "--port", "0"]) == 2
