from __future__ import annotations

from thyca.protocol import Message, ToolCall, ToolResult
from thyca.sessions import SessionManager

from .stage import Stage


def _tool_message(result: ToolResult) -> Message:
    meta = {"is_error": True} if result.is_error else None
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

    def assistant(self, stage: Stage) -> str:
        content = "" if stage.reply is None else (stage.reply.content or "")
        self._sessions.append(Message(role="assistant", content=content))
        return content

    def observe(self, stage: Stage) -> None:
        if stage.reply is None:
            raise ValueError("Stage.reply is required")
        assistant = Message(
            role="assistant",
            content=stage.reply.content,
            tool_calls=stage.reply.tool_calls,
        )
        ordered = self._order_results(stage.reply.tool_calls, stage.results)
        tool_messages = [_tool_message(result) for result in ordered]
        added = [assistant, *tool_messages]
        for message in added:
            self._sessions.append(message)
        stage.messages.extend(added)
        stage.results = ordered

    def loop_limit(self, stage: Stage) -> str:
        text = "loop limit reached"
        msg = Message(role="assistant", content=text)
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
