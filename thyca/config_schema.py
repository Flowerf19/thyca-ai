"""Config schema for the WebUI settings panel.

Derives a UI-readable schema from the frozen dataclasses in
:mod:`thyca.config`, so a new ``Config`` field shows up in the settings
panel without any frontend change. Labels live in ``_LABELS``; unlabeled
fields fall back to their key name.
"""
from __future__ import annotations

import dataclasses
from dataclasses import fields
from typing import Any
from thyca.config import (
    LimitsCfg,
    ProviderCfg,
    REASONING_EFFORTS,
    TimelineCfg,
)

_LABELS: dict[str, str] = {
    "provider": "Nhà cung cấp",
    "baseUrl": "Base URL",
    "apiKeyEnv": "Tên biến môi trường API key",
    "model": "Model",
    "reasoningEffort": "Mức suy luận (thinking)",
    "apiKey": "API key",
    "mcpServers": "MCP servers",
    "timeline": "Khác",
    "timezone": "Múi giờ",
    "limits": "Giới hạn",
    "loopMax": "Số vòng agent tối đa",
    "hotTailKB": "Dung lượng nhớ nóng (KB)",
    "contextTokens": "Trần ngữ cảnh gửi lên model (tokens)",
    "pricing": "Giá token (USD / 1M)",
    "input": "Input",
    "cache": "Cache",
    "output": "Output",
}

_HINTS: dict[str, str] = {}  # hints removed from the settings panel

# int ranges mirror LimitsCfg.__post_init__ bounds.
_RANGES: dict[str, tuple[int, int]] = {
    "loopMax": (1, 200),
    "hotTailKB": (1, 64),
    "contextTokens": (1000, 2_000_000),
}

def _is_secret(name: str) -> bool:
    # Exact match: apiKeyEnv holds a variable *name*, not a secret.
    return name.lower() == "apikey"


def _field_type(field: dataclasses.Field) -> str:
    if field.type in (str, "str"):
        return "text"
    if field.type in (bool, "bool"):
        return "bool"
    if field.type in (int, float, "int", "float"):
        return "number"
    return "text"


def _field_entry(prefix: str, field: dataclasses.Field) -> dict[str, Any]:
    """Schema entry for one scalar field; key is the dotted config path."""
    entry: dict[str, Any] = {
        "key": f"{prefix}{field.name}",
        "type": _field_type(field),
        "label": _LABELS.get(field.name, field.name),
    }
    if field.default is not dataclasses.MISSING:
        entry["default"] = field.default
    if field.name == "reasoningEffort":
        entry["choices"] = list(REASONING_EFFORTS)
    if field.name in ("timezone", "apiKeyEnv"):
        # timezone follows the host system; apiKeyEnv is plumbing, not user-facing.
        # Both stay in the config file, the panel just skips them.
        entry["hidden"] = True
    if field.name in _RANGES:
        entry["min"], entry["max"] = _RANGES[field.name]
    if _is_secret(field.name):
        entry["secret"] = True
    if field.name in _HINTS:
        entry["hint"] = _HINTS[field.name]
    return entry


def _scalar_section(key: str, cfg: Any) -> dict[str, Any]:
    prefix = f"{key}."
    return {
        "key": key,
        "label": _LABELS.get(key, key),
        "fields": [_field_entry(prefix, f) for f in fields(cfg)],
    }


def _dict_section(key: str) -> dict[str, Any]:
    """mcpServers / pricing are dynamic dict-of-objects: one JSON field."""
    return {
        "key": key,
        "label": _LABELS.get(key, key),
        "fields": [
            {
                "key": key,
                "type": "dict",
                "label": _LABELS.get(key, key),
            }
        ],
    }


def config_schema() -> dict[str, Any]:
    sections: list[dict[str, Any]] = [_scalar_section("provider", ProviderCfg)]
    # Pricing/models render as cards in the UI, not schema sections.
    # Timeline hidden (system default); mcpServers agent-managed.
    sections.append(_scalar_section("limits", LimitsCfg))
    sections.append(_scalar_section("timeline", TimelineCfg))
    return {"sections": sections}
