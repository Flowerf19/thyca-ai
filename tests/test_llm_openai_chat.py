from __future__ import annotations

import json

import httpx
import pytest

from thyca.config import ProviderCfg
from thyca.llm.llm_base import LLMError, normalize_usage
from thyca.llm.openai_chat import OpenAIChat, _chat_url
from thyca.protocol import Message, ToolCall


def _provider() -> ProviderCfg:
    return ProviderCfg(
        baseUrl="https://api.example.com/v1",
        model="demo-model",
        apiKey="sk-secret-key",
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_chat_url_does_not_double_slash() -> None:
    assert _chat_url("https://api.example.com/v1/") == "https://api.example.com/v1/chat/completions"
    assert _chat_url("https://api.example.com/v1") == "https://api.example.com/v1/chat/completions"


@pytest.mark.asyncio
async def test_text_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer sk-secret-key"
        body = json.loads(request.content)
        assert body["model"] == "demo-model"
        assert body["messages"][0]["content"] == "ping"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}
                ],
                "usage": {"total_tokens": 3},
            },
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="ping")])
    assert reply.content == "pong"
    assert reply.tool_calls == []
    assert reply.finish_reason == "stop"
    assert reply.usage == {"total_tokens": 3}


@pytest.mark.asyncio
async def test_usage_cached_and_reasoning_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini-2024-07-18",
                "choices": [
                    {"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                    "prompt_tokens_details": {"cached_tokens": 20},
                    "completion_tokens_details": {"reasoning_tokens": 4},
                },
            },
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="ping")])
    assert reply.model == "gpt-4o-mini-2024-07-18"
    assert reply.usage == {
        "prompt_tokens": 100,
        "cached_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 110,
        "reasoning_tokens": 4,
    }


def test_normalize_usage_unknown_shape_is_none() -> None:
    assert normalize_usage(None, "openai") is None
    assert normalize_usage({}, "openai") is None
    assert normalize_usage({"foo": 1}, "openai") is None


def test_normalize_usage_anthropic_and_google_stubs() -> None:
    import asyncio

    # offline stub coverage cho connect chưa implement (TASK-003)
    anthropic = normalize_usage(
        {
            "input_tokens": 10,
            "output_tokens": 3,
            "cache_read_input_tokens": 4,
            "cache_creation_input_tokens": 1,
            "total_tokens": 13,
        },
        "anthropic",
    )
    assert anthropic == {
        "prompt_tokens": 10,
        "cached_tokens": 4,
        "completion_tokens": 3,
        "total_tokens": 13,
    }

    google = normalize_usage(
        {
            "promptTokenCount": 20,
            "candidatesTokenCount": 5,
            "cachedContentTokenCount": 8,
            "totalTokenCount": 25,
        },
        "google",
    )
    assert google == {
        "prompt_tokens": 20,
        "cached_tokens": 8,
        "completion_tokens": 5,
        "total_tokens": 25,
    }
    # connect thật vẫn NotImplementedError tới khi có key
    import asyncio

    import pytest

    from thyca.llm.anthropic_chat import AnthropicChat
    from thyca.llm.google_chat import GoogleChat

    with pytest.raises(NotImplementedError):
        asyncio.run(GoogleChat().chat([]))
    with pytest.raises(NotImplementedError):
        asyncio.run(AnthropicChat().chat([]))


@pytest.mark.asyncio
async def test_null_content_tool_call_and_bad_arguments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": "{not-json"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="use tool")])
    assert reply.content is None
    assert len(reply.tool_calls) == 1
    call = reply.tool_calls[0]
    assert call.id == "call-1"
    assert call.name == "echo"
    assert call.arguments == {}
    assert call.parse_error == "invalid arguments"


@pytest.mark.asyncio
async def test_empty_choices_is_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    connect = OpenAIChat(_provider(), client=_client(handler))
    with pytest.raises(LLMError, match="missing choices"):
        await connect.chat([Message(role="user", content="x")])


@pytest.mark.asyncio
async def test_401_does_not_retry_and_redacts_key() -> None:
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(401, text="denied sk-secret-key")

    connect = OpenAIChat(_provider(), client=_client(handler))
    with pytest.raises(LLMError) as err:
        await connect.chat([Message(role="user", content="x")])
    assert hits["n"] == 1
    assert "sk-secret-key" not in str(err.value)
    assert "[redacted]" in str(err.value)


@pytest.mark.asyncio
async def test_429_retries_once() -> None:
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert hits["n"] == 2
    assert reply.content == "ok"


@pytest.mark.asyncio
async def test_timeout_retries_once_then_errors() -> None:
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        raise httpx.ReadTimeout("read timed out")

    connect = OpenAIChat(_provider(), client=_client(handler))
    with pytest.raises(LLMError, match="timeout"):
        await connect.chat([Message(role="user", content="x")])
    assert hits["n"] == 2


@pytest.mark.asyncio
async def test_assistant_tool_roundtrip_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    call = ToolCall(id="c1", name="echo", arguments={"q": "hi"})
    await connect.chat(
        [
            Message(role="user", content="go"),
            Message(role="assistant", content=None, tool_calls=[call]),
            Message(role="tool", content="hi", tool_call_id="c1"),
        ]
    )
    messages = seen["body"]["messages"]
    assert messages[1]["tool_calls"][0]["function"]["name"] == "echo"
    assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {"q": "hi"}
    assert messages[2]["tool_call_id"] == "c1"


@pytest.mark.asyncio
async def test_reasoning_effort_sent_by_default() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    await connect.chat([Message(role="user", content="x")])
    assert seen["body"]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_reasoning_effort_dropped_on_400_and_retried() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(
                400,
                json={"error": {"message": "reasoning_effort does not support 'high' with this model"}},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.content == "ok"
    assert len(bodies) == 2
    assert "reasoning_effort" in bodies[0]
    assert "reasoning_effort" not in bodies[1]


@pytest.mark.asyncio
async def test_reasoning_effort_400_without_marker_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    connect = OpenAIChat(_provider(), client=_client(handler))
    with pytest.raises(LLMError, match="400"):
        await connect.chat([Message(role="user", content="x")])


@pytest.mark.asyncio
async def test_reasoning_effort_low_sent() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(
        ProviderCfg(
            baseUrl="https://api.example.com/v1",
            model="demo-model",
            apiKey="sk-secret-key",
            reasoningEffort="low",
        ),
        client=_client(handler),
    )
    await connect.chat([Message(role="user", content="x")])
    assert seen["body"]["reasoning_effort"] == "low"
