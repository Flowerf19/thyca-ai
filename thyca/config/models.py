"""Registered-model entity plus its sparse wire form."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .defaults import DEFAULT_LIMITS_CONTEXT_TOKENS_MAX, REASONING_EFFORTS
from .errors import ConfigError
from .validation import _integer, _number


@dataclass(frozen=True)
class ModelCfg:
    """A model the user registered: optional provider override + token prices.

    ``baseUrl`` empty means "use provider.baseUrl"; a non-empty value points
    the model at another OpenAI-compatible endpoint (multi-provider).
    Prices are USD / 1M tokens. Limits/reasoning empty or None inherit the
    global provider/limits values.

    ``provider`` names the entry in ``Config.providers`` this model calls;
    empty means the default provider.
    """

    provider: str = ""
    baseUrl: str = ""
    input: float = 0.0
    cache: float = 0.0
    output: float = 0.0
    reasoningEffort: str = ""
    reasoningEfforts: tuple[str, ...] = ()
    loopMax: int | None = None
    hotTailKB: int | None = None
    contextTokens: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str):
            raise ConfigError("models[].provider must be a string")
        if not isinstance(self.baseUrl, str):
            raise ConfigError("models[].baseUrl must be a string")
        if self.baseUrl and not self.baseUrl.startswith(("http://", "https://")):
            raise ConfigError(
                f"models[].baseUrl must start with http:// or https://: {self.baseUrl!r}"
            )
        object.__setattr__(self, "input", _number(self.input, "models[].input"))
        object.__setattr__(self, "cache", _number(self.cache, "models[].cache"))
        object.__setattr__(self, "output", _number(self.output, "models[].output"))
        if not isinstance(self.reasoningEffort, str):
            raise ConfigError("models[].reasoningEffort must be a string")
        if not isinstance(self.reasoningEfforts, tuple) or not all(
            isinstance(level, str) and level.strip() for level in self.reasoningEfforts
        ):
            raise ConfigError(
                "models[].reasoningEfforts must be a list of non-empty strings"
            )
        if len(set(self.reasoningEfforts)) != len(self.reasoningEfforts):
            raise ConfigError("models[].reasoningEfforts must not contain duplicates")
        if self.reasoningEffort and self.reasoningEfforts and self.reasoningEffort not in self.reasoningEfforts:
            raise ConfigError(
                f"models[].reasoningEffort {self.reasoningEffort!r} is not in "
                f"models[].reasoningEfforts ({'/'.join(self.reasoningEfforts)})"
            )
        if self.reasoningEffort and not self.reasoningEfforts and self.reasoningEffort not in REASONING_EFFORTS:
            raise ConfigError(
                "models[].reasoningEffort must be one of "
                f"{'/'.join(REASONING_EFFORTS)} (or declare models[].reasoningEfforts), "
                f"got {self.reasoningEffort!r}"
            )
        for value, name, lower, upper in (
            (self.loopMax, "models[].loopMax", 1, 200),
            (self.hotTailKB, "models[].hotTailKB", 1, 64),
            (self.contextTokens, "models[].contextTokens", 1000, DEFAULT_LIMITS_CONTEXT_TOKENS_MAX),
        ):
            if value is None:
                continue
            _integer(value, name)
            if not lower <= value <= upper:
                raise ConfigError(f"{name} must be {lower}..{upper}, got {value}")


def _model_to_dict(cfg: ModelCfg) -> dict[str, Any]:
    data: dict[str, Any] = {
        "baseUrl": cfg.baseUrl,
        "input": cfg.input,
        "cache": cfg.cache,
        "output": cfg.output,
    }
    if cfg.provider:
        data["provider"] = cfg.provider
    if cfg.reasoningEffort:
        data["reasoningEffort"] = cfg.reasoningEffort
    if cfg.reasoningEfforts:
        data["reasoningEfforts"] = list(cfg.reasoningEfforts)
    if cfg.loopMax is not None:
        data["loopMax"] = cfg.loopMax
    if cfg.hotTailKB is not None:
        data["hotTailKB"] = cfg.hotTailKB
    if cfg.contextTokens is not None:
        data["contextTokens"] = cfg.contextTokens
    return data
