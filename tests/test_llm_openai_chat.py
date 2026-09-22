from __future__ import annotations

import json

import httpx
import pytest

from thyca.config import Config, ModelCfg, ProviderCfg, ProviderEntry
from thyca.llm.llm_base import LLMError, normalize_usage
from thyca.llm.openai_chat import OpenAIChat, _chat_url
from thyca.core.protocol import Message, ToolCall


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
async def test_effective_model_endpoint_is_used_for_request() -> None:
    cfg = Config(
        providers={"default": ProviderEntry(apiKey="sk-secret-key")},
        defaultModel="special",
        models={"special": ModelCfg(baseUrl="https://other.example/v1")},
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={"model": "special", "choices": [{"message": {"content": "ok"}}]},
        )

    connect = OpenAIChat(cfg.effective_provider(), client=_client(handler))
    await connect.chat([Message(role="user", content="x")])

    assert seen == ["https://other.example/v1/chat/completions"]


@pytest.mark.asyncio
async def test_default_client_uses_five_minute_read_timeout() -> None:
    connect = OpenAIChat(_provider())
    try:
        assert connect._client.timeout.read == 300.0
    finally:
        await connect.aclose()


@pytest.mark.asyncio
async def test_text_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer sk-secret-key"
        body = json.loads(request.content)
        assert body["model"] == "demo-model"
        assert body["messages"][0]["content"] == "ping"
        assert "tools" not in body
        assert "tool_choice" not in body
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
async def test_tools_sent_without_tool_choice() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    tools = [{"type": "function", "function": {"name": "echo"}}]
    await connect.chat([Message(role="user", content="x")], tools)
    assert seen["body"]["tools"] == tools
    assert "tool_choice" not in seen["body"]


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


def test_normalize_usage_anthropic_and_google() -> None:
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
async def test_429_retries_up_to_three_attempts() -> None:
    hits = {"n": 0}
    retries: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    connect.set_retry_hook(lambda a, m: retries.append((a, m)))
    reply = await connect.chat([Message(role="user", content="x")])
    assert hits["n"] == 3
    assert reply.content == "ok"
    assert retries == [(1, 3), (2, 3)]


@pytest.mark.asyncio
async def test_timeout_retries_three_times_then_errors() -> None:
    hits = {"n": 0}
    retries: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        raise httpx.ReadTimeout("read timed out")

    connect = OpenAIChat(_provider(), client=_client(handler))
    connect.set_retry_hook(lambda a, m: retries.append((a, m)))
    with pytest.raises(LLMError, match="timeout"):
        await connect.chat([Message(role="user", content="x")])
    assert hits["n"] == 3
    assert retries == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.asyncio
async def test_connect_error_retries_three_times_then_errors() -> None:
    hits = {"n": 0}
    retries: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        raise httpx.ConnectError("connection reset")

    connect = OpenAIChat(_provider(), client=_client(handler))
    connect.set_retry_hook(lambda a, m: retries.append((a, m)))
    with pytest.raises(LLMError, match="connection reset"):
        await connect.chat([Message(role="user", content="x")])
    assert hits["n"] == 3
    assert retries == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.asyncio
async def test_500_retries_up_to_three_attempts() -> None:
    hits = {"n": 0}
    retries: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] < 3:
            return httpx.Response(500, headers={"Retry-After": "0"}, text="boom")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    connect.set_retry_hook(lambda a, m: retries.append((a, m)))
    reply = await connect.chat([Message(role="user", content="x")])
    assert hits["n"] == 3
    assert reply.content == "ok"
    assert retries == [(1, 3), (2, 3)]


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


def _sse(*chunks: dict, done: bool = True) -> httpx.Response:
    parts = [f"data: {json.dumps(chunk)}" for chunk in chunks]
    if done:
        parts.append("data: [DONE]")
    return httpx.Response(
        200,
        text="\n\n".join(parts) + "\n\n",
        headers={"Content-Type": "text/event-stream"},
    )


