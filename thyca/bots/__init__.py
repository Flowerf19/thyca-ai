"""Team bots package: registry, runtime, bus, admin tools, scheduler."""
from __future__ import annotations

from thyca.bots.admin_tools import ADMIN_TOOL_NAMES, AdminDeps, register_admin_tools
from thyca.bots.bus import Bus, Envelope, new_span_id, new_trace_id
from thyca.bots.card import (
    BASELINE_BOT_TOOLS,
    BotCard,
    BotCardDoc,
    parse_bot_card,
    render_bot_card,
    with_baseline_tools,
)
from thyca.bots.policy import BotPolicy
from thyca.bots.registry import BotRegistry, RegistryError
from thyca.bots.runtime import BotRuntime, filtered_tool_schemas
from thyca.bots.artifacts import BusArtifactStore
from thyca.bots.scheduler import due, tick
from thyca.bots.tasks import OpenTask, derive_open_tasks, format_task_index

__all__ = [
    "ADMIN_TOOL_NAMES",
    "AdminDeps",
    "BASELINE_BOT_TOOLS",
    "BotCard",
    "BotCardDoc",
    "BotPolicy",
    "BotRegistry",
    "BotRuntime",
    "Bus",
    "BusArtifactStore",
    "Envelope",
    "RegistryError",
    "due",
    "OpenTask",
    "derive_open_tasks",
    "filtered_tool_schemas",
    "format_task_index",
    "new_span_id",
    "new_trace_id",
    "parse_bot_card",
    "register_admin_tools",
    "render_bot_card",
    "tick",
    "with_baseline_tools",
]
