"""Thyca-only bot administration tools (BE-5).

``bot_create | bot_pause | bot_delete | bot_restore | bot_edit_card |
bot_delegate | bot_stop`` are registered on the shared tool registry, but
the bot runtime filters every ``bot_*`` name out of child-bot snapshots
(master §5: only Thyca's registry carries them), and ``hidden_tools``
enforcement backs that at the gateway. Each handler re-checks that the
in-flight caller is Thyca (or the user: no bot bound), so a leaked spec
can never be used by a child bot.

Submodules group the tools by family: :mod:`peer` (A2A send/fetch),
:mod:`lifecycle` (create/pause/delete/restore/edit/stop), :mod:`messaging`
(message_user/ask_user/report_error/task_open); :mod:`deps` holds the
shared wiring. This package root owns the registration order (an
LLM-facing contract) and the package-level public API.
"""
from __future__ import annotations

from thyca.tools.registry import ToolRegistry

from .deps import AdminDeps
from .lifecycle import (
    _create_spec,
    _delete_spec,
    _edit_card_spec,
    _pause_spec,
    _restore_spec,
    _stop_spec,
    apply_card_edit,
)
from .messaging import (
    _ask_user_spec,
    _message_user_spec,
    _report_error_spec,
    _task_open_spec,
)
from .peer import _delegate_spec, _fetch_spec, _send_spec


ADMIN_TOOL_NAMES = (
    "bot_create",
    "bot_pause",
    "bot_delete",
    "bot_restore",
    "bot_edit_card",
    "bot_delegate",
    "bot_stop",
)



def register_admin_tools(registry: ToolRegistry, deps: AdminDeps) -> None:
    for spec in (
        _create_spec(deps),
        _pause_spec(deps),
        _delete_spec(deps),
        _restore_spec(deps),
        _edit_card_spec(deps),
        _delegate_spec(deps),
        _stop_spec(deps),
        _send_spec(deps),
        _fetch_spec(deps),
        _task_open_spec(deps),
        _message_user_spec(deps),
        _ask_user_spec(deps),
        _report_error_spec(deps),
    ):
        if registry.get(spec.name) is None:
            registry.register(spec)


#: ``bot_*`` names a child bot may hold: peer send/fetch (CBE-3).
# ``task_open`` has no ``bot_`` prefix and passes the check naturally.
CHILD_BOT_TOOLS = frozenset({"bot_send", "bot_fetch"})


def visible_for_bot(schemas: list[dict] | None, bot_id: str | None) -> list[dict] | None:
    """Tool snapshot for one caller: children never see admin ``bot_*``
    tools; ``bot_send``/``bot_fetch`` (peer send/fetch, identity from
    ContextVar) are the ``bot_*`` names a child may see."""
    if not schemas or bot_id is None or bot_id == "thyca":
        return schemas
    return [
        schema
        for schema in schemas
        if schema.get("function", {}).get("name") in CHILD_BOT_TOOLS
        or not str(schema.get("function", {}).get("name", "")).startswith("bot_")
    ]


__all__ = [
    "ADMIN_TOOL_NAMES",
    "AdminDeps",
    "CHILD_BOT_TOOLS",
    "apply_card_edit",
    "register_admin_tools",
    "visible_for_bot",
]
