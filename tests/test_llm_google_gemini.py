"""Unit tests for Google Gemini REST connect (thyca.llms.google_gemini)."""
from __future__ import annotations

import json

import httpx
import pytest

from thyca.configs import ProviderCfg
from thyca.core.protocol import Message, ToolCall
from thyca.llms.google_gemini import (
    GoogleGemini,
    _gemini_stream_url,
    _to_gemini_contents,
    _to_gemini_tools,
    parse_gemini_payload,
)
from thyca.llms.llm_base import LLMError, normalize_usage


def _sse(*chunks: dict) -> httpx.Response:
    parts = [f"data: {json.dumps(chunk)}" for chunk in chunks]
    return httpx.Response(
        200,
        text="\n\n".join(parts) + "\n\n",
        headers={"Content-Type": "text/event-stream"},
    )


def _provider(**overrides) -> ProviderCfg:
    base = {
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "apiKey": "AIza-secret-key",
        "model": "gemini-2.5-flash",
        "api": "google_gemini",
        "reasoningEffort": "high",
    }
    base.update(overrides)
    return ProviderCfg(**base)


def test_gemini_stream_url_normalizes_models_prefix() -> None:
    base = "https://generativelanguage.googleapis.com/v1beta/"
    assert (
        _gemini_stream_url(base, "gemini-2.5-flash")
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
    )
    assert (
        _gemini_stream_url(base, "models/gemini-2.5-flash")
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
    )


def test_to_gemini_tools_uses_parameters_json_schema() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "read file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
        },
        "junk",
    ]
    assert _to_gemini_tools(tools) == [
        {
            "functionDeclarations": [
                {
                    "name": "read",
                    "description": "read file",
                    "parametersJsonSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "additionalProperties": False,
                    },
                }
            ]
        }
    ]


def test_to_gemini_contents_preserves_thought_signatures_and_pairs_tool_names() -> None:
    messages = [
        Message(role="system", content="sys1"),
        Message(role="user", content="check"),
        Message(
            role="assistant",
            content="running",
            reasoning_details=[
                {"type": "gemini.thought", "text": "thinking", "thoughtSignature": "sig-thought"},
                {"type": "gemini.call_signature", "call_id": "fc_1", "thoughtSignature": "sig-fc1"},
                {"type": "gemini.text_signature", "thoughtSignature": "sig-text"},
                {"type": "thinking", "thinking": "foreign", "signature": "anth"},
            ],
            tool_calls=[
                ToolCall(id="fc_1", name="bash", arguments={"cmd": "pwd"}),
                ToolCall(id="gemini_call_1", name="read", arguments={"path": "a"}),
            ],
        ),
        Message(role="tool", content="/tmp", tool_call_id="fc_1"),
        Message(role="tool", content="denied", tool_call_id="gemini_call_1", meta={"is_error": True}),
    ]
    sys_inst, contents = _to_gemini_contents(messages)
    assert sys_inst == {"parts": [{"text": "sys1"}]}
    assert contents == [
        {"role": "user", "parts": [{"text": "check"}]},
        {
            "role": "model",
            "parts": [
                {"thought": True, "text": "thinking", "thoughtSignature": "sig-thought"},
                {"text": "running", "thoughtSignature": "sig-text"},
                {
                    "functionCall": {"name": "bash", "args": {"cmd": "pwd"}, "id": "fc_1"},
                    "thoughtSignature": "sig-fc1",
                },
                {"functionCall": {"name": "read", "args": {"path": "a"}}},
            ],
        },
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": "bash", "response": {"output": "/tmp"}, "id": "fc_1"}},
                {"functionResponse": {"name": "read", "response": {"error": "denied"}}},
            ],
        },
    ]


def test_normalize_usage_google() -> None:
    got = normalize_usage(
        {
            "promptTokenCount": 120,
            "cachedContentTokenCount": 50,
            "candidatesTokenCount": 30,
            "thoughtsTokenCount": 70,
            "totalTokenCount": 220,
        },
        "google_gemini",
    )
    assert got == {
        "prompt_tokens": 120,
        "cached_tokens": 50,
        "completion_tokens": 100,
        "total_tokens": 220,
        "reasoning_tokens": 70,
    }


