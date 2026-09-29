"""Golden test: freeze CURRENT Responses wire bytes (GOAL-001 TASK-001).

Current module paths (2026-09-29, branch refactor/provider-rebuild):
  payload items .. thyca.llm.openai_responses._to_responses_input
                   thyca.llm.openai_responses._to_responses_tools
  endpoint ...... thyca.llm.openai_responses._responses_url
  config ........ thyca.config.providers.ProviderCfg
responses_parse.py was folded into openai_responses.py (TASK-006). The ONLY
allowed bytes change versus the GOAL-001 recording is the added
"store":false field; everything else must stay identical.
"""
from __future__ import annotations

import json

from thyca.config.providers import ProviderCfg
from thyca.core.protocol import Message, ToolCall
from thyca.llm.openai_responses import _responses_url, _to_responses_input

BASE_URL = "https://api.example.com/v1"
MODEL = "golden-model"

# Shared transcript (identical in test_wire_golden_chat.py): system + user
# text + assistant with one tool_call + one tool result.
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
EXPECTED_RESPONSES = (
    b'{"input":[{"content":"You are a test assistant.","role":"system"},'
    b'{"content":"What is 2+2?","role":"user"},'
    b'{"content":"Let me compute that.","role":"assistant"},'
    b'{"arguments":"{\\"expr\\": \\"2+2\\"}","call_id":"call_1","name":"calc",'
    b'"type":"function_call"},'
    b'{"call_id":"call_1","output":"4","type":"function_call_output"}],'
    b'"model":"golden-model","reasoning":{"effort":"high","summary":"auto"},'
    b'"store":false,"stream":true}'
)

EXPECTED_RESPONSES_EFFORT_MAX = EXPECTED_RESPONSES.replace(b'"effort":"high"', b'"effort":"max"')


def _build_payload(provider: ProviderCfg, messages: list[Message]) -> dict:
    """Mirror OpenAIResponses.chat() payload construction exactly (no tools)."""
    payload: dict = {
        "model": provider.model,
        "input": _to_responses_input(messages),
        "stream": True,
        "store": False,
    }
    if provider.reasoningEffort:
        payload["reasoning"] = {"effort": provider.reasoningEffort, "summary": "auto"}
    return payload


def _wire_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _provider(effort: str) -> ProviderCfg:
    return ProviderCfg(
        baseUrl=BASE_URL, apiKey="sk-test", model=MODEL, reasoningEffort=effort, api="openai_responses"
    )


def test_responses_wire_golden() -> None:
    provider = _provider("high")
    assert _responses_url(provider.baseUrl) == BASE_URL + "/responses"
    assert _wire_bytes(_build_payload(provider, TRANSCRIPT)) == EXPECTED_RESPONSES


def test_responses_wire_golden_reasoning_variant() -> None:
    """reasoning {effort, summary:auto} passes effort verbatim; rest identical."""
    provider = _provider("max")
    raw = _wire_bytes(_build_payload(provider, TRANSCRIPT))
    assert b'"reasoning":{"effort":"max","summary":"auto"}' in raw
    assert raw == EXPECTED_RESPONSES_EFFORT_MAX
