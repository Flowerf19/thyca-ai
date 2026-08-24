from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest

from thyca.cli import Cli
from thyca.llm.llm_base import ChatReply
from thyca.serve import ServeError
from thyca.serve_daemon import pid_alive, pid_file, running_pid, stop_daemon
from tests.test_cli import FakeLLM


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
