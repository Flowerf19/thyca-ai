"""Banned markers: dropped modules, files, and legacy anatomy stay gone.

One consolidated absence contract (each marker covered exactly once):
dead live-copy modules, removed standalone assets, retired UI anatomy,
superseded tool names, and removed Python APIs. Positive behavior for the
replacements lives in the suites that execute them (Node harnesses,
serve/API tests); this file only pins that the old shapes never return.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
UI = WEBUI / "ui"

CHAT_JS = UI / "chat" / "js" / "chat.js"
APP_JS = UI / "chat" / "js" / "app.js"
DATA_JS = UI / "chat" / "js" / "data.js"
SHARED_DATA_JS = UI / "share" / "data.js"
SIDEBAR_JS = UI / "share" / "sidebar.js"
SHELL_JS = UI / "share" / "shell.js"
SHELL_APP_JS = UI / "share" / "shell-app.js"
SIDEBAR_CSS = UI / "share" / "sidebar.css"
BASE_CSS = UI / "share" / "base.css"
CHAT_CSS = UI / "chat" / "chatcss" / "chat.css"
DASH_JS = UI / "dashboard" / "js" / "dashboard.js"
INDEX_HTML = UI / "chat" / "index.html"


def _sources() -> dict[str, str]:
    return {
        "sidebar.js": SIDEBAR_JS.read_text(encoding="utf-8"),
        "chat.js": CHAT_JS.read_text(encoding="utf-8"),
        "app.js": APP_JS.read_text(encoding="utf-8"),
        "chat/data.js": DATA_JS.read_text(encoding="utf-8"),
        "share/data.js": SHARED_DATA_JS.read_text(encoding="utf-8"),
        "index.html": INDEX_HTML.read_text(encoding="utf-8"),
        "chat.css": CHAT_CSS.read_text(encoding="utf-8"),
        "sidebar.css": SIDEBAR_CSS.read_text(encoding="utf-8"),
        "base.css": BASE_CSS.read_text(encoding="utf-8"),
        "dashboard.js": DASH_JS.read_text(encoding="utf-8"),
    }


def test_dropped_live_copy_modules_are_gone() -> None:
    for rel in (
        "backend/staff",
        "staff",
        "backend/chat-ambient.js",
        "ui/chat/js/chat-ambient.js",
    ):
        assert not (WEBUI / rel).exists(), rel
    src = _sources()
    assert "ambientLineForEvent" not in src["chat.js"]


def test_retired_team_and_kit_anatomy_is_gone() -> None:
    banned = ["team-strip", "team-message", "team-exchange", "team-dossier",
              "team-bot-flap", "team-card-box", "team-nav-back", "session-item",
              "kit.css", "botLookFor", "BOT_LOOK_TINTS", "tintVar",
              "splitExchange", "delegation-tree", "runGuardedStages", "teamArticleFor",
              "openRename", "openDelete", "submitRename", "submitDelete",
              "rename-dialog", "delete-dialog", "rename-form", "delete-form",
              "rename-id", "delete-id", "renameStatus", "deleteStatus",
              "state.saving", "deleteId", "patchJson", "deleteJson",
              "refreshTeamBots", "onBotDeleted",
              "onBotRosterChanged", "onSessionsChanged",
              "Tools used:", "screen-shell", "pick-shell", "is-picking",
              "session-rename", "unread"]
    import re

    visible = "\n".join(_sources().values())
    assert not re.search(r"\brefreshBotDrawer\b", visible)
    for marker in banned:
        assert marker not in visible, marker
    assert "team-bot-flap" not in _sources()["chat.css"]
    assert ".session-action" not in _sources()["sidebar.css"]


def test_superseded_tool_and_journal_shapes_are_gone() -> None:
    import re

    src = _sources()
    # TURN-CURATE: the composer meter/nudge is gone from the entry.
    for marker in ('id="context-nudge"', 'id="context-meter"',
                   'id="context-focus"', "Gọn phiên"):
        assert marker not in src["index.html"], marker
    # Superseded meter internals surface nowhere in the shipped UI.
    blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in UI.rglob("*")
        if path.is_file() and path.suffix in {".js", ".html"}
    )
    for marker in ("sumLastTurnUsage", "lastTurnTools", "renderComposerMeter",
                   'id="meter"', 'id="tool-meter"'):
        assert marker not in blob, marker
    # Reload copy never invents turn status.
    for copy in ("Đang trả lời lượt trước", "Phiên đang trả lời",
                 "SESSION_BUSY_STATUS"):
        assert copy not in src["chat.js"], copy
    # The per-session cost journal left with the old grouped session list.
    for gone in ("Chi phí theo phiên", "cost-sessions", "aggregateSessions",
                 "renderSessions", "NO_SESSION_KEY", "modelTurnCoverage",
                 "estimatedCostSplit", "priceRates"):
        assert gone not in src["dashboard.js"], gone
    assert not re.search(r"session-body", src["chat.js"] + src["share/data.js"])


def test_legacy_pager_styles_are_gone() -> None:
    for path in sorted(UI.rglob("*.css")):
        css = path.read_text(encoding="utf-8")
        assert ".journal-pager" not in css, path.name
        assert ".session-pager" not in css, path.name
        assert ".screen-button" not in css, path.name


def test_removed_python_apis_stay_gone() -> None:
    import thyca.config.schema as schema
    from thyca.sessions.store import SessionStore

    assert not hasattr(schema, "_dict_section")
    for key in ("model", "mcpServers", "pricing", "input", "cache", "output"):
        assert key not in schema._LABELS
    assert not hasattr(SessionStore, "read")
