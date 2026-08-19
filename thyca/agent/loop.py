from __future__ import annotations

from thyca.sessions import SessionManager

from .act import Act
from .assemble import Assemble
from .observe import Observe
from .stage import Stage
from .think import Think


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
    ) -> None:
        self._sessions = sessions
        self._assemble = assemble
        self._think = think
        self._act = act
        self._observe = observe
        self._loop_max = loop_max
        self._tools = tools

    async def run(self, user_msg: str, hot: object = None) -> str:
        if self._loop_max < 1:
            raise ValueError("loop_max must be positive")

        stage = Stage(
            messages=list(self._sessions.current.messages),
            hot=hot,
            tools=self._tools,
        )
        self._observe.compact()
        self._assemble.assemble(stage, user_msg)
        self._observe.user(stage)

        for _ in range(self._loop_max):
            stage.round += 1
            await self._think.think(stage)
            assert stage.reply is not None
            if not stage.reply.tool_calls:
                return self._observe.assistant(stage)
            await self._act.act(stage)
            self._observe.observe(stage)
            if stage.round == self._loop_max:
                return self._observe.loop_limit(stage)

        raise ValueError("loop_max must be positive")
