"""Root ``Config`` aggregate: wire form plus per-model resolution policy."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .defaults import DEFAULT_PROVIDER_ID, DEFAULT_PROVIDER_MODEL
from .limits import LimitsCfg
from .mcp import McpServerCfg
from .models import ModelCfg, _model_to_dict
from .pricing import PricingCfg
from .providers import ProviderCfg, ProviderEntry, _provider_to_dict
from .timeline import TimelineCfg


def _default_providers() -> dict[str, ProviderEntry]:
    # Module-level (not a lambda) so Config stays picklable.
    return {DEFAULT_PROVIDER_ID: ProviderEntry()}


def resolve_model_base_url(entry_base_url: str, registered: ModelCfg | None) -> str:
    """Legacy per-model endpoint wins over its provider's URL.

    The one override rule for the turn path (:meth:`Config.effective_provider_for`)
    and the provider test endpoint: a model with its own baseUrl (0.8.2 escape
    hatch) calls that endpoint; the new UI does not write it, but old configs
    keep working."""
    if registered is not None and registered.baseUrl:
        return registered.baseUrl
    return entry_base_url


@dataclass(frozen=True)
class Config:
    providers: dict[str, ProviderEntry] = field(default_factory=_default_providers)
    defaultProvider: str = DEFAULT_PROVIDER_ID
    defaultModel: str = DEFAULT_PROVIDER_MODEL
    mcpServers: dict[str, McpServerCfg] = field(default_factory=dict)
    timeline: TimelineCfg = field(default_factory=TimelineCfg)
    limits: LimitsCfg = field(default_factory=LimitsCfg)
    models: dict[str, ModelCfg] = field(default_factory=dict)
    pricing: dict[str, PricingCfg] = field(default_factory=dict)

    @property
    def provider(self) -> ProviderCfg:
        """Resolved connection for the default model (compat for single-provider callers)."""
        return self.effective_provider()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "providers": {
                name: _provider_to_dict(entry) for name, entry in self.providers.items()
            },
            "defaultProvider": self.defaultProvider,
            "defaultModel": self.defaultModel,
            "mcpServers": {name: asdict(server) for name, server in self.mcpServers.items()},
            "timeline": asdict(self.timeline),
            "limits": asdict(self.limits),
        }
        if self.models:
            payload["models"] = {name: _model_to_dict(cfg) for name, cfg in self.models.items()}
        if self.pricing:
            payload["pricing"] = {name: asdict(cfg) for name, cfg in self.pricing.items()}
        return payload

    def effective_pricing(self) -> dict[str, PricingCfg]:
        """Pricing overlay for cost tracing: user models win over pricing."""
        merged = dict(self.pricing)
        for name, model in self.models.items():
            merged[name] = PricingCfg(input=model.input, cache=model.cache, output=model.output)
        return merged

    def provider_id_for(self, model_id: str) -> str:
        """Provider id a model resolves through (registered ref or default)."""
        registered = self.models.get(model_id)
        if (
            registered is not None
            and registered.provider
            and registered.provider in self.providers
        ):
            return registered.provider
        return self.defaultProvider

    def effective_provider_for(self, model_id: str) -> ProviderCfg:
        """Resolved connection for one model: its provider's URL/key plus model overrides."""
        entry = self.providers.get(self.provider_id_for(model_id))
        if entry is None:
            entry = self.providers.get(self.defaultProvider)
        if entry is None:
            entry = next(iter(self.providers.values()), ProviderEntry())
        effort = entry.reasoningEffort
        registered = self.models.get(model_id)
        base_url = resolve_model_base_url(entry.baseUrl, registered)
        own_set: tuple[str, ...] = ()
        if registered is not None:
            if registered.reasoningEffort:
                effort = registered.reasoningEffort
                # The model's own set governs only its own override; an
                # inherited entry effort stays on the global check.
                own_set = registered.reasoningEfforts
        return ProviderCfg(
            baseUrl=base_url,
            apiKeyEnv=entry.apiKeyEnv,
            apiKey=entry.apiKey,
            reasoningEffort=effort,
            api=entry.api,
            model=model_id,
            reasoningEfforts=own_set,
        )

    def effective_provider(self) -> ProviderCfg:
        """Provider slice for the default model (single-provider callers)."""
        return self.effective_provider_for(self.defaultModel)

    def effective_limits(self) -> LimitsCfg:
        """Limits for the active model; unset fields inherit the global block."""
        registered = self.models.get(self.defaultModel)
        if registered is None:
            return self.limits
        return LimitsCfg(
            loopMax=self.limits.loopMax if registered.loopMax is None else registered.loopMax,
            hotTailKB=self.limits.hotTailKB if registered.hotTailKB is None else registered.hotTailKB,
            contextTokens=(
                self.limits.contextTokens
                if registered.contextTokens is None
                else registered.contextTokens
            ),
            softTimeoutS=self.limits.softTimeoutS,
        )
