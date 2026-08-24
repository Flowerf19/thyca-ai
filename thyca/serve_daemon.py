"""Detach --serve from the controlling terminal."""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from thyca.serve import ServeError

PID_NAME = "serve.pid"
LOG_NAME = "serve.log"


def pid_file(root: Path) -> Path:
    return root / PID_NAME


def log_file(root: Path) -> Path:
    return root / LOG_NAME


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_pid(root: Path) -> int | None:
    path = pid_file(root)
    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        path.unlink(missing_ok=True)
        return None
    if pid_alive(pid):
        return pid
    path.unlink(missing_ok=True)
    return None


def daemonize(root: Path) -> None:
    existing = running_pid(root)
    if existing is not None:
        raise ServeError(f"already running (pid {existing})")
    log_path = log_file(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    log = open(log_path, "a", encoding="utf-8")
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    log.close()
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    pid_file(root).write_text(f"{os.getpid()}\n", encoding="utf-8")


def stop_daemon(root: Path, timeout: float = 5.0) -> int:
    pid = running_pid(root)
    if pid is None:
        raise ServeError("serve is not running")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            pid_file(root).unlink(missing_ok=True)
            return pid
        time.sleep(0.05)
    raise ServeError(f"serve pid {pid} did not exit")
