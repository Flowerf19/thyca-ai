"""Cross-instance coordination for the model registry vs bot assignment.

The config file (``config.json``) and the per-bot model settings
(``bots/<id>/settings.json`` plus the legacy ``TWIN.md`` model key) are two
separate stores with a shared invariant: no persistent explicit bot reference
may name a model that the config no longer registers. (A bot set to
``default`` inherits the global default and is never an explicit reference;
an unregistered global default itself stays legal by long-standing contract.)

``store.save()`` locks only the final config write, and bot settings take
only per-bot locks, so an uncoordinated check-then-act races: a bot can
acquire a model between the deletion reference check and the config save
(or vice versa). Every operation below must hold the coordination lock
across its whole check-and-mutate critical section:

Lock order (global, must never be inverted):
  1. model coordination lock (this module) — outermost, one acquisition
     per operation at its outermost layer
  2. config file lock (``store.save`` → ``config.json.lock``) — only inside (1)
  3. per-bot lock (``bots/<id>.lock``) — only inside (1), never before (1)

``FileLock`` is not reentrant (verified: a same-thread nested acquire blocks
until timeout), so helpers that run inside a held section must never acquire
this lock again. Each participant below acquires it exactly once.

Participants (all supported in-server writers):
  * ``POST /api/config`` (``thyca.serve.config_api``) — full replacement,
    including every model removal. Conditional writes carry the revision
    from a fresh ``GET``; mismatches fail with 409 instead of replacing.
  * ``GET /api/config`` — values, metadata, and revision are snapshotted
    under this lock so a POST cannot slip in between the values read and
    the revision read.
  * ``POST /api/bots/<id>/settings`` via ``ChatApp.update_bot_settings`` —
    reloads the config inside the lock, then validates and writes.
  * admin ``bot_create`` / ``bot_edit_card`` — validate the new model
    against the freshly loaded config inside the lock, then create/update.

Revisions are HMAC-SHA256 over the config plus auth-file bytes with a
machine-local 0600 key (``config_revision.key``, created once via O_EXCL
so concurrent first uses in any process converge). Content-derived, so
key-only edits rotate the revision while the value itself reveals nothing
about any secret. Works across processes (read-only after creation);
a missing/corrupt key or unreadable input fails closed. Legacy callers
that omit ``baseRevision`` keep unconditional replace — reported as a
residual and never to be assumed safe.

Deliberately out of scope (uncoordinated by design, never silently safe):
direct ``store.save()`` / ``registry`` callers (tests, seeding, hand edits)
and any external file edit bypass this lock. The server guard still enforces
the invariant per config write; concurrent uncoordinated writes keep
last-writer-wins semantics for unrelated fields.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

#: How long a participant waits for the coordination lock before failing
#: closed (matches the per-file lock timeouts elsewhere).
LOCK_TIMEOUT_S = 5.0

LOCK_FILENAME = "config_models.lock"


class CoordinationError(RuntimeError):
    """The coordination lock could not be acquired (fail closed, retryable)."""


def lock_path_for(root: Path) -> Path:
    """Path of the coordination lock file for one Thyca data dir."""
    return Path(root) / LOCK_FILENAME


@contextmanager
def hold(root: Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold the model coordination lock for ``root``.

    Raises :class:`CoordinationError` (never blocks forever) when the lock
    is contended past ``timeout`` or the lock file itself is unusable.
    Not reentrant: do not nest.
    """
    try:
        with FileLock(str(lock_path_for(root)), timeout=timeout):
            yield
    except FileLockTimeout as exc:
        raise CoordinationError("hệ thống đang bận, thử lại sau") from exc
    except CoordinationError:
        raise
    except OSError as exc:
        raise CoordinationError("hệ thống đang bận, thử lại sau") from exc
