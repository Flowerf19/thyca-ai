"""Navigation moved to the shared shell: thycaHeader() in ui/share/shell.js
emits the same .page-links strip on every page, marking the current one.
The old navigation.js memory-scope submenu (lazy /api/bots fetch,
thyca:memory-scope events, ?bot_id= links) is gone — scoping now happens
inside the memories page toolbar via #memory-bot, covered by
test_webui_memory_edit.py."""
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
SHELL_JS = UI / "share" / "shell.js"
MEM_JS = UI / "memories" / "js" / "memories.js"
SHELL_PAGES = ("memories", "profile", "provider", "dashboard", "trace", "settings")


def _header(current: str = "memories") -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = f"""
    import fs from 'node:fs';
    const src = fs.readFileSync({json.dumps(str(SHELL_JS))}, 'utf8');
    const run = new Function('$', 'document', 'window', src + `
      return {{ header: thycaHeader({json.dumps(current)}), pages: SHELL_PAGES }};
    `);
    console.log(JSON.stringify(run(() => null, {{}}, {{}})));
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
    marks the right link; chat keeps static markup instead."""
    for page in SHELL_PAGES:
        html = (UI / page / "index.html").read_text(encoding="utf-8")
        shell_tag = html.index('src="../share/shell.js"')
        # The header comment also names emitShell — find the real call.
        emit = html.index(f"emitShell('{page}')", shell_tag)
        assert shell_tag < emit, page
    # Chat keeps its own static header — the landing page has no page-links
    # strip; every other page links back to ../chat/index.html via the shell.
    chat = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    assert "emitShell" not in chat
    assert "page-links" not in chat
    assert 'class="site-header"' in chat


def test_scope_navigation_lives_in_the_page_toolbar() -> None:
    """The submenu contract died with navigation.js: nothing in the shared
    shell fetches a roster for a 'Nhật ký' flyout or emits thyca:memory-scope.
    Scope is the in-page #memory-bot select hitting stats?bot_id=."""
    shell = SHELL_JS.read_text(encoding="utf-8")
    assert "memory-scope" not in shell
    assert "thyca:memory-scope" not in shell
    assert "?bot_id=" not in shell
    assert "submenu" not in shell

    js = MEM_JS.read_text(encoding="utf-8")
    assert 'id="memory-bot"' in js
    assert "/api/memory/stats?bot_id=${encodeURIComponent(scope)}" in js
    assert "$('#memory-bot').onchange" in js
    # The scope card announces the chosen bot inside the page — the header
    # link to 'Nhật ký' stays a plain route.
    assert 'id="memory-scope-card"' in js
