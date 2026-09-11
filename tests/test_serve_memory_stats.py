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

import pytest

from thyca.cli import Cli, build_parser
from thyca.serve import ServeError, default_webui, make_server
from thyca.tools.memory import MemoryFacade

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
WEBUI = default_webui()


def _url(httpd, path: str) -> str:
    port = httpd.server_address[1]
    return f"http://127.0.0.1:{port}{path}"


def _fresh_now() -> datetime:
    """Wall-clock now: leaves the entry's TTL ahead of the real clock.

    A hardcoded past date goes stale — the endpoint refreshes the index with
    the real clock, which purges anything whose TTL already ran out.
    """
    return datetime.now(TZ).replace(second=0, microsecond=0)


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
        sid = facade.remember("cafe", "likes ca phe den enough")
        facade.get(session_id=sid)
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
        with urlopen(_url(httpd, "/memories.html"), timeout=2) as response:
            html = response.read().decode("utf-8")
        assert 'id="memory-list"' in html
        assert './memories.js' in html
        with urlopen(_url(httpd, "/memories.js"), timeout=2) as response:
            assert "javascript" in response.headers.get_content_type()
            memory_js = response.read()
        assert b'getJson("/api/memory/stats")' in memory_js
        assert b'"/api/memory/update"' in memory_js
        mapping = (WEBUI / "backend" / "memory-data.js").read_text(encoding="utf-8")
        assert "selectMemories" in mapping
        assert 'view === "used-more"' in mapping
    finally:
        _stop(httpd, thread)


def test_forget_endpoint(tmp_path: Path) -> None:
    httpd, thread, facade = _start(tmp_path)
    try:
        now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
        sid = facade.remember("cafe", "likes ca phe den enough", now=now)
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


