from __future__ import annotations

import json

import httpx
import pytest

from thyca.config import ProviderCfg
from thyca.llm.llm_base import LLMError, normalize_usage
from thyca.llm.openai_responses import OpenAIResponses, _responses_url
from thyca.llm.responses_parse import _to_responses_input, _to_responses_tools
from thyca.core.protocol import Message, ToolCall


def _provider() -> ProviderCfg:
    return ProviderCfg(
        baseUrl="https://api.example.com/v1",
        model="demo-model",
        apiKey="sk-secret-key",
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _sse(*chunks: dict) -> httpx.Response:
    parts = [f"data: {json.dumps(chunk)}" for chunk in chunks]
    parts.append("data: [DONE]")
    return httpx.Response(
        200,
        text="\n\n".join(parts) + "\n\n",
        headers={"Content-Type": "text/event-stream"},
    )


def test_responses_url() -> None:
    assert _responses_url("https://x.example/v1/") == "https://x.example/v1/responses"
    assert _responses_url("https://x.example/v1") == "https://x.example/v1/responses"


def test_tools_convert_chat_schema_to_flat() -> None:
    tools = [
        {
            "type": "function",
            "function": {"name": "bash", "description": "run", "parameters": {"type": "object"}},
        },
        {"type": "function", "name": "flat", "parameters": {"type": "object"}},
        "junk",
        {"type": "function", "function": {}},
    ]
    assert _to_responses_tools(tools) == [
        {"type": "function", "name": "bash", "parameters": {"type": "object"}, "description": "run"},
        {"type": "function", "name": "flat", "parameters": {"type": "object"}},
    ]


def test_input_mapping() -> None:
    messages = [
        Message(role="system", content="sys"),
        Message(role="system", content=""),
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="let me check",
            tool_calls=[ToolCall(id="c1", name="bash", arguments={"cmd": "ls"})],
        ),
        Message(role="assistant", content=None, tool_calls=None),
        Message(role="tool", content="out", tool_call_id="c1"),
        Message(role="tool", content="x", tool_call_id=None),
    ]
    assert _to_responses_input(messages) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "let me check"},
        {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": '{"cmd": "ls"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "out"},
    ]


def _completed(usage: dict | None = None, model: str = "demo-model") -> dict:
    response: dict = {"model": model, "status": "completed"}
    if usage is not None:
        response["usage"] = usage
    return {"type": "response.completed", "response": response}


@pytest.mark.asyncio
async def test_stream_full_turn() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return _sse(
            {"type": "response.created"},
            {"type": "response.reasoning_summary_text.delta", "delta": "thinking "},
            {"type": "response.reasoning_summary_text.delta", "delta": "hard"},
            {"type": "response.output_text.delta", "delta": "hi"},
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {"type": "function_call", "call_id": "c1", "name": "bash"},
            },
            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"q":'},
            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '"hi"}'},
            {
                "type": "response.output_item.added",
                "output_index": 2,
                "item": {"type": "function_call", "call_id": "c2", "name": "read"},
            },
            {"type": "response.function_call_arguments.done", "output_index": 2, "arguments": "{}"},
            _completed(
                {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                 "output_tokens_details": {"reasoning_tokens": 3}},
                model="demo-model-echo",
            ),
        )

    thoughts: list[str] = []
    contents: list[str] = []
    connect = OpenAIResponses(_provider(), client=_client(handler))
    reply = await connect.chat(
        [Message(role="user", content="x")],
        on_reasoning=thoughts.append,
        on_content=contents.append,
    )
    assert reply.content == "hi"
    assert reply.reasoning == "thinking hard"
    assert reply.model == "demo-model-echo"
    assert reply.finish_reason == "completed"
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("c1", "bash", {"q": "hi"}),
        ("c2", "read", {}),
    ]
    assert reply.usage == {
        "prompt_tokens": 10, "cached_tokens": 0, "completion_tokens": 5,
        "total_tokens": 15, "reasoning_tokens": 3,
    }
    assert "".join(thoughts) == "thinking hard"
    assert "".join(contents) == "hi"
    assert seen[0]["reasoning"] == {"effort": "high", "summary": "auto"}
    assert seen[0]["input"] == [{"role": "user", "content": "x"}]
    assert "tools" not in seen[0]


@pytest.mark.asyncio
async def test_stream_unknown_events_and_missing_completed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"type": "response.created"},
            {"type": "response.subscription_usage", "subscription": {}},
            {"type": "response.output_text.delta", "delta": "hi"},
        )

    connect = OpenAIResponses(_provider(), client=_client(handler))
    with pytest.raises(LLMError, match="response incomplete"):
        await connect.chat([Message(role="user", content="x")])


@pytest.mark.asyncio
async def test_stream_reasoning_text_delta_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"type": "response.reasoning_text.delta", "delta": "deep"},
            _completed(),
        )

    connect = OpenAIResponses(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.reasoning == "deep"


@pytest.mark.asyncio
async def test_stream_invalid_function_call_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "function_call", "call_id": "c1"},
            },
            _completed(),
        )

    connect = OpenAIResponses(_provider(), client=_client(handler))
    with pytest.raises(LLMError, match="missing name"):
        await connect.chat([Message(role="user", content="x")])


