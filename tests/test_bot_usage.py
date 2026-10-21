from thyca.bots.usage import usage_for_window
from thyca.core.protocol import Message


def row(span, *, usage=None, cost=None, status=None, kind="llm"):
    return Message(role="assistant", content="", meta={
        "kind": kind, "span_id": span, "trace_id": "trace-" + span,
        "model": "model", "usage": usage, "cost_usd": cost, "status": status,
    })


def test_usage_is_span_scoped_and_stop_markers_are_not_calls():
    tokens = {"prompt_tokens": 11, "cached_tokens": 2,
              "completion_tokens": 3, "total_tokens": 14}
    messages = [row("first", usage=tokens, cost=0.01),
                row("second", usage=tokens, cost=0.02),
                row("second", usage=tokens, cost=0.02, status="loop_limit")]
    result = usage_for_window(messages)
    assert result["last_turn"]["span_id"] == "second"
    assert result["last_turn"]["requests"] == 1
    assert result["last_turn"]["usage"] == tokens
    assert result["last_turn"]["cost_usd"] == 0.02
    assert result["conversation"]["requests"] == 2
    assert result["conversation"]["cost_usd"] == 0.03
    assert result["conversation"]["usage"]["prompt_tokens"] == 22


def test_unknown_usage_and_partial_cost_are_not_zero_or_complete_totals():
    result = usage_for_window([
        row("first", usage={"prompt_tokens": 11}, cost=0.01),
        row("second"),
    ])
    assert result["last_turn"]["cost_usd"] is None
    assert result["conversation"]["cost_usd"] is None
    assert result["conversation"]["known_cost_usd"] == 0.01
    assert result["conversation"]["unpriced_requests"] == 1
    assert all(value is None for value in result["conversation"]["usage"].values())


def test_explicit_zero_is_preserved_and_other_kinds_are_excluded():
    zero = dict.fromkeys(("prompt_tokens", "cached_tokens", "completion_tokens", "total_tokens"), 0)
    result = usage_for_window([
        row("first", usage=zero, cost=0),
        row("naming", usage=zero, cost=100, kind="naming"),
        row("aggregate", usage=zero, cost=100, kind="message"),
    ])
    assert result["conversation"]["requests"] == 1
    assert result["conversation"]["cost_usd"] == 0
    assert result["conversation"]["usage"] == zero
    assert result["last_turn"]["span_id"] == "first"


def test_empty_and_stop_only_have_no_measured_requests():
    empty = usage_for_window([])
    assert empty["last_turn"] is None
    assert empty["conversation"]["requests"] == 0
    assert empty["conversation"]["cost_usd"] is None
    stopped = usage_for_window([row("stop", status="context_limit")])
    assert stopped["last_turn"]["span_id"] == "stop"
    assert stopped["last_turn"]["requests"] == 0
    assert stopped["last_turn"]["cost_usd"] is None


def test_invalid_counts_and_costs_are_unknown():
    for bad in (True, -1, float("nan"), float("inf")):
        result = usage_for_window([row("span", usage={"prompt_tokens": bad}, cost=bad)])
        assert result["conversation"]["usage"]["prompt_tokens"] is None
        assert result["conversation"]["cost_usd"] is None
