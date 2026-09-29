"""GOAL-002: reasoning contracts across transports (regression).

Explicit empty summary[] (even encrypted) must survive; missing or
malformed summary stays dropped; native reasoning needs a nonblank
string id; Chat transport must not forward native reasoning items.
"""
from __future__ import annotations

import json

import httpx
import pytest

from thyca.config import ProviderCfg
from thyca.core.protocol import Message
from thyca.llm.openai_chat import OpenAIChat, _to_openai_message
from thyca.llm.openai_responses import OpenAIResponses, _to_responses_input


def _provider() -> ProviderCfg:
    return ProviderCfg(
        baseUrl="https://api.example.com/v1",
        model="demo-model",
        apiKey="sk-secret-key",
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_empty_summary_preserved_with_and_without_encrypted() -> None:
    details = [
        {"type": "reasoning", "id": "rs_e", "summary": []},
        {
            "type": "reasoning",
            "id": "rs_ee",
            "summary": [],
            "encrypted_content": "blob",
        },
    ]
    items = _to_responses_input(
        [Message(role="assistant", content="ok", reasoning_details=details)]
    )
    assert items[0] == {"type": "reasoning", "summary": [], "id": "rs_e"}
    assert items[1] == {
        "type": "reasoning",
        "summary": [],
        "id": "rs_ee",
        "encrypted_content": "blob",
    }
    assert items[2] == {"role": "assistant", "content": "ok"}


def test_missing_or_malformed_summary_stays_dropped() -> None:
    details = [
        {"type": "reasoning", "id": "rs_no_summary", "encrypted_content": "blob"},
        {"type": "reasoning", "id": "rs_str", "summary": "nope"},
        {
            "type": "reasoning",
            "id": "rs_junk",
            "summary": [{"type": "bogus", "text": "x"}, "nope"],
        },
    ]
    items = _to_responses_input(
        [Message(role="assistant", content="ok", reasoning_details=details)]
    )
    assert items == [{"role": "assistant", "content": "ok"}]


def test_native_reasoning_requires_nonblank_string_id() -> None:
    valid = {"type": "summary_text", "text": "plan"}
    details = [
        {"type": "reasoning", "summary": [valid]},
        {"type": "reasoning", "id": "", "summary": [valid]},
        {"type": "reasoning", "id": "  ", "summary": [valid]},
        {"type": "reasoning", "id": "\t", "summary": [valid]},
        {"type": "reasoning", "id": 42, "summary": [valid]},
        {"type": "reasoning", "id": "rs_ok", "summary": [valid]},
    ]
    items = _to_responses_input(
        [Message(role="assistant", content="ok", reasoning_details=details)]
    )
    assert items == [
        {"type": "reasoning", "summary": [valid], "id": "rs_ok"},
        {"role": "assistant", "content": "ok"},
    ]


def test_chat_boundary_filters_native_reasoning_only() -> None:
    native = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [{"type": "summary_text", "text": "plan"}],
    }
    chat_shapes = [
        {"type": "reasoning.text", "text": "t", "signature": "sig"},
        {"type": "reasoning.summary", "summary": "s"},
        {"type": "reasoning.encrypted", "data": "d"},
    ]
    payload = _to_openai_message(
        Message(
            role="assistant",
            content="hi",
            reasoning_details=[native, *chat_shapes],
        )
    )
    assert payload["reasoning_details"] == chat_shapes
    assert payload["reasoning_details"] is not chat_shapes
    # Chat shapes ride through byte-identical; native-only leaves no key.
    lone = _to_openai_message(
        Message(role="assistant", content="hi", reasoning_details=[native])
    )
    assert "reasoning_details" not in lone


@pytest.mark.asyncio
async def test_two_round_empty_summary_jsonl_roundtrip() -> None:
    """Persisted empty-summary reasoning returns on round 2 with its id."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        bodies.append(body)
        for item in body.get("input", []):
            if isinstance(item, dict) and item.get("type") == "reasoning":
                assert "summary" in item, "summary-less reasoning must never reach the wire"
                assert isinstance(item.get("id"), str) and item["id"]
        if len(bodies) == 1:
            return httpx.Response(
                200,
                json={
                    "model": "demo-model",
                    "status": "completed",
                    "output": [
                        {
                            "type": "reasoning",
                            "id": "rs_empty",
                            "summary": [],
                            "encrypted_content": "enc-blob",
                        },
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "first"}],
                        },
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "demo-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "second"}],
                    }
                ],
            },
        )

    connect = OpenAIResponses(_provider(), client=_client(handler))
    first = await connect.chat([Message(role="user", content="q1")])
    assert first.reasoning_details == [
        {
            "type": "reasoning",
            "id": "rs_empty",
            "summary": [],
            "encrypted_content": "enc-blob",
        }
    ]
    # JSONL persistence roundtrip, like the session store.
    stored = Message(
        role="assistant",
        content=first.content,
        reasoning_details=first.reasoning_details,
    )
    revived = Message.from_json_line(stored.to_json_line())
    await connect.chat(
        [Message(role="user", content="q1"), revived, Message(role="user", content="q2")]
    )
    round2 = bodies[1]["input"]
    assert {
        "type": "reasoning",
        "summary": [],
        "id": "rs_empty",
        "encrypted_content": "enc-blob",
    } in round2


@pytest.mark.asyncio
async def test_two_round_missing_and_invalid_id_never_sent() -> None:
    """Missing/invalid-id reasoning never reaches the wire, across JSONL revive."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        bodies.append(body)
        for item in body.get("input", []):
            if isinstance(item, dict) and item.get("type") == "reasoning":
                assert "summary" in item, "summary-less reasoning must never reach the wire"
                assert isinstance(item.get("id"), str) and item["id"]
        return httpx.Response(
            200,
            json={
                "model": "demo-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
            },
        )

    connect = OpenAIResponses(_provider(), client=_client(handler))
    history = Message(
        role="assistant",
        content="prior",
        reasoning_details=[
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "s"}]},
            {"type": "reasoning", "id": 7, "summary": [{"type": "summary_text", "text": "s"}]},
        ],
    )
    first = await connect.chat([history, Message(role="user", content="q1")])
    assert bodies[0]["input"] == [
        {"role": "assistant", "content": "prior"},
        {"role": "user", "content": "q1"},
    ]
    # JSONL persistence roundtrip, like the session store.
    revived_history = Message.from_json_line(history.to_json_line())
    stored = Message(
        role="assistant",
        content=first.content,
        reasoning_details=first.reasoning_details,
    )
    revived = Message.from_json_line(stored.to_json_line())
    await connect.chat(
        [
            revived_history,
            Message(role="user", content="q1"),
            revived,
            Message(role="user", content="q2"),
        ]
    )
    assert bodies[1]["input"] == [
        {"role": "assistant", "content": "prior"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "q2"},
    ]


@pytest.mark.asyncio
async def test_chat_transport_after_provider_switch() -> None:
    """Responses-native history switched to Chat: native filtered on the wire."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200, json={"model": "m", "choices": [{"message": {"content": "ok"}}]}
        )

    history = Message(
        role="assistant",
        content="prior",
        reasoning_details=[
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "plan"}],
                "encrypted_content": "enc",
            },
            {"type": "reasoning.text", "text": "t", "signature": "sig"},
        ],
    )
    revived = Message.from_json_line(history.to_json_line())
    connect = OpenAIChat(_provider(), client=_client(handler))
    await connect.chat([Message(role="user", content="q"), revived])
    sent = seen[0]["messages"][1]
    assert sent["reasoning_details"] == [
        {"type": "reasoning.text", "text": "t", "signature": "sig"}
    ]