@pytest.mark.asyncio
async def test_nonstream_parses_output_and_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": [
                        {"type": "summary_text", "text": "a"},
                        {"type": "summary_text", "text": "b"},
                    ]},
                    {"type": "message", "content": [
                        {"type": "output_text", "text": "hello"},
                        {"type": "refusal", "refusal": "x"},
                    ]},
                    {"type": "function_call", "call_id": "c9", "name": "bash",
                     "arguments": "not-json"},
                ],
                "usage": {"input_tokens": 4, "output_tokens": 6},
            },
        )

    thoughts: list[str] = []
    connect = OpenAIResponses(_provider(), client=_client(handler))
    reply = await connect.chat(
        [Message(role="user", content="x")], on_reasoning=thoughts.append
    )
    assert reply.content == "hello"
    assert reply.reasoning == "ab"
    assert reply.tool_calls[0].parse_error == "invalid arguments"
    assert reply.usage == {
        "prompt_tokens": 4, "cached_tokens": 0, "completion_tokens": 6, "total_tokens": 10,
    }
    assert thoughts == ["ab"]

    def err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "bad input"}})

    connect = OpenAIResponses(_provider(), client=_client(err_handler))
    with pytest.raises(LLMError, match="bad input"):
        await connect.chat([Message(role="user", content="x")])


@pytest.mark.asyncio
async def test_bad_reasoning_drops_and_retries_once() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": {"message": "reasoning not supported"}})
        return httpx.Response(
            200, json={"model": "m", "status": "completed",
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}
        )

    connect = OpenAIResponses(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.content == "ok"
    assert "reasoning" in bodies[0]
    assert "reasoning" not in bodies[1]


@pytest.mark.asyncio
async def test_plain_400_raises_without_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    connect = OpenAIResponses(_provider(), client=_client(handler))
    with pytest.raises(LLMError, match="HTTP 400"):
        await connect.chat([Message(role="user", content="x")])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_then_ok_and_hook() -> None:
    calls = []
    seen_hooks: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, text="busy")
        return _sse(
            {"type": "response.output_text.delta", "delta": "ok"},
            _completed(),
        )

    connect = OpenAIResponses(_provider(), client=_client(handler))
    connect.set_retry_hook(lambda attempt, maximum: seen_hooks.append((attempt, maximum)))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.content == "ok"
    assert seen_hooks == [(1, 3)]


@pytest.mark.asyncio
async def test_transient_exhaustion_and_timeout() -> None:
    def busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    connect = OpenAIResponses(_provider(), client=_client(busy))
    with pytest.raises(LLMError, match="HTTP 500"):
        await connect.chat([Message(role="user", content="x")])

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    connect = OpenAIResponses(_provider(), client=_client(slow))
    with pytest.raises(LLMError, match="provider timeout"):
        await connect.chat([Message(role="user", content="x")])


@pytest.mark.asyncio
async def test_errors_never_leak_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad sk-secret-key"}})

    connect = OpenAIResponses(_provider(), client=_client(handler))
    with pytest.raises(LLMError) as excinfo:
        await connect.chat([Message(role="user", content="x")])
    assert "sk-secret-key" not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)


def test_normalize_usage_responses_shape() -> None:
    assert normalize_usage(
        {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
         "input_tokens_details": {"cached_tokens": 4},
         "output_tokens_details": {"reasoning_tokens": 2}},
        "openai_responses",
    ) == {
        "prompt_tokens": 10, "cached_tokens": 4, "completion_tokens": 5,
        "total_tokens": 15, "reasoning_tokens": 2,
    }
    assert normalize_usage({"prompt_tokens": 1}, "openai_responses") is None


@pytest.mark.asyncio
async def test_reasoning_summary_feeds_persist_and_cost_pipeline() -> None:
    """TASK-011/012: summaries land in ChatReply.reasoning (UI/persist) and
    usage normalizes into the trace/pricing shape."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(
            {"type": "response.reasoning_summary_text.delta", "delta": "pondering"},
            {"type": "response.output_text.delta", "delta": "answer"},
            _completed(
                {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                 "input_tokens_details": {"cached_tokens": 20},
                 "output_tokens_details": {"reasoning_tokens": 40}},
            ),
        )

    from pathlib import Path

    from thyca.agent.observe import Observe
    from thyca.agent.stage import Stage
    from thyca.config import PricingCfg
    from thyca.llm.pricing import cost_for
    from thyca.sessions import SessionManager

    connect = OpenAIResponses(_provider(), client=_client(handler))
    reply = await connect.chat([Message(role="user", content="x")])
    assert reply.reasoning == "pondering"

    manager = SessionManager(Path("/tmp") / "unused-observe")
    manager.create()
    stage = Stage(messages=[], reply=reply)
    Observe(manager).assistant(stage)
    stored = manager.current.messages[-1]
    assert stored.reasoning == "pondering"

    pricing = {"demo-model": PricingCfg(input=1.0, cache=0.5, output=2.0)}
    cost = cost_for(reply.model or "demo-model", reply.usage, pricing)
    assert cost is not None and cost > 0
