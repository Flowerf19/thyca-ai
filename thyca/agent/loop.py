from __future__ import annotations

from collections.abc import Callable

from thyca.llm.pricing import cost_for
from thyca.sessions import SessionManager

from .act import Act
from .assemble import Assemble
from .events import EventSink, TurnEvent, emit_event
from .observe import Observe
from .stage import Stage
from .think import Think
from .thinking import ThinkingDelta
from .reply import ContentDelta


def _reasoning_callback(
    event_sink: EventSink | None, round_no: int
) -> Callable[[str], None] | None:
    if event_sink is None:
        return None

    def on_reasoning(delta: str) -> None:
        if not delta:
            return
        try:
            event_sink(ThinkingDelta(round=round_no, delta=delta))  # type: ignore[arg-type]
        except Exception:
            pass

    return on_reasoning


def _content_callback(
    event_sink: EventSink | None, round_no: int
) -> Callable[[str], None] | None:
    if event_sink is None:
        return None

    def on_content(delta: str) -> None:
        if not delta:
            return
        try:
            event_sink(ContentDelta(round=round_no, delta=delta))  # type: ignore[arg-type]
        except Exception:
            pass

    return on_content


def _resolve_cost(stage: Stage, model: str | None, pricing: dict | None) -> None:
    echoed = getattr(stage.reply, "model", None)
    if isinstance(echoed, str) and echoed.strip():
        stage.llm_model = echoed.strip()
    usage = getattr(stage.reply, "usage", None)
    stage.llm_cost_usd = cost_for(stage.llm_model, usage, pricing)
    if stage.llm_cost_usd is None and model and model != stage.llm_model:
        stage.llm_cost_usd = cost_for(model, usage, pricing)


class AgentLoop:
    def __init__(
        self,
        sessions: SessionManager,
        assemble: Assemble,
        think: Think,
        act: Act,
        observe: Observe,
        loop_max: int,
        tools: list | None = None,
        model: str | None = None,
        pricing: dict | None = None,
    ) -> None:
        self._sessions = sessions
        self._assemble = assemble
        self._think = think
        self._act = act
        self._observe = observe
        self._loop_max = loop_max
        self._tools = tools
        self._model = model
        self._pricing = pricing

    async def run(
        self,
        user_msg: str,
        hot: object = None,
        event_sink: EventSink | None = None,
        persist_user: bool = True,
    ) -> str:
        if self._loop_max < 1:
            raise ValueError("loop_max must be positive")

        stage = Stage(
            messages=list(self._sessions.current.messages),
            hot=hot,
            tools=self._tools,
        )
        self._observe.compact()
        if persist_user:
            self._assemble.assemble(stage, user_msg)
            self._observe.user(stage)
        else:
            stage.messages = list(self._sessions.current.messages)
            self._assemble.assemble(stage, user_msg, append_user=False)
        emit_event(event_sink, TurnEvent(type="turn.accepted"))

        for _ in range(self._loop_max):
            stage.round += 1
            # carry model/pricing so Observe/Trace can build meta without reading config
            stage.llm_model = self._model
            emit_event(event_sink, TurnEvent(type="llm.started", round=stage.round))
            round_no = stage.round
            on_reasoning = _reasoning_callback(event_sink, round_no)
            on_content = _content_callback(event_sink, round_no)
            await self._think.think(
                stage, on_reasoning=on_reasoning, on_content=on_content
            )
            assert stage.reply is not None
            _resolve_cost(stage, self._model, self._pricing)
            emit_event(
                event_sink,
                TurnEvent(
                    type="llm.finished",
                    round=stage.round,
                    tool_count=len(stage.reply.tool_calls),
                ),
            )
            if not stage.reply.tool_calls:
                return self._observe.assistant(stage)
            await self._act.act(stage, event_sink)
            self._observe.observe(stage)
            if stage.round == self._loop_max:
                return self._observe.loop_limit(stage)

        raise ValueError("loop_max must be positive")