@pytest.mark.asyncio
async def test_stream_reasoning_content_emits_and_persists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        return _sse(
            {"choices": [{"delta": {"reasoning_content": "Think about "}}]},
            {"choices": [{"delta": {"reasoning_content": "the chords."}}]},
            {
                "choices": [{"delta": {"content": "yes"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 4},
            },
        )

    seen: list[str] = []
    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")], on_reasoning=seen.append)
    assert reply.content == "yes"
    assert reply.reasoning == "Think about the chords."
    assert "".join(seen) == "Think about the chords."
    assert reply.usage == {"total_tokens": 4}


@pytest.mark.asyncio
async def test_stream_reasoning_field_openrouter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"choices": [{"delta": {"reasoning": "because"}}]},
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.reasoning == "because"
    assert reply.content == "ok"


@pytest.mark.asyncio
async def test_stream_content_deltas_forwarded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"choices": [{"delta": {"content": "Xin "}}]},
            {"choices": [{"delta": {"content": "chào"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "bash", "arguments": "{}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        )

    seen: list[str] = []
    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")], on_content=seen.append)
    assert "".join(seen) == "Xin chào"
    assert reply.content == "Xin chào"
    assert reply.finish_reason == "tool_calls"
    assert [call.name for call in reply.tool_calls] == ["bash"]


@pytest.mark.asyncio
async def test_stream_reasoning_text_field_parsed() -> None:
    """Some providers send reasoning as reasoning_text (pi reads it too)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"choices": [{"delta": {"reasoning_text": "step one "}}]},
            {"choices": [{"delta": {"reasoning_text": "step two"}}]},
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    seen: list[str] = []
    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")], on_reasoning=seen.append)
    assert reply.reasoning == "step one step two"
    assert "".join(seen) == "step one step two"
    assert reply.content == "ok"


@pytest.mark.asyncio
async def test_json_reasoning_content_parsed_without_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "pong",
                            "reasoning_content": "I thought",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    seen: list[str] = []
    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")], on_reasoning=seen.append)
    assert reply.reasoning == "I thought"
    assert seen == ["I thought"]


@pytest.mark.asyncio
async def test_reasoning_key_redacted_in_delta() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"choices": [{"delta": {"reasoning_content": "use sk-secret-key now"}}]},
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    seen: list[str] = []
    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")], on_reasoning=seen.append)
    assert "sk-secret-key" not in (reply.reasoning or "")
    assert "[redacted]" in (reply.reasoning or "")
    assert all("sk-secret-key" not in item for item in seen)


@pytest.mark.asyncio
async def test_reasoning_capped_at_result_limit() -> None:
    from thyca.core.protocol import RESULT_CAP_BYTES

    huge = "a" * (RESULT_CAP_BYTES + 50)

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"choices": [{"delta": {"reasoning_content": huge}}]},
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.reasoning is not None
    assert len(reply.reasoning.encode("utf-8")) <= RESULT_CAP_BYTES


@pytest.mark.asyncio
async def test_stream_tool_calls_assembled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {"name": "echo", "arguments": ""},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '{"q":"hi"}'}}
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.content is None
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].id == "call-1"
    assert reply.tool_calls[0].name == "echo"
    assert reply.tool_calls[0].arguments == {"q": "hi"}


@pytest.mark.asyncio
async def test_stream_reasoning_details_merged_and_invalid_ignored() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"choices": [{"delta": {"reasoning_details": [
                {"type": "reasoning.text", "text": "unga ", "id": "d1"},
                {"type": "bogus", "text": "nope"},
                "not-a-dict",
            ]}}]},
            {"choices": [{"delta": {"reasoning_details": [
                {"type": "reasoning.text", "text": "bunga", "signature": "sig1"},
            ]}}]},
            {"choices": [{"delta": {"reasoning_details": [
                {"type": "reasoning.summary", "summary": "did stuff"},
            ], "content": "ok"}, "finish_reason": "stop"}]},
            {"choices": [{"delta": {"reasoning_details": "not-a-list"}}]},
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.reasoning_details == [
        {"type": "reasoning.text", "text": "unga bunga", "signature": "sig1", "id": "d1"},
        {"type": "reasoning.summary", "summary": "did stuff"},
    ]


@pytest.mark.asyncio
async def test_nonstream_reasoning_details_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "demo-model",
                "choices": [{
                    "message": {
                        "content": "ok",
                        "reasoning_details": [
                            {"type": "reasoning.encrypted", "data": "blob", "index": 2},
                            {"type": "reasoning.text", "text": ""},
                        ],
                    },
                    "finish_reason": "stop",
                }],
            },
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.reasoning_details == [
        {"type": "reasoning.encrypted", "data": "blob", "index": 2}
    ]


@pytest.mark.asyncio
async def test_no_reasoning_details_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"model": "m", "choices": [{"message": {"content": "ok"}}]}
        )

    connect = OpenAIChat(_provider(), client=_client(handler))
    assert (await connect.chat([Message(role="user", content="x")])).reasoning_details is None


def test_to_openai_message_roundtrips_reasoning_details() -> None:
    from thyca.llm.openai_chat import _to_openai_message

    details = [{"type": "reasoning.text", "text": "t", "signature": "s"}]
    payload = _to_openai_message(
        Message(role="assistant", content="hi", reasoning_details=details)
    )
    assert payload["reasoning_details"] == details
    assert payload["reasoning_details"] is not details
    plain = _to_openai_message(Message(role="assistant", content="hi"))
    assert "reasoning_details" not in plain
    assert "reasoning_details" not in _to_openai_message(
        Message(role="user", content="hi")
    )
