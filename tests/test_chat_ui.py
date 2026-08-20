from __future__ import annotations

from io import StringIO

from thyca.chat_ui import ChatUi


def test_banner_prompt_assistant_without_color() -> None:
    out = StringIO()
    err = StringIO()
    ui = ChatUi(out, err, color=False)
    ui.banner("2026-08-20T10-00-00_abcd", "demo-model")
    ui.prompt()
    ui.assistant("hello")
    ui.error("boom")
    ui.goodbye()
    text = out.getvalue()
    assert "session 2026-08-20T10-00-00_abcd" in text
    assert "model demo-model" in text
    assert "you> " in text
    assert "thyca\nhello\n" in text
    assert "\033[" not in text
    assert err.getvalue() == "thyca: boom\n"


def test_color_wraps_when_enabled() -> None:
    out = StringIO()
    ui = ChatUi(out, StringIO(), color=True)
    ui.prompt()
    assert "\033[36m" in out.getvalue()
    assert "you> " in out.getvalue()
