"""Golden test: freeze CURRENT Chat Completions wire bytes (GOAL-001 TASK-001).

Current module paths (2026-09-29, branch refactor/provider-rebuild):
  payload items .. thyca.llm.openai_chat._to_openai_message
  endpoint ...... thyca.llm.openai_chat._chat_url
  config ........ thyca.config.providers.ProviderCfg
Merged 2026-09-29 (TASK-005): chat parse/SSE now live in openai_chat.py;
the EXPECTED_* bytes below stayed identical.
"""
from __future__ import annotations

import json

from thyca.config.providers import ProviderCfg
from thyca.core.protocol import Message, ToolCall
from thyca.llm.openai_chat import _chat_url, _to_openai_message

BASE_URL = "https://api.example.com/v1"
MODEL = "golden-model"

# Shared transcript (identical in test_wire_golden_responses.py): system +
# user text + assistant with one tool_call + one tool result.
TRANSCRIPT = [
    Message(role="system", content="You are a test assistant."),
    Message(role="user", content="What is 2+2?"),
    Message(
        role="assistant",
        content="Let me compute that.",
        tool_calls=[ToolCall(id="call_1", name="calc", arguments={"expr": "2+2"})],
    ),
    Message(role="tool", content="4", tool_call_id="call_1"),
]

# Recorded 2026-09-29 by running _build_payload once; frozen since.
EXPECTED_CHAT = (
    b'{"messages":[{"content":"You are a test assistant.","role":"system"},'
    b'{"content":"What is 2+2?","role":"user"},'
    b'{"content":"Let me compute that.","role":"assistant","tool_calls":'
    b'[{"function":{"arguments":"{\\"expr\\": \\"2+2\\"}","name":"calc"},'
    b'"id":"call_1","type":"function"}]},'
    b'{"content":"4","role":"tool","tool_call_id":"call_1"}],'
    b'"model":"golden-model","reasoning_effort":"high","stream":true,'
    b'"stream_options":{"include_usage":true}}'
)

EXPECTED_CHAT_EFFORT_LOW = EXPECTED_CHAT.replace(b'"reasoning_effort":"high"', b'"reasoning_effort":"low"')


def _build_payload(provider: ProviderCfg, messages: list[Message]) -> dict:
    """Mirror OpenAIChat.chat() payload construction exactly (no tools)."""
    payload: dict = {
        "model": provider.model,
        "messages": [_to_openai_message(m) for m in messages],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if provider.reasoningEffort:
        payload["reasoning_effort"] = provider.reasoningEffort
    return payload


def _wire_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _provider(effort: str) -> ProviderCfg:
    return ProviderCfg(
        baseUrl=BASE_URL, apiKey="sk-test", model=MODEL, reasoningEffort=effort, api="openai_chat"
    )


def test_chat_wire_golden() -> None:
    provider = _provider("high")
    assert _chat_url(provider.baseUrl) == BASE_URL + "/chat/completions"
    assert _wire_bytes(_build_payload(provider, TRANSCRIPT)) == EXPECTED_CHAT


def test_chat_wire_golden_reasoning_effort_variant() -> None:
    """reasoning_effort passes through verbatim; same bytes otherwise."""
    provider = _provider("low")
    raw = _wire_bytes(_build_payload(provider, TRANSCRIPT))
    assert b'"reasoning_effort":"low"' in raw
    assert raw == EXPECTED_CHAT_EFFORT_LOW
