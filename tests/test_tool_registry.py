from __future__ import annotations

import asyncio

import pytest

from thyca.protocol import ToolCall, ToolResult
from thyca.tools.registry import ToolRegistry, ToolSpec


def _echo_spec(**overrides) -> ToolSpec:
    async def echo(args: dict) -> str:
        return str(args.get("text", ""))

    fields = dict(
        name="echo",
        description="echo text",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=echo,
    )
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


@pytest.mark.asyncio
async def test_dispatch_keeps_call_id_and_name() -> None:
    registry = ToolRegistry()
    registry.register(_echo_spec())
    result = await registry.dispatch(ToolCall(id="c1", name="echo", arguments={"text": "hi"}))
    assert result == ToolResult(tool_call_id="c1", name="echo", content="hi", is_error=False)


@pytest.mark.asyncio
async def test_unknown_missing_extra_and_parse_error_do_not_run_handler() -> None:
    hits = {"n": 0}

    async def echo(args: dict) -> str:
        hits["n"] += 1
        return "ran"

    registry = ToolRegistry()
    registry.register(_echo_spec(handler=echo))
    unknown = await registry.dispatch(ToolCall(id="u", name="nope", arguments={"text": "x"}))
    missing = await registry.dispatch(ToolCall(id="m", name="echo", arguments={}))
    extra = await registry.dispatch(
        ToolCall(id="e", name="echo", arguments={"text": "x", "bonus": 1})
    )
    parsed = await registry.dispatch(
        ToolCall(id="p", name="echo", arguments={"text": "x"}, parse_error="bad json")
    )
    assert hits["n"] == 0
    assert unknown.is_error and unknown.tool_call_id == "u"
    assert missing.content == "missing argument: text"
    assert extra.content.startswith("unexpected argument")
    assert parsed.content == "bad json"


@pytest.mark.asyncio
async def test_handler_exception_and_result_cap() -> None:
    async def boom(args: dict) -> str:
        raise RuntimeError("failed")

    async def huge(args: dict) -> str:
        return "á" * 40_000

    registry = ToolRegistry(result_cap=20)
    registry.register(_echo_spec(name="boom", handler=boom))
    registry.register(_echo_spec(name="huge", handler=huge))
    err = await registry.dispatch(ToolCall(id="b", name="boom", arguments={"text": "x"}))
    big = await registry.dispatch(ToolCall(id="h", name="huge", arguments={"text": "x"}))
    assert err.is_error and err.content == "failed" and err.tool_call_id == "b"
    assert not big.is_error
    assert len(big.content.encode("utf-8")) <= 20


@pytest.mark.asyncio
async def test_same_resource_serializes_different_keys_overlap() -> None:
    events: list[str] = []

    async def work(args: dict) -> str:
        events.append(f"start-{args['text']}")
        await asyncio.sleep(0.03)
        events.append(f"end-{args['text']}")
        return args["text"]

    registry = ToolRegistry()
    registry.register(
        _echo_spec(handler=work, resource_key=lambda args: args["text"])
    )
    same = await asyncio.gather(
        registry.dispatch(ToolCall(id="a", name="echo", arguments={"text": "k"})),
        registry.dispatch(ToolCall(id="b", name="echo", arguments={"text": "k"})),
    )
    assert [r.content for r in same] == ["k", "k"]
    assert events[:4] == ["start-k", "end-k", "start-k", "end-k"]

    events.clear()
    await asyncio.gather(
        registry.dispatch(ToolCall(id="c", name="echo", arguments={"text": "one"})),
        registry.dispatch(ToolCall(id="d", name="echo", arguments={"text": "two"})),
    )
    assert events[0].startswith("start-") and events[1].startswith("start-")
