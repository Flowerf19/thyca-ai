from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from thyca.core.protocol import Message, ToolResult

if TYPE_CHECKING:
    from .think import ChatReply


@dataclass
class Stage:
    """Shared workspace for one user turn. Phases only read/write this."""

    messages: list[Message] = field(default_factory=list)
    round: int = 0
    hot: object = None
    tools: list | None = None
    reply: ChatReply | None = None
    results: list[ToolResult] = field(default_factory=list)
    # trace carriers — set by Think/Act, consumed by Observe
    llm_latency_ms: int | None = None
    llm_model: str | None = None
    llm_cost_usd: float | None = None
    # Raw wire estimate for this round's outgoing payload, set by the loop
    # before think() and persisted next to provider usage so later turns
    # can calibrate the estimator per model. Raw on purpose: storing the
    # calibrated value would make future ratios decay toward 1.
    llm_estimate_tokens: int | None = None
    tool_latencies: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 0:
            raise ValueError("Stage.round must be a non-negative integer")
