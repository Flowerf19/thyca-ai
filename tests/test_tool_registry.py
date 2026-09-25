from __future__ import annotations

import pytest

from thyca.tools.registry import ToolRegistry, ToolSpec


def _echo_spec(**overrides) -> ToolSpec:
    async def echo(args: dict) -> str:
        return str(args.get("text", ""))

    fields = {
        "name": "echo",
        "description": "echo text",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "handler": echo,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def test_openai_schema_shape() -> None:
    registry = ToolRegistry()
    registry.register(_echo_spec())
    schema = registry.to_openai_schema()
    assert schema == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "echo text",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def test_duplicate_register_raises() -> None:
    registry = ToolRegistry()
    registry.register(_echo_spec())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_echo_spec())


def test_get_returns_spec_or_none() -> None:
    registry = ToolRegistry()
    assert registry.get("echo") is None
    spec = _echo_spec()
    registry.register(spec)
    assert registry.get("echo") is spec


def test_validate_args_missing_and_extra() -> None:
    registry = ToolRegistry()
    spec = _echo_spec()
    registry.register(spec)
    assert registry.validate_args(spec, {"text": "hi"}) is None
    assert registry.validate_args(spec, {}) == "missing argument: text"
    assert registry.validate_args(spec, {"text": "x", "bonus": 1}).startswith(
        "unexpected argument"
    )


def test_spec_rejects_empty_name_description_and_bad_parameters() -> None:
    with pytest.raises(ValueError, match="ToolSpec.name"):
        _echo_spec(name="")
    with pytest.raises(ValueError, match="ToolSpec.description"):
        _echo_spec(description="")
    with pytest.raises(ValueError, match="ToolSpec.parameters"):
        _echo_spec(parameters=[])


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
def _strict_spec(**overrides):
    from thyca.tools.registry import ToolSpec

    async def handler(args: dict) -> str:
        return "ok"

    fields = {
        "name": "probe",
        "description": "probe",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "flag": {"type": "boolean"},
                "tags": {"type": "array"},
                "meta": {"type": "object"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "handler": handler,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def test_x3_registry_rejects_mistyped_args() -> None:
    from thyca.tools.registry import ToolRegistry

    registry = ToolRegistry()
    spec = _strict_spec()
    registry.register(spec)
    assert registry.validate_args(spec, {"name": "x"}) is None
    assert registry.validate_args(spec, {"name": "x", "count": 5.0}) is None
    assert "missing argument" in (registry.validate_args(spec, {}) or "")
    assert "unexpected argument" in (
        registry.validate_args(spec, {"name": "x", "bonus": 1}) or ""
    )
    assert registry.validate_args(spec, {"name": 123}) == (
        "argument 'name' must be string, got integer"
    )
    assert registry.validate_args(spec, {"name": "x", "count": True}) == (
        "argument 'count' must be integer, got boolean"
    )
    assert registry.validate_args(spec, {"name": "x", "count": 2.5}) == (
        "argument 'count' must be integer, got number"
    )
    assert registry.validate_args(spec, {"name": "x", "flag": 1}) == (
        "argument 'flag' must be boolean, got integer"
    )
    assert registry.validate_args(spec, {"name": None}) == (
        "argument 'name' must be string, got null"
    )


def test_x3_untyped_and_unknown_types_skip() -> None:
    from thyca.tools.registry import ToolRegistry

    registry = ToolRegistry()
    spec = _strict_spec(
        parameters={
            "type": "object",
            "properties": {"loose": {}, "weird": {"type": "mystery"}},
            "additionalProperties": False,
        }
    )
    registry.register(spec)
    assert registry.validate_args(spec, {"loose": 123, "weird": [1]}) is None


def test_x3_malformed_type_shape_skips() -> None:
    from thyca.tools.registry import ToolRegistry

    registry = ToolRegistry()
    spec = _strict_spec(
        parameters={
            "type": "object",
            "properties": {"odd": {"type": 123}, "mixed": {"type": ["string", 456]}},
            "additionalProperties": False,
        }
    )
    registry.register(spec)
    assert registry.validate_args(spec, {"odd": object(), "mixed": "x"}) is None
    assert registry.validate_args(spec, {"mixed": 5}) == (
        "argument 'mixed' must be string, got integer"
    )
