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


def _delta_callback(
    event_sink: EventSink | None,
    round_no: int,
    cls: type[ThinkingDelta] | type[ContentDelta],
) -> Callable[[str], None] | None:
    """One live-delta callback: reasoning and content differ only in class."""
    if event_sink is None:
        return None

    def on_delta(delta: str) -> None:
        if not delta:
            return
        try:
            event_sink(cls(round=round_no, delta=delta))  # type: ignore[arg-type]
        except Exception:
            pass

    return on_delta


def _resolve_cost(stage: Stage, model: str | None, pricing: dict | None) -> None:
    # Think already echoed reply.model into stage.llm_model; only the cost
    # fallback lives here.
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

        # Compact first: the stage must snapshot post-compaction history so
        # the turn tripping the cap sends the compacted tail to the LLM.
        self._observe.compact()
        stage = Stage(
            messages=list(self._sessions.current.messages),
            hot=hot,
            tools=self._tools,
        )
        if persist_user:
            self._assemble.assemble(stage, user_msg)
            self._observe.user(stage)
        else:
            self._assemble.assemble(stage, user_msg, append_user=False)
        emit_event(event_sink, TurnEvent(type="turn.accepted"))

        for _ in range(self._loop_max):
            stage.round += 1
            # carry model/pricing so Observe/Trace can build meta without reading config
            stage.llm_model = self._model
            emit_event(event_sink, TurnEvent(type="llm.started", round=stage.round))
            round_no = stage.round
            on_reasoning = _delta_callback(event_sink, round_no, ThinkingDelta)
            on_content = _delta_callback(event_sink, round_no, ContentDelta)
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

        # Unreachable: loop_max >= 1 always enters, and every path returns.
        raise AssertionError("unreachable: agent loop fell through")
