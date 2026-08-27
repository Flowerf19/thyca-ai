from __future__ import annotations

from thyca.protocol import Message, ToolCall, ToolResult
from thyca.sessions import SessionManager

from .stage import Stage


def _tool_message(
    result: ToolResult, latency_ms: int | None = None, round_no: int | None = None
) -> Message:
    meta: dict | None = None
    if result.is_error:
        meta = {"is_error": True}
    if latency_ms is not None:
        if meta is None:
            meta = {}
        meta["latency_ms"] = latency_ms
    if round_no is not None and round_no > 0:
        if meta is None:
            meta = {}
        meta["round"] = round_no
    return Message(
        role="tool",
        content=result.content,
        tool_call_id=result.tool_call_id,
        meta=meta,
    )


class Observe:
    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions

    def compact(self) -> bool:
        return self._sessions.compact_if_needed()

    def user(self, stage: Stage) -> None:
        self._sessions.append(stage.messages[-1])

    def _assistant_meta(self, stage: Stage, *, kind: str = "llm") -> dict | None:
        meta: dict = {"kind": kind}
        if stage.round:
            meta["round"] = stage.round
        model = getattr(stage, "llm_model", None) or getattr(getattr(stage, "reply", None), "model", None)
        if isinstance(model, str) and model.strip():
            meta["model"] = model.strip()
        latency = getattr(stage, "llm_latency_ms", None)
        if isinstance(latency, int) and latency >= 0:
            meta["latency_ms"] = latency
        usage = getattr(getattr(stage, "reply", None), "usage", None)
        if isinstance(usage, dict) and usage:
            meta["usage"] = dict(usage)
        cost = getattr(stage, "llm_cost_usd", None)
        if isinstance(cost, (int, float)):
            meta["cost_usd"] = float(cost)
        finish = getattr(getattr(stage, "reply", None), "finish_reason", None)
        if isinstance(finish, str) and finish:
            meta["finish_reason"] = finish
        # drop kind-only meta when no other field — still keep kind for trace grouping
        return meta

    def assistant(self, stage: Stage) -> str:
        content = "" if stage.reply is None else (stage.reply.content or "")
        meta = self._assistant_meta(stage, kind="llm")
        self._sessions.append(Message(role="assistant", content=content, meta=meta))
        return content

    def observe(self, stage: Stage) -> None:
        if stage.reply is None:
            raise ValueError("Stage.reply is required")
        meta = self._assistant_meta(stage, kind="llm")
        assistant = Message(
            role="assistant",
            content=stage.reply.content,
            tool_calls=stage.reply.tool_calls,
            meta=meta,
        )
        ordered = self._order_results(stage.reply.tool_calls, stage.results)
        latencies = getattr(stage, "tool_latencies", {}) or {}
        tool_messages = [
            _tool_message(result, latency_ms=latencies.get(result.tool_call_id), round_no=stage.round)
            for result in ordered
        ]
        added = [assistant, *tool_messages]
        for message in added:
            self._sessions.append(message)
        stage.messages.extend(added)
        stage.results = ordered

    def loop_limit(self, stage: Stage) -> str:
        text = "loop limit reached"
        meta = self._assistant_meta(stage, kind="llm")
        if meta is not None:
            meta["status"] = "loop_limit"
        msg = Message(role="assistant", content=text, meta=meta)
        self._sessions.append(msg)
        stage.messages.append(msg)
        return text

    @staticmethod
    def _order_results(
        calls: list[ToolCall],
        results: list[ToolResult],
    ) -> list[ToolResult]:
        if len(calls) != len(results):
            raise ValueError("calls and results must have the same length")

        call_ids = [call.id for call in calls]
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("duplicate tool call id")

        results_by_id: dict[str, ToolResult] = {}
        for result in results:
            if result.tool_call_id in results_by_id:
                raise ValueError("duplicate tool result id")
            results_by_id[result.tool_call_id] = result

        call_id_set = set(call_ids)
        result_id_set = set(results_by_id)
        if call_id_set - result_id_set:
            raise ValueError("missing tool result id")
        if result_id_set - call_id_set:
            raise ValueError("extra tool result id")

        return [results_by_id[call_id] for call_id in call_ids]
