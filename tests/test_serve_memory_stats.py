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

from thyca.app.cli import Cli, build_parser
from thyca.serve import ServeError, default_webui, make_server
from thyca.memory.facade import MemoryFacade

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
        assert payload["bot_id"] is None
        assert payload["total"] == 1
        assert payload["used"] == 1
        assert payload["unused"] == 0
        assert payload["searched"] == 0
        assert payload["untouched"] == 0
        assert payload["leaves"][0]["get_count"] == 1
        assert payload["leaves"][0]["search_count"] == 0
        with urlopen(_url(httpd, "/ui/memories/"), timeout=2) as response:
            html = response.read().decode("utf-8")
        # Legacy memories entry aliases resolve to the canonical root app document.
        assert 'id="thyca-page-host"' in html
        assert 'src="../memories/js/memories.js"' in html
        assert 'src="js/memories.js"' not in html
        assert 'src="../share/router.js"' in html
        assert "../share/shell.js" in html
        assert "../share/sidebar.css" in html
        assert "../share/app-shell.css" not in html
        assert 'class="session-item" type="button" data-view=' not in html
        with urlopen(_url(httpd, "/ui/share/shell.js"), timeout=2) as response:
            shell_js = response.read()
        assert b'id="team-bot-list"' in shell_js
        with urlopen(_url(httpd, "/ui/memories/js/memories.js"), timeout=2) as response:
            assert "javascript" in response.headers.get_content_type()
            memory_js = response.read()
        # Page markup lives in PAGE_HTML inside memories.js; the served file
        # also owns the stats/leaves rendering and the leaf actions.
        assert b'id="memory-stats"' in memory_js
        assert b'id="memory-leaves"' in memory_js
        assert b'id="memory-search"' in memory_js
        assert b'data-act="reinforce"' in memory_js
        assert b'data-act="edit"' in memory_js
        assert b'data-act="forget"' in memory_js
        assert b"/api/memory/stats?bot_id=" in memory_js
        assert b"'/api/memory/update'" in memory_js
        assert b"'/api/memory/reinforce'" in memory_js
        assert b"'/api/memory/forget'" in memory_js
        # The old #memory-bot select is gone: kind/usage filter through the
        # ghost selects, bot scope is a sidebar pick (selectBot -> ?bot=<id>)
        # shown as a scope card with a "Bỏ lọc" clear, and long leaf lists
        # page 60 at a time through .mem-pager.
        assert b'id="memory-bot"' not in memory_js
        assert b'id="memory-kind"' in memory_js
        assert b'id="memory-use"' in memory_js
        assert b'id="memory-scope-card"' in memory_js
        assert b'mem-scope-clear' in memory_js
        assert b"'?bot=' + encodeURIComponent" in memory_js
        assert b'LEAF_PAGE = 60' in memory_js
        assert b'mem-pager' in memory_js
        with urlopen(_url(httpd, "/ui/share/data.js"), timeout=2) as response:
            shared_data = response.read()
        assert b"getJson('/api/bots')" in shared_data
        mapping = memory_js.decode("utf-8")
        # Leaf filtering is a text search over title/snippet plus the
        # server-computed expiring/suggest_removal chips — the old
        # data-view="used-more" buckets did not survive the rebuild.
        assert "filteredLeaves" in mapping
        assert "stats.expiring" in mapping
        assert "suggest_removal" in mapping
    finally:
        _stop(httpd, thread)