def test_update_and_reinforce_endpoints(tmp_path: Path) -> None:
    httpd, thread, facade = _start(tmp_path)
    try:
        now = _fresh_now()
        sid = facade.remember("cafe", "likes ca phe den enough", now=now)
        update = json.dumps({"session_id": sid, "topic": "tra"}).encode("utf-8")
        request = Request(
            _url(httpd, "/api/memory/update"),
            data=update,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body == {"ok": True}
        text = facade.get(session_id=sid, now=now)
        assert "tra" in text
        assert "likes ca phe den enough" in text
        reinforce = json.dumps({"session_id": sid}).encode("utf-8")
        request = Request(
            _url(httpd, "/api/memory/reinforce"),
            data=reinforce,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["expires_at"]
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/memory/update"),
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("expected 400")
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
    assert WEBUI.name == "webui"
    for name in (
        "index.html",
        "memories.html",
        "profile.html",
        "trace.html",
        "provider.html",
        "dashboard.html",
    ):
        assert (WEBUI / name).is_file()
    for name in ("app.js", "memories.js", "profile.js", "trace.js", "provider.js", "cost.js", "usage.js"):
        assert (WEBUI / name).is_file()
    raw = (WEBUI / "memories.js").read_text(encoding="utf-8")
    assert '"/api/memory/update"' in raw
    assert '"/api/memory/reinforce"' in raw
    assert '"/api/memory/forget"' in raw
    # Hồ sơ is its own screen now: the canonical write lives there.
    assert "canonical" not in raw
    profile = (WEBUI / "profile.js").read_text(encoding="utf-8")
    assert 'postJson("/api/memory/canonical"' in profile


def test_profile_screen_renders_markdown() -> None:
    """Hồ sơ is its own screen: mục lục route, file switcher, markdown body."""
    html = (WEBUI / "profile.html").read_text(encoding="utf-8")
    script = (WEBUI / "profile.js").read_text(encoding="utf-8")
    css = (WEBUI / "profile.css").read_text(encoding="utf-8")
    navigation = (WEBUI / "navigation.js").read_text(encoding="utf-8")
    memories = (WEBUI / "memories.html").read_text(encoding="utf-8")

    assert '["profile.html", "Hồ sơ"' in navigation
    assert 'id="profile-nav"' in html
    assert "./profile.js" in html
    assert "./navigation.js" in html
    # The edit dialog moves across unchanged; only its size grows.
    assert 'id="canonical-dialog"' in html
    assert "min(56rem, calc(100vw - 2rem))" in css
    # Body text is markdown, rendered by the shared chat renderer.
    assert 'from "./backend/markdown.js"' in script
    assert "formatMarkdown(file.content)" in script
    assert "selectCanonical" in script
    assert 'postJson("/api/memory/canonical"' in script
    # Nhật ký no longer carries the profile view or its dialog.
    assert 'data-view="profile"' not in memories
    assert "canonical" not in memories

    # Switching files must not rebuild the list: the removed button would take
    # keyboard focus with it, and the loader status must not outlive the load.
    assert "function markNav()" in script
    show_body = script[script.index("function show(") : script.index("function activeNavButton")]
    assert "markNav();" in show_body
    assert "renderNav();" not in show_body
    load_body = script[script.index("async function load()") : script.index("function openDialog")]
    assert "renderNav();" in load_body
    assert "setStatus();" in show_body
    # A stray escape in the hash must not throw out of the hashchange handler;
    # the guard lives in the shared helper (tests/test_webui_format.py).
    assert 'import { decodeHash } from "./backend/format.js";' in script
    assert "decodeHash(location.hash)" in script
    assert "decodeURIComponent(" not in script

    # Blanking a profile is a real action, so it asks first rather than being
    # blocked by native `required` validation.
    assert 'id="canonical-content" spellcheck' in html
    assert "required" not in html
    assert 'confirm(`Xoá nội dung “${name}”?`)' in script

    # Switching files moves focus to the nav button, whose own label announces
    # the file; re-reading a whole file body would be noise.
    assert 'id="profile-content" aria-live' not in html

    # Heading, status and prose share one measure (measured 1440px: all three
    # span the same box instead of the prose being centred on its own).
    assert ".profile-surface > .screen-heading," in css
    assert "max-width: 60rem;" in css

    # Sidebar rows reuse the shape dashboard.html and memories.html use, so the
    # shared .session-item/.session-name rules apply unchanged (measured: row
    # 58.4px tall, name 41px in, 15.04px — identical on all three screens).
    # The chat variant (.session-body) lays the same row out taller and shifts
    # the name, which is what this screen used to do.
    assert 'className = "session-body"' not in script
    assert 'icon.className = "session-icon"' in script
    assert 'name.className = "session-name"' in script

    # Spacing matches the screens that put a heading above content: 1rem under
    # the heading (same as .dashboard-block > .screen-heading), and an empty
    # status line holds no space (measured: heading 71px + 16px gap on both).
    assert ".profile-surface > .screen-heading {" in css
    assert "margin-block-end: 1rem;" in css
    assert ".profile-status:empty {" in css


def test_index_html_parses() -> None:
    expected = {
        "index.html": './app.js',
        "memories.html": './memories.js',
        "trace.html": './trace.js',
        "provider.html": './provider.js',
        "dashboard.html": './cost.js',
        "profile.html": './profile.js',
    }
    for name, script in expected.items():
        raw = (WEBUI / name).read_text(encoding="utf-8")
        HTMLParser().feed(raw)
        assert script in raw
        assert './navigation.js' in raw


def test_cli_serve_flag_conflicts(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--serve"])
    assert args.serve
    assert args.port == 8765
    out, err = StringIO(), StringIO()
    cli = Cli(thyca_dir=tmp_path, stdin=StringIO(""), stdout=out, stderr=err)
    assert cli.main(["--serve", "-p", "hi"]) == 2
    assert "--serve" in err.getvalue()
    assert cli.main(["--serve", "--port", "0"]) == 2


def test_canonical_write_endpoint(tmp_path_factory) -> None:
    from thyca.memory.archived import ArchiveError
    from thyca.tools.memory import MemoryFacade
    facade = MemoryFacade(tmp_path_factory.mktemp("canon"), timezone_name="Asia/Ho_Chi_Minh")
    facade.write_canonical("USER.md", "# User\n\nTên: Hòa\n")
    assert (facade.thyca_dir / "USER.md").read_text(encoding="utf-8").startswith("# User")
    with pytest.raises(ArchiveError):
        facade.write_canonical("../evil.md", "x")
    with pytest.raises(ArchiveError):
        facade.write_canonical("MEMORY.md", "x")
