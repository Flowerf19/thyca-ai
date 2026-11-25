"""Unit tests for Anthropic Messages API connect (thyca.llms.anthropic_messages)."""
from __future__ import annotations

import json

import httpx
import pytest

from thyca.configs import ProviderCfg
from thyca.core.protocol import Message, ToolCall
from thyca.llms.anthropic_messages import (
    ANTHROPIC_VERSION,
    AnthropicMessages,
    _messages_url,
    _to_anthropic_messages,
    _to_anthropic_tools,
    parse_anthropic_payload,
)
from thyca.llms.llm_base import LLMError, normalize_usage


def _sse(*events: dict) -> httpx.Response:
    parts = [f"event: {ev.get('type', 'message')}\ndata: {json.dumps(ev)}" for ev in events]
    return httpx.Response(
        200,
        text="\n\n".join(parts) + "\n\n",
        headers={"Content-Type": "text/event-stream"},
    )


def _provider(**overrides) -> ProviderCfg:
    base = {
        "baseUrl": "https://api.anthropic.com/v1",
        "apiKey": "sk-ant-secret",
        "model": "claude-sonnet-4-6",
        "api": "anthropic_messages",
        "reasoningEffort": "high",
    }
    base.update(overrides)
    return ProviderCfg(**base)


def test_messages_url() -> None:
    assert _messages_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/messages"
    assert _messages_url("https://api.anthropic.com/v1/") == "https://api.anthropic.com/v1/messages"


def test_to_anthropic_tools_converts_chat_and_flat_specs() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "run shell",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
            },
        },
        {"name": "ping", "parameters": {"properties": {}}},
        "junk",
        {"function": {}},
    ]
    assert _to_anthropic_tools(tools) == [
        {
            "name": "bash",
            "description": "run shell",
            "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        },
        {
            "name": "ping",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]


def test_to_anthropic_messages_splits_system_merges_tools_and_preserves_thinking() -> None:
    messages = [
        Message(role="system", content="sys1"),
        Message(role="system", content="sys2"),
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content="checking",
            reasoning_details=[
                {"type": "thinking", "thinking": "plan", "signature": "sig-1"},
                {"type": "redacted_thinking", "data": "enc-1"},
                {"type": "reasoning", "id": "rs_foreign", "summary": []},
                {"type": "gemini.thought", "thoughtSignature": "g-sig"},
            ],
            tool_calls=[
                ToolCall(id="tu_1", name="read", arguments={"path": "a.txt"}),
                ToolCall(id="tu_2", name="read", arguments={"path": "b.txt"}),
            ],
        ),
        Message(role="tool", content="A", tool_call_id="tu_1"),
        Message(role="tool", content="boom", tool_call_id="tu_2", meta={"is_error": True}),
        Message(role="user", content="follow up"),
    ]
    system, wire = _to_anthropic_messages(messages)
    assert system == "sys1\n\nsys2"
    assert wire == [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "plan", "signature": "sig-1"},
                {"type": "redacted_thinking", "data": "enc-1"},
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "tu_1", "name": "read", "input": {"path": "a.txt"}},
                {"type": "tool_use", "id": "tu_2", "name": "read", "input": {"path": "b.txt"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "A"},
                {"type": "tool_result", "tool_use_id": "tu_2", "content": "boom", "is_error": True},
                {"type": "text", "text": "follow up"},
            ],
        },
    ]


def test_normalize_usage_anthropic() -> None:
    got = normalize_usage(
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 80,
            "output_tokens": 50,
            "output_tokens_details": {"thinking_tokens": 30},
        },
        "anthropic_messages",
    )
    assert got == {
        "prompt_tokens": 200,
        "cached_tokens": 80,
        "completion_tokens": 50,
        "total_tokens": 250,
        "reasoning_tokens": 30,
    }