def test_scoped_memory_stats(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.bots.registry import BotRegistry

    registry = BotRegistry(tmp_path)
    registry.create(BotCard(bot_id="coder", name="Coder", role="C",
                            description="D", persona="P"))
    registry.create(BotCard(bot_id="researcher", name="Researcher", role="R",
                            description="D", persona="P"))
    httpd, thread, facade = _start(tmp_path)
    try:
        now = _fresh_now()
        facade.remember("Own", "coder private note", bots=["coder"], now=now)
        facade.remember("Other", "researcher private note", bots=["researcher"], now=now)
        facade.remember("Team", "shared note", bots=["team"], now=now)
        scoped_stats = json.loads(urlopen(_url(httpd, "/api/memory/stats?bot_id=coder"), timeout=2)
                                  .read().decode("utf-8"))
        assert scoped_stats["bot_id"] == "coder"
        assert scoped_stats["total"] == 1
        global_stats = json.loads(urlopen(_url(httpd, "/api/memory/stats"), timeout=2)
                                  .read().decode("utf-8"))
        assert global_stats["bot_id"] is None
        assert global_stats["total"] == 3

        for path in (
            "/api/memory/stats?bot_id=",
            "/api/memory/stats?bot_id=not_a_bot",
            "/api/memory/stats?bot_id=coder&bot_id=coder",
            "/api/memory/stats?unexpected=1",
        ):
            try:
                urlopen(_url(httpd, path), timeout=2)
            except HTTPError as exc:
                assert exc.code == 404 if "=missing" in path else 400, path
            else:
                raise AssertionError(f"expected scoped request rejection: {path}")
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
    # Single SPA entry: legacy workspace URLs alias to it (see
    # tests/test_webui_spa_frontend.py), so only the chat document ships.
    assert (WEBUI / "ui/chat/index.html").is_file()
    for name in (
        "ui/chat/js/app.js",
        "ui/chat/js/chat.js",
        "ui/chat/js/data.js",
        "ui/memories/js/memories.js",
        "ui/profile/js/profile.js",
        "ui/trace/js/trace.js",
        "ui/provider/js/provider.js",
        "ui/dashboard/js/dashboard.js",
        "ui/share/settings-dialog.js",
        "ui/share/data.js",
        "ui/share/shell.js",
        "ui/share/shell-app.js",
        "ui/share/sidebar.js",
        "ui/share/util.js",
        "ui/share/avatars/avatars.js",
        "ui/share/avatars/idle.js",
        "ui/share/avatars/studio.js",
    ):
        assert (WEBUI / name).is_file(), name
    raw = (WEBUI / "ui" / "memories" / "js" / "memories.js").read_text(encoding="utf-8")
    assert "'/api/memory/update'" in raw
    assert "'/api/memory/reinforce'" in raw
    assert "'/api/memory/forget'" in raw
    # Hồ sơ is its own screen now: the canonical write lives there.
    assert "canonical" not in raw
    profile = (WEBUI / "ui" / "profile" / "js" / "profile.js").read_text(encoding="utf-8")
    assert "postJson('/api/memory/canonical'" in profile


def test_profile_screen_renders_markdown() -> None:
    """Hồ sơ is its own screen: one tab per relationship ("Bạn và <bot>") —
    the shared tab renders the canonical files as markdown cards, other bots
    a read-only TWIN.md card.

    The root app serves the Chat document and mounts Profile into its retained
    workspace. The factory owns the tab/doc markup, markdown pipeline, and
    page-local edit dialog; util.js owns shared route links."""
    script = (WEBUI / "ui" / "profile" / "js" / "profile.js").read_text(encoding="utf-8")
    css = (WEBUI / "ui" / "profile" / "profilecss" / "profile.css").read_text(encoding="utf-8")
    util = (WEBUI / "ui" / "share" / "util.js").read_text(encoding="utf-8")
    shell_css = (WEBUI / "ui" / "share" / "app-shell.css").read_text(encoding="utf-8")
    memories = (WEBUI / "ui" / "memories" / "js" / "memories.js").read_text(encoding="utf-8")

    # Navigation is the shared SHELL_PAGES table, one row per screen — it
    # lives in util.js so chat's static markup renders the same nav.
    assert "['profile', 'Hồ sơ', '../profile/index.html']" in util
    # The SPA entry mounts the profile module; the legacy standalone
    # document is gone (server aliases resolve it to the entry).
    app = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")
    assert 'src="../profile/js/profile.js"' in app
    assert "../share/shell.js" in app
    # The script fills the shell's .workspace: sidebar bot cards act as the
    # tabs ("Bạn và <bot>"), the shared (thyca) tab carrying the editable
    # canonical docs.
    assert 'id="profile-root"' in script
    assert "'Bạn và '" in script
    assert "sharedBotId" not in script
    assert "let selectedBot = '';" in script
    assert "state.tab" in script
    # The edit dialog is still one dialog with a real editor textarea — its
    # larger footprint is page CSS, not a shared dialog size.
    assert 'id="edit-dialog"' in script
    assert "#edit-dialog" in css
    assert "min-height:46vh" in css
    # Body text is markdown, rendered by the shared chat renderer (a module,
    # so profile.js falls back to escaped paragraphs before it evaluates).
    assert "../share/markdown.js" in app
    assert "window.formatMarkdown" in script
    assert "contentHtml(file.content)" in script
    assert "selectCanonical" in script
    assert "postJson('/api/memory/canonical'" in script
    # Nhật ký no longer carries the profile view or its dialog.
    assert "/api/memory/canonical" not in memories

    # Switching tabs re-renders the docs, deep-links ?bot=<id>, and a #<file>
    # hash scrolls the shared tab to that card — the loader status must not
    # outlive the load.
    show_body = script[script.index("function showTab") : script.index("async function loadFiles")]
    assert "renderContent();" in show_body
    assert "'?bot=' + encodeURIComponent" in show_body
    assert "scrollIntoView" in show_body
    load_body = script[script.index("async function loadFiles") : script.index("function setBusy")]
    assert "selectCanonical(stats.files)" in load_body
    assert "renderContent();" in load_body
    assert "setStatus(el.status, '');" in load_body
    # A stray escape in the route hash must not throw; the decoder is guarded.
    assert "function decodeHash()" in script
    assert "try { return decodeURIComponent" in script
    assert "onRoute(route)" in script
    assert "showShared('file-' + hash)" in script

    # Blanking a profile is a real action, so it asks first rather than being
    # blocked by native `required` validation (the factory owns the dialog
    # markup; the exact textarea carries no `required` attribute).
    assert '<textarea id="edit-content" spellcheck="false">' in script
    assert 'confirm(`Xoá trắng nội dung “${name}”?`)' in script

    # Switching tabs moves focus to the sidebar bot card, whose own label
    # announces the bot; re-reading a whole file body would be noise.
    assert 'id="profile-content" aria-live' not in script

    # Heading and file card share the surface width (no centred 75% column),
    # and an empty status line holds no space — it is hidden, not just blank.
    assert "max-width: 75%;" not in css
    assert ".page-status[hidden]" in shell_css
    assert "node.hidden = !text" in script

    # Canonical files render as .profile-doc paper cards with a data-edit
    # button; non-shared bots get a read-only .profile-bot TWIN.md card —
    # the old .profile-file rail and .session-item grid did not survive.
    assert 'class="paper-card profile-doc"' in script
    assert 'data-edit="' in script
    assert ".profile-doc" in css
    assert ".profile-doc + .profile-doc" in css
    assert 'class="paper-card profile-bot"' in script
    assert "profile-readonly" in script
    assert "session-body" not in script


def test_index_html_parses() -> None:
    """Single SPA entry: ui/chat/index.html parses and mounts every page
    module; each module registers its workspace with the router (Trace
    stays Dashboard's load-once detail renderer, not a route)."""
    raw = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")
    HTMLParser().feed(raw)
    for script in ("js/app.js", "js/chat.js", "js/data.js"):
        assert f'src="{script}"' in raw
    for asset in ("../share/util.js", "../share/data.js", "../share/sidebar.css"):
        assert asset in raw
    for page in ("dashboard", "memories", "profile", "provider"):
        assert f'src="../{page}/js/{page}.js"' in raw, page
    assert 'src="../trace/js/trace.js"' in raw
    assert 'src="../share/settings-dialog.js"' in raw   # shared dialog, not a page
    for page in ("dashboard", "memories", "profile", "provider"):
        js = (WEBUI / "ui" / page / "js" / f"{page}.js").read_text(encoding="utf-8")
        assert f"ThycaRouter.register('{page}'" in js, page
    trace = (WEBUI / "ui" / "trace" / "js" / "trace.js").read_text(encoding="utf-8")
    assert "window.ThycaTraceDetail" in trace
    assert "ThycaRouter.register" not in trace


def test_cli_serve_flag_conflicts(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--serve"])
    assert args.serve
    assert args.port == 19502
    out, err = StringIO(), StringIO()
    cli = Cli(thyca_dir=tmp_path, stdin=StringIO(""), stdout=out, stderr=err)
    assert cli.main(["--serve", "-p", "hi"]) == 2
    assert "--serve" in err.getvalue()
    assert cli.main(["--serve", "--port", "0"]) == 2


def test_canonical_write_endpoint(tmp_path_factory) -> None:
    from thyca.memory.archived import ArchiveError
    from thyca.memory.facade import MemoryFacade
    facade = MemoryFacade(tmp_path_factory.mktemp("canon"), timezone_name="Asia/Ho_Chi_Minh")
    facade.write_canonical("USER.md", "# User\n\nTên: Hòa\n")
    assert (facade.thyca_dir / "USER.md").read_text(encoding="utf-8").startswith("# User")
    with pytest.raises(ArchiveError):
        facade.write_canonical("../evil.md", "x")
    with pytest.raises(ArchiveError):
        facade.write_canonical("MEMORY.md", "x")


def test_null_byte_path_is_404_not_dropped(tmp_path: Path) -> None:
    from thyca.serve.static import safe_file

    assert safe_file(WEBUI, "/%00") is None
    assert safe_file(WEBUI, "/\x00") is None
    httpd, thread, _ = _start(tmp_path)
    try:
        try:
            urlopen(_url(httpd, "/%00"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404 for null-byte path")
    finally:
        _stop(httpd, thread)
