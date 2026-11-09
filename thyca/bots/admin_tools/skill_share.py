"""Thyca-only team-skill sharing tool: list/share/unshare.

Only runtime-bound Thyca may manage sharing. Missing identity and child
bots are denied even when they forge a caller identity in arguments.
Trusted Thyca A2A turns remain eligible because they still bind the
Thyca bot id.
"""
from __future__ import annotations

from thyca.memories.identity import current_bot_id
from thyca.tools.registry import ToolSpec


def _deny_child() -> str | None:
    # Only runtime-bound Thyca manages sharing; missing identity fails closed.
    caller = current_bot_id()
    if caller != "thyca":
        return f"denied: skill sharing is Thyca-only (caller: {caller})"
    return None


def _share_spec(service) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        action = args.get("action")
        if action == "list":
            try:
                shared = service.list_shared()
            except Exception as exc:
                return f"skill sharing state invalid: {exc}"
            # List every team skill with its sharing flag; never trust a
            # caller-supplied identity field.
            from pathlib import Path

            root = service.root
            team_root = Path(root) / "skills-team"
            names: list[str] = []
            try:
                if team_root.is_dir() and not team_root.is_symlink():
                    for entry in team_root.iterdir():
                        try:
                            if entry.is_symlink() or not entry.is_dir():
                                continue
                        except OSError:
                            continue
                        names.append(entry.name)
            except OSError as exc:
                return f"skill list failed: {exc}"
            names.sort()
            shared_set = set(shared)
            lines = [f"- {name} ({'shared' if name in shared_set else 'unshared'})"
                     for name in names]
            if not lines:
                return "no team skills"
            return "\n".join(lines)
        if action in ("share", "unshare"):
            name = args.get("name")
            if not isinstance(name, str) or not name.strip():
                return f"skill {action} failed: name must be a non-empty string"
            try:
                if action == "share":
                    service.share(name.strip())
                    return f"shared {name.strip()}"
                else:
                    service.unshare(name.strip())
                    return f"unshared {name.strip()}"
            except ValueError as exc:
                return f"skill {action} failed: {exc}"
            except Exception as exc:
                return f"skill {action} failed: {exc}"
        return "skill manage failed: action must be list, share, or unshare"

    return ToolSpec(
        name="bot_skill_manage",
        description=(
            "Thyca-only team-skill sharing. Actions: list team skills with "
            "sharing status; share one existing team skill with all bots; "
            "unshare it. Sharing is name-based and persists across restarts; "
            "edits preserve the share flag."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=lambda _args: "skill-sharing",
    )


def register_skill_share_tools(registry, service) -> None:
    if registry.get("bot_skill_manage") is None:
        registry.register(_share_spec(service))