@pytest.mark.asyncio
async def test_stream_full_turn_headers_thinking_tools_and_usage() -> None:
    seen_requests: list[tuple[dict[str, str], dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((dict(request.headers), json.loads(request.content.decode("utf-8"))))
        return _sse(
            {"type": "ping"},
            {
                "type": "message_start",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 60, "cache_read_input_tokens": 40},
                },
            },
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "step 1 "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "sk-ant-secret"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig-abc"},
            },
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "Hi "},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "there"},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "tool_use", "id": "tu_9", "name": "bash"},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '{"cmd":'},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '"ls"}'},
            },
            {"type": "content_block_stop", "index": 2},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 25},
            },
            {"type": "message_stop"},
        )

    thought_chunks: list[str] = []
    text_chunks: list[str] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connect = AnthropicMessages(_provider(), client=client)
    reply = await connect.chat(
        [Message(role="system", content="sys"), Message(role="user", content="go")],
        tools=[{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}],
        on_reasoning=thought_chunks.append,
        on_content=text_chunks.append,
    )

    headers, body = seen_requests[0]
    assert headers["x-api-key"] == "sk-ant-secret"
    assert headers["anthropic-version"] == ANTHROPIC_VERSION
    assert body["system"] == "sys"
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "high"}
    assert body["max_tokens"] == 16384
    assert body["tools"] == [{"name": "bash", "input_schema": {"type": "object"}}]

    assert reply.content == "Hi there"
    assert "".join(text_chunks) == "Hi there"
    assert reply.reasoning == "step 1 [redacted]"
    assert "[redacted]" in "".join(thought_chunks)
    assert reply.reasoning_details == [
        {"type": "thinking", "thinking": "step 1 [redacted]", "signature": "sig-abc"}
    ]
    assert reply.tool_calls == [ToolCall(id="tu_9", name="bash", arguments={"cmd": "ls"})]
    assert reply.finish_reason == "tool_calls"
    assert reply.model == "claude-sonnet-4-6"
    assert reply.usage == {
        "prompt_tokens": 100,
        "cached_tokens": 40,
        "completion_tokens": 25,
        "total_tokens": 125,
    }


@pytest.mark.asyncio
async def test_drops_thinking_and_output_config_on_400() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                400,
                json={"type": "error", "error": {"message": "output_config is not supported"}},
            )
        return httpx.Response(
            200,
            json={
                "model": "claude-3-haiku",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connect = AnthropicMessages(_provider(model="claude-3-haiku"), client=client)
    reply = await connect.chat([Message(role="user", content="ping")])
    assert len(bodies) == 2
    assert "thinking" in bodies[0] and "output_config" in bodies[0]
    assert "thinking" not in bodies[1] and "output_config" not in bodies[1]
    assert reply.content == "ok"


def test_parse_non_streaming_and_errors() -> None:
    reply = parse_anthropic_payload(
        {
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "content": [
                {"type": "redacted_thinking", "data": "cipher"},
                {"type": "text", "text": "done"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 3},
        },
        "sk-ant-secret",
    )
    assert reply.content == "done"
    assert reply.reasoning_details == [{"type": "redacted_thinking", "data": "cipher"}]

    with pytest.raises(LLMError, match=r"provider error:.*\[redacted\]"):
        parse_anthropic_payload(
            {"type": "error", "error": {"message": "bad sk-ant-secret"}},
            "sk-ant-secret",
        )


@pytest.mark.asyncio
async def test_incomplete_sse_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _sse({"type": "message_start", "message": {"model": "claude-sonnet-4-6"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connect = AnthropicMessages(_provider(), client=client)
    with pytest.raises(LLMError, match="incomplete"):
        await connect.chat([Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_message_delta_null_usage_fields_preserve_message_start_counters() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _sse(
            {
                "type": "message_start",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 80, "cache_read_input_tokens": 20},
                },
            },
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {
                    "input_tokens": None,
                    "cache_creation_input_tokens": None,
                    "cache_read_input_tokens": None,
                    "output_tokens": 12,
                },
            },
            {"type": "message_stop"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connect = AnthropicMessages(_provider(), client=client)
    reply = await connect.chat([Message(role="user", content="hi")])
    assert reply.usage == {
        "prompt_tokens": 100,
        "cached_tokens": 20,
        "completion_tokens": 12,
        "total_tokens": 112,
    }
    assert reply.finish_reason == "stop"


def test_refusal_and_empty_max_tokens_raise_while_partial_max_tokens_normalizes() -> None:
    with pytest.raises(LLMError, match="refusal"):
        parse_anthropic_payload(
            {"model": "claude-sonnet-4-6", "stop_reason": "refusal", "content": []},
            "sk-ant-secret",
        )
    with pytest.raises(LLMError, match="max_tokens"):
        parse_anthropic_payload(
            {"model": "claude-sonnet-4-6", "stop_reason": "max_tokens", "content": []},
            "sk-ant-secret",
        )
    partial = parse_anthropic_payload(
        {
            "model": "claude-sonnet-4-6",
            "stop_reason": "max_tokens",
            "content": [{"type": "text", "text": "cut off"}],
        },
        "sk-ant-secret",
    )
    assert partial.content == "cut off"
    assert partial.finish_reason == "length"
