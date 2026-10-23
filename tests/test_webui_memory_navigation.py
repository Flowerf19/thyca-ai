"""Navigation lives in the shared shell: SHELL_PAGES + pageLinksMarkup() in
ui/share/util.js feed every page's nav — emitShell() pages embed the links
in thycaHeader(), chat's static <nav data-page-nav="chat"> gets hydrated by
util.js itself. The old navigation.js memory-scope submenu (lazy /api/bots
fetch, thyca:memory-scope events, ?bot_id= links) is gone — bot scoping now
happens through sidebar clicks + the ?bot= URL param inside the memories
page, covered by test_webui_memory_edit.py."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "thyca" / "webui" / "ui"
UTIL_JS = UI / "share" / "util.js"
SHELL_JS = UI / "share" / "shell.js"
MEM_JS = UI / "memories" / "js" / "memories.js"
SHELL_PAGES = ("memories", "profile", "provider", "dashboard", "trace", "settings")


def _header(current: str = "memories") -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = f"""
    import fs from 'node:fs';
    const util = fs.readFileSync({json.dumps(str(UTIL_JS))}, 'utf8');
    const shell = fs.readFileSync({json.dumps(str(SHELL_JS))}, 'utf8');
    /* util.js declares $/SHELL_PAGES/pageLinksMarkup which shell.js consumes;
       one shared scope for both. The document stub reports no data-page-nav
       element, so util.js's self-hydration no-ops. */
    const run = new Function('document', 'window', util + shell + `
      return {{ header: thycaHeader({json.dumps(current)}), pages: SHELL_PAGES }};
    `);
    console.log(JSON.stringify(run({{ querySelector: () => null, querySelectorAll: () => [] }}, {{}})));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=15,
        env={**os.environ},
    )
    return json.loads(result.stdout)


def test_header_links_every_page_and_marks_current() -> None:
    out = _header("memories")
    header = out["header"]
    links = re.findall(
        r'<a href="([^"]+)" data-page-link="([^"]+)"([^>]*)>([^<]+)</a>', header)
    # One relative route per page, in the shell's own order.
    assert [page for _href, page, _rest, _label in links] == [
        "chat", "memories", "profile", "provider", "dashboard", "trace", "settings"]
    for href, page, _rest, _label in links:
        assert href == f"../{page}/index.html"
        assert (UI / page / "index.html").is_file(), href
    # The current page is the only marked link.
    marks = {page: rest for _href, page, rest, _label in links}
    assert 'class="is-current"' in marks["memories"]
    assert 'aria-current="page"' in marks["memories"]
    assert all("is-current" not in rest for page, rest in marks.items()
               if page != "memories")
    assert [label for *_x, label in links] == [
        "Trò chuyện", "Nhật ký", "Hồ sơ", "Provider", "Sử dụng", "Trace",
        "Cài đặt"]


def test_every_shell_page_emits_its_own_id() -> None:
    """Each rebuilt page loads shell.js then emitShell('<id>') so the header
    marks the right link; chat ships static markup and lets util.js hydrate
    its data-page-nav stub instead."""
    for page in SHELL_PAGES:
        html = (UI / page / "index.html").read_text(encoding="utf-8")
        shell_tag = html.index('src="../share/shell.js"')
        # The header comment also names emitShell — find the real call.
        emit = html.index(f"emitShell('{page}')", shell_tag)
        assert shell_tag < emit, page
    # Chat keeps its own static header — the landing page still carries the
    # shared nav as an empty <nav data-page-nav="chat"> that util.js fills
    # on load; every other page links back to ../chat/index.html.
    chat = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    assert "emitShell" not in chat
    assert 'class="page-links"' in chat
    assert 'data-page-nav="chat"' in chat
    assert 'src="../share/util.js"' in chat
    assert 'class="site-header"' in chat


def test_scope_navigation_flows_through_sidebar_and_url() -> None:
    """The submenu contract died with navigation.js: nothing in the shared
    shell fetches a roster for a 'Nhật ký' flyout or emits thyca:memory-scope.
    Scope is now sidebar clicks -> selectBot() -> ?bot= deep link ->
    stats?bot_id=; the toolbar selects are client-side kind/use filters."""
    shell = SHELL_JS.read_text(encoding="utf-8")
    assert "memory-scope" not in shell
    assert "thyca:memory-scope" not in shell
    assert "?bot_id=" not in shell
    assert "submenu" not in shell

    js = MEM_JS.read_text(encoding="utf-8")
    # The old toolbar bot select is gone — kind/use/search filter in-page.
    assert 'id="memory-bot"' not in js
    for marker in ('id="memory-kind"', 'id="memory-use"', 'id="memory-search"'):
        assert marker in js, marker
    # Sidebar clicks scope the journal: selectBot() rewrites ?bot=, the boot
    # reads it back, the stats call carries bot_id=, and the scope card's
    # "Bỏ lọc" button clears the filter.
    assert "function selectBot(id)" in js
    assert "'?bot=' + encodeURIComponent" in js
    assert "ctx.updateRoute({ search: q })" in js
    assert "new URLSearchParams(ctx.route.search)" in js
    assert "/api/memory/stats?bot_id=${encodeURIComponent(scope)}" in js
    assert 'id="memory-scope-card"' in js
    assert "mem-scope-clear" in js
    # The sidebar still delegates bot cards to the page's selectBot.
    sidebar = (UI / "share" / "sidebar.js").read_text(encoding="utf-8")
    assert "selectBot(b.id)" in sidebar
