from __future__ import annotations

import json
from collections.abc import Callable

from thyca.core.protocol import estimate_tokens as _chars_to_tokens
from thyca.llm.pricing import cost_for
from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveSnapshot
from thyca.sessions import SessionManager

from .act import Act
from .assemble import Assemble
from .events import EventSink, TurnEvent, emit_event
from .observe import Observe
from .shrink import BACKSTOP_RATIO, shrink_stage_messages
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
        prompts: PromptManager | None = None,
        context_tokens: int | None = None,
    ) -> None:
        # context_tokens None disables the mid-turn guard (unit tests, no limits).
        self._sessions = sessions
        self._assemble = assemble
        self._think = think
        self._act = act
        self._observe = observe
        self._loop_max = loop_max
        self._tools = tools
        self._model = model
        self._pricing = pricing
        self._prompts = prompts or PromptManager()
        self._context_tokens = context_tokens

    def _hot_tokens(self, hot: object) -> int:
        if not isinstance(hot, ActiveSnapshot):
            return 0
        return _chars_to_tokens(self._prompts.build(hot))

    def _tools_tokens(self) -> int:
        if not self._tools:
            return 0
        return _chars_to_tokens(json.dumps(self._tools, ensure_ascii=False))

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
        # Overhead sizes (hot prompt, tools schema, pending user text) let
        # the compactor reserve room for what assemble adds after this.
        self._observe.compact(
            hot_tokens=self._hot_tokens(hot),
            tools_tokens=self._tools_tokens(),
            pending_user_tokens=_chars_to_tokens(user_msg),
        )
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
        # Index boundary for the shrink guard: entries below are pre-run
        # history (round numbers restart every turn, so round-protection
        # must not apply to them); entries at/above are this run's own.
        run_start = len(stage.messages)

        for _ in range(self._loop_max):
            stage.round += 1
            # carry model/pricing so Observe/Trace can build meta without reading config
            stage.llm_model = self._model
            if self._context_tokens is not None:
                shrunk_messages, shrunk, hidden, estimate = shrink_stage_messages(
                    stage.messages,
                    current_round=stage.round,
                    context_tokens=self._context_tokens,
                    tools_tokens=self._tools_tokens(),
                    run_start=run_start,
                )
                if shrunk:
                    stage.messages = shrunk_messages
                    emit_event(
                        event_sink,
                        TurnEvent(
                            type="context.shrunk",
                            round=stage.round,
                            tool_count=shrunk,
                            hidden_bytes=hidden,
                        ),
                    )
                if estimate > int(self._context_tokens * BACKSTOP_RATIO):
                    return self._observe.context_limit(stage)
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
