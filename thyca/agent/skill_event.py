"""Skill-load event classification: is this ``read`` call a skill load?

Pure classification — no event emission, no writes. The containment check
resolves both paths, which stats the filesystem once; callers accept that
(the read itself happens later via the dispatcher).

:class:`Act` calls :func:`classify_skill_read` right before dispatch;
everything else (skill index scan, ``write``/``edit`` authoring, reads
outside the skills dir, ``bash`` inside a skill dir) stays an ordinary
``tool.*`` event.

Known limitation (accepted residual): the check and the dispatch are two
separate instants — a symlink swapped in between could make the event
mislabel the read (TOCTOU). The failure direction is safe: classification
errors never turn a foreign path into ``skill.*``, the wire never carries
path/content, and the skills dir is 0700 so an attacker needs local trust
already. Eliminating it would need ``openat``/``O_NOFOLLOW`` in the read
tool, which is out of scope for this layer.
"""
from __future__ import annotations

from pathlib import Path

from thyca.skills import NAME_MAX, _NAME_RE

_FALLBACK = "skill"


def is_skill_name(name: str) -> bool:
    """Same grammar the store validates at scan time: [a-z0-9-], max 64."""
    return len(name) <= NAME_MAX and bool(_NAME_RE.fullmatch(name))


def classify_skill_read(root: Path, path: Path) -> str | None:
    """Return the sanitized skill name when ``path`` points inside a skill.

    ``root`` is ``SkillStore.root`` (``~/.thyca/skills``). Both sides are
    resolved so a symlinked ``~/.thyca`` still classifies, and a lexical
    ``..`` segment can never survive containment: after ``resolve()`` a
    path that escaped the root simply fails ``relative_to``. Resolving or
    comparing never raises here — any error returns ``None`` (fail closed
    to a plain ``tool.*`` event). The name is the first path segment below
    ``root``; the scan-time name grammar is re-applied so an invalid
    directory can never reach the wire as a name.
    """
    try:
        root = Path(root).expanduser().resolve()
        path = Path(path).expanduser().resolve()
        relative = path.relative_to(root)
    except (OSError, ValueError, TypeError):
        return None
    if not relative.parts:
        return None
    name = relative.parts[0]
    if not is_skill_name(name):
        return None
    return name


def public_skill_name(name: str | None) -> str:
    """Event-safe display name: already-validated names pass, else fallback."""
    if name is None:
        return _FALLBACK
    return name if is_skill_name(name) else _FALLBACK
