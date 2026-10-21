from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest

from tests.test_cli import FakeLLM
from thyca.app.cli import Cli
from thyca.llm.llm_base import ChatReply
from thyca.serve import ServeError
from thyca.serve.daemon import (
    claim_server_lock,
    pid_alive,
    pid_file,
    running_pid,
    stop_daemon,
)


def test_stale_pidfile_is_cleared(tmp_path: Path) -> None:
    pid_file(tmp_path).write_text("999999\n", encoding="utf-8")
    assert running_pid(tmp_path) is None
    assert not pid_file(tmp_path).exists()


def test_running_pid_detects_self(tmp_path: Path) -> None:
    pid_file(tmp_path).write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert running_pid(tmp_path) == os.getpid()
    assert pid_alive(os.getpid())


def test_stop_without_server(tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="not running"):
        stop_daemon(tmp_path)


def test_cli_daemon_and_stop_flags(tmp_path: Path) -> None:
    out, err = StringIO(), StringIO()
    cli = Cli(
        thyca_dir=tmp_path,
        connect=FakeLLM(ChatReply(content="x")),
        stdout=out,
        stderr=err,
    )
    assert cli.main(["--daemon"]) == 2
    assert "--daemon requires --serve" in err.getvalue()
    err.seek(0)
    err.truncate(0)
    assert cli.main(["--stop"]) == 2
    assert "--stop requires --serve" in err.getvalue()
    err.seek(0)
    err.truncate(0)
    assert cli.main(["--serve", "--daemon", "--stop"]) == 2
    assert "mutually exclusive" in err.getvalue()
    err.seek(0)
    err.truncate(0)
    assert cli.main(["--serve", "--stop"]) == 1
    assert "not running" in err.getvalue()


def test_double_daemonize_before_pidfile_raises(tmp_path: Path, monkeypatch) -> None:
    from thyca.serve.daemon import daemonize

    class _Exited(Exception):
        pass

    monkeypatch.setattr(os, "fork", lambda: 1)  # parent branch, no real fork
    monkeypatch.setattr(
        os, "_exit", lambda code: (_ for _ in ()).throw(_Exited())
    )
    with pytest.raises(_Exited):
        daemonize(tmp_path)  # claims the start lock, then "parent" exits
    # The grandchild never ran, so no pidfile — but the start is claimed:
    # the leaked lock fd stands in for the in-flight daemon.
    assert not pid_file(tmp_path).is_file()
    with pytest.raises(ServeError, match="already running"):
        daemonize(tmp_path)


def _serve_cli(tmp_path: Path) -> tuple[Cli, StringIO, StringIO]:
    out, err = StringIO(), StringIO()
    return (
        Cli(
            thyca_dir=tmp_path,
            connect=FakeLLM(ChatReply(content="x")),
            stdout=out,
            stderr=err,
        ),
        out,
        err,
    )


def _stub_serve_components(monkeypatch) -> list[dict]:
    """Swap MemoryFacade/ChatApp/run so _serve can run without a real server."""
    calls: list[dict] = []

    class _FakeFacade:
        def __init__(self, root, timezone_name=None) -> None:
            self.root = root

    class _FakeChatApp:
        def __init__(self, root, cfg, *, connect=None, stderr=None) -> None:
            self.root = root

    def _fake_run(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("thyca.app.cli.MemoryFacade", _FakeFacade)
    monkeypatch.setattr("thyca.app.chat_app.ChatApp", _FakeChatApp)
    monkeypatch.setattr("thyca.serve.run", _fake_run)
    return calls


def test_foreground_serve_starts_when_unclaimed(tmp_path: Path, monkeypatch) -> None:
    calls = _stub_serve_components(monkeypatch)
    _cli, _out, _err = _serve_cli(tmp_path)
    assert _cli.main(["--serve", "--port", "19599"]) == 0
    assert len(calls) == 1
    assert calls[0]["port"] == 19599
    # Foreground runs do not own the pidfile (the daemon does); the flock
    # is what blocks a second instance.
    assert not pid_file(tmp_path).exists()


def test_foreground_serve_rejects_running_daemon_pidfile(tmp_path: Path, monkeypatch) -> None:
    calls = _stub_serve_components(monkeypatch)
    pid_file(tmp_path).write_text(f"{os.getpid()}\n", encoding="utf-8")
    _cli, _out, err = _serve_cli(tmp_path)
    assert _cli.main(["--serve", "--port", "19599"]) == 1
    assert f"already running (pid {os.getpid()})" in err.getvalue()
    assert calls == []


def test_foreground_serve_rejects_held_serve_lock(tmp_path: Path, monkeypatch) -> None:
    calls = _stub_serve_components(monkeypatch)
    held = claim_server_lock(tmp_path)  # stands in for a live server
    try:
        _cli, _out, err = _serve_cli(tmp_path)
        assert _cli.main(["--serve", "--port", "19599"]) == 1
        assert "already running (start in progress)" in err.getvalue()
        assert calls == []
    finally:
        os.close(held)


def test_claim_server_lock_excludes_second_holder(tmp_path: Path) -> None:
    first = claim_server_lock(tmp_path)
    try:
        with pytest.raises(ServeError, match="already running"):
            claim_server_lock(tmp_path)
    finally:
        os.close(first)
    # Released with the fd: a fresh claim succeeds again.
    second = claim_server_lock(tmp_path)
    os.close(second)


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
def test_m7_loopback_covers_all_loopback_ips() -> None:
    from thyca.serve.server import _is_loopback

    assert _is_loopback("127.0.0.1")
    assert _is_loopback("127.0.0.2")
    assert _is_loopback("::1")
    assert _is_loopback("localhost")
    assert not _is_loopback("0.0.0.0")
    assert not _is_loopback("example.com")