@pytest.mark.asyncio
async def test_stream_full_turn_thoughts_signatures_calls_and_usage() -> None:
    seen: list[tuple[str, dict[str, str], dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (str(request.url), dict(request.headers), json.loads(request.content.decode("utf-8")))
        )
        return _sse(
            {
                "modelVersion": "gemini-2.5-flash-001",
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "thought": True,
                                    "text": "plan AIza-secret-key",
                                    "thoughtSignature": "sig-t",
                                }
                            ],
                        }
                    }
                ],
            },
            {
                "modelVersion": "gemini-2.5-flash-001",
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "Answer "},
                                {
                                    "functionCall": {"id": "fc_9", "name": "bash", "args": {"cmd": "id"}},
                                    "thoughtSignature": "sig-call",
                                },
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 4,
                    "thoughtsTokenCount": 6,
                    "totalTokenCount": 20,
                },
            },
        )

    thoughts: list[str] = []
    texts: list[str] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connect = GoogleGemini(_provider(), client=client)
    reply = await connect.chat(
        [Message(role="system", content="be brief"), Message(role="user", content="hi")],
        tools=[{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}],
        on_reasoning=thoughts.append,
        on_content=texts.append,
    )

    url, headers, body = seen[0]
    assert url.endswith("/models/gemini-2.5-flash:streamGenerateContent?alt=sse")
    assert "AIza-secret-key" not in url
    assert headers["x-goog-api-key"] == "AIza-secret-key"
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert body["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingLevel": "HIGH",
    }

    assert reply.content == "Answer "
    assert "".join(texts) == "Answer "
    assert reply.reasoning == "plan [redacted]"
    assert "[redacted]" in "".join(thoughts)
    assert reply.tool_calls == [ToolCall(id="fc_9", name="bash", arguments={"cmd": "id"})]
    assert reply.reasoning_details == [
        {"type": "gemini.thought", "thoughtSignature": "sig-t", "text": "plan [redacted]"},
        {"type": "gemini.call_signature", "call_id": "fc_9", "thoughtSignature": "sig-call"},
    ]
    assert reply.finish_reason == "STOP"
    assert reply.model == "gemini-2.5-flash-001"
    assert reply.usage == {
        "prompt_tokens": 10,
        "cached_tokens": 0,
        "completion_tokens": 10,
        "total_tokens": 20,
        "reasoning_tokens": 6,
    }


@pytest.mark.asyncio
async def test_two_step_400_fallback_drops_thinking_level_then_thinking_config() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                400,
                json={"error": {"message": "Unknown name \"thinkingLevel\" at 'generation_config.thinking_config'"}},
            )
        if len(bodies) == 2:
            return httpx.Response(
                400,
                json={"error": {"message": "Thinking is not supported by this model (thinkingConfig)"}},
            )
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-2.0-flash",
                "candidates": [{"content": {"parts": [{"text": "pong"}]}, "finishReason": "STOP"}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connect = GoogleGemini(_provider(model="gemini-2.0-flash", reasoningEffort="xhigh"), client=client)
    reply = await connect.chat([Message(role="user", content="ping")])
    assert len(bodies) == 3
    assert bodies[0]["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingLevel": "HIGH",
    }
    assert bodies[1]["generationConfig"]["thinkingConfig"] == {"includeThoughts": True}
    assert "thinkingConfig" not in bodies[2]["generationConfig"]
    assert reply.content == "pong"


def test_parse_non_streaming_block_and_error() -> None:
    with pytest.raises(LLMError, match="blocked \\(SAFETY\\)"):
        parse_gemini_payload({"promptFeedback": {"blockReason": "SAFETY"}}, "AIza-secret-key")
    with pytest.raises(LLMError, match=r"provider error:.*\[redacted\]"):
        parse_gemini_payload({"error": {"message": "bad AIza-secret-key"}}, "AIza-secret-key")


@pytest.mark.asyncio
async def test_synthetic_function_call_ids_are_unique_across_rounds() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-2.5-flash",
                "candidates": [
                    {
                        "content": {
                            "parts": [{"functionCall": {"name": "bash", "args": {"cmd": "pwd"}}}]
                        },
                        "finishReason": "STOP",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connect = GoogleGemini(_provider(), client=client)
    first = await connect.chat([Message(role="user", content="round 1")])
    second = await connect.chat([Message(role="user", content="round 2")])
    assert len(first.tool_calls) == 1 and len(second.tool_calls) == 1
    assert first.tool_calls[0].id.startswith("gemini_call_")
    assert second.tool_calls[0].id.startswith("gemini_call_")
    assert first.tool_calls[0].id != second.tool_calls[0].id


@pytest.mark.asyncio
async def test_incomplete_sse_without_finish_reason_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _sse(
            {
                "modelVersion": "gemini-2.5-flash",
                "candidates": [{"content": {"parts": [{"text": "partial"}]}}],
            }
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connect = GoogleGemini(_provider(), client=client)
    with pytest.raises(LLMError, match="incomplete"):
        await connect.chat([Message(role="user", content="hi")])
