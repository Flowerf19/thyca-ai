"""Config service tests — TASK-302 verification."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from thyca.config import (
    Config,
    ConfigError,
    LimitsCfg,
    ModelCfg,
    PricingCfg,
    ProviderCfg,
    ProviderEntry,
    _model_to_dict,
    default_config,
    load,
    save,
)


def test_default_config_valid(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    cfg = load(p)
    # load creates default when missing
    assert p.exists()
    assert cfg.provider.model == "gpt-4o-mini"
    # timezone follows the host system (fallback Asia/Ho_Chi_Minh)
    ZoneInfo(cfg.timeline.timezone)
    # round-trip
    cfg2 = load(p)
    assert cfg2.to_dict() == cfg.to_dict()


def test_api_key_json_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["providers"]["default"]["apiKey"] = "json-secret"
    p.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("THYCA_TOKEN", "env-secret")
    cfg = load(p)
    assert cfg.provider.api_key() == "json-secret"
    assert "json-secret" not in repr(cfg.provider)


def test_api_key_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "config.json"
    cfg = load(p)
    monkeypatch.delenv("THYCA_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="THYCA_TOKEN not set"):
        cfg.provider.api_key()
    monkeypatch.setenv("THYCA_TOKEN", "sk-test-123")
    assert cfg.provider.api_key() == "sk-test-123"


def test_api_key_env_custom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["providers"]["default"]["apiKeyEnv"] = "MY_KEY"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.provider.apiKeyEnv == "MY_KEY"
    monkeypatch.delenv("MY_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_KEY not set"):
        cfg.provider.api_key()
    monkeypatch.setenv("MY_KEY", "secret")
    assert cfg.provider.api_key() == "secret"


def test_api_key_bang_command_rejected_loudly() -> None:
    entry = ProviderEntry(apiKey="!echo sk-command")
    with pytest.raises(ConfigError, match="!command"):
        entry.api_key()


def test_invalid_limits_type_is_config_error(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["limits"]["loopMax"] = "ten"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"limits\.loopMax must be an integer"):
        load(p)


def test_default_path_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    load()
    assert stat.S_IMODE((home / ".thyca").stat().st_mode) == 0o700
    assert stat.S_IMODE((home / ".thyca" / "config.json").stat().st_mode) == 0o600


def test_save_custom_path_does_not_create_default_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    custom = tmp_path / "custom" / "config.json"
    monkeypatch.setenv("HOME", str(home))
    save(default_config(), custom)
    assert custom.exists()
    assert not (home / ".thyca").exists()


def test_save_fails_closed_when_lock_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenLock:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("lock unavailable")

    monkeypatch.setattr("thyca.config.store.FileLock", BrokenLock)
    p = tmp_path / "config.json"
    with pytest.raises(ConfigError, match="cannot lock"):
        save(default_config(), p)
    assert not p.exists()


def test_config_rejects_wrong_field_types(tmp_path: Path) -> None:
    cases = [
        ({"providers": {"default": {"baseUrl": None}}}, "providers"),
        ({"mcpServers": {"echo": {"command": ["python"]}}}, "command must be a non-empty string"),
        ({"mcpServers": {"echo": {"command": "python", "args": [1]}}}, "args must be a list of strings"),
        ({"timeline": {"timezone": 7}}, "timeline.timezone"),
    ]
    for patch, message in cases:
        p = tmp_path / f"{len(list(tmp_path.iterdir()))}.json"
        raw = default_config().to_dict()
        for key, value in patch.items():
            raw[key] = value
        p.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ConfigError, match=message):
            load(p)


def test_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load(p)


def test_invalid_timezone(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["timeline"]["timezone"] = "Not/AZone"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="not a valid IANA timezone"):
        load(p)


def test_mcp_servers_parse(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["mcpServers"] = {"echo": {"command": "python", "args": ["-m", "echo"], "env": {"FOO": "bar"}}}
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert "echo" in cfg.mcpServers
    assert cfg.mcpServers["echo"].command == "python"
    assert cfg.mcpServers["echo"].args == ["-m", "echo"]
    assert cfg.mcpServers["echo"].env == {"FOO": "bar"}


def test_mcp_server_keys_reject_invalid_charset(tmp_path: Path) -> None:
    for name in ("echo.1", "echo server"):
        p = tmp_path / f"{len(list(tmp_path.iterdir()))}.json"
        raw = default_config().to_dict()
        raw["mcpServers"] = {name: {"command": "python"}}
        p.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(
            ConfigError, match=re.escape(f"mcpServers[{name!r}] must match")
        ):
            load(p)


def test_mcp_server_keys_accept_letters_digits_underscore_hyphen(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["mcpServers"] = {
        "Echo_1": {"command": "python"},
        "a-b": {"command": "python"},
    }
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert set(cfg.mcpServers) == {"Echo_1", "a-b"}


def test_limits_validation() -> None:
    with pytest.raises(ConfigError):
        LimitsCfg(loopMax=0)
    with pytest.raises(ConfigError):
        LimitsCfg(hotTailKB=100)
    with pytest.raises(ConfigError):
        ProviderCfg(baseUrl="", apiKeyEnv="X", model="m")


def test_save_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    cfg = Config(defaultModel="gpt-4o")
    save(cfg, p)
    assert p.exists()
    cfg2 = load(p)
    assert cfg2.provider.model == "gpt-4o"


def test_pricing_parse_alias_and_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["pricing"] = {
        "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.6},
    }
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.pricing["gpt-4o-mini"] == PricingCfg(input=0.15, cache=0.075, output=0.6)
    saved = cfg.to_dict()
    assert "cached_input" not in saved["pricing"]["gpt-4o-mini"]
    assert saved["pricing"]["gpt-4o-mini"]["cache"] == 0.075


def test_models_parse_and_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["models"] = {
        "Qwen/Qwen3.8-Flash": {"input": 0.1, "cache": 0.01, "output": 0.4},
        "gpt-x@other": {"baseUrl": "https://other.api/v1", "input": 1.0, "cache": 0.1, "output": 2.0},
    }
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.models["Qwen/Qwen3.8-Flash"].baseUrl == ""
    assert cfg.models["gpt-x@other"].baseUrl == "https://other.api/v1"
    saved = cfg.to_dict()
    assert saved["models"]["gpt-x@other"] == {
        "baseUrl": "https://other.api/v1", "input": 1.0, "cache": 0.1, "output": 2.0,
    }


def test_effective_provider_applies_model_endpoint_and_reasoning() -> None:
    cfg = Config(
        providers={"default": ProviderEntry(reasoningEffort="high")},
        defaultModel="special",
        models={
            "special": ModelCfg(
                baseUrl="https://other.example/v1", reasoningEffort="low"
            )
        },
    )

    effective = cfg.effective_provider()

    assert effective.baseUrl == "https://other.example/v1"
    assert effective.reasoningEffort == "low"


def test_model_limits_override_global(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["defaultModel"] = "special"
    raw["providers"]["default"]["reasoningEffort"] = "low"
    raw["limits"] = {"loopMax": 10, "hotTailKB": 4, "contextTokens": 8000}
    raw["models"] = {
        "special": {
            "input": 0,
            "cache": 0,
            "output": 0,
            "reasoningEffort": "high",
            "loopMax": 50,
            "hotTailKB": 8,
            "contextTokens": 128000,
        },
        "plain": {"input": 0, "cache": 0, "output": 0},
    }
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.effective_provider().reasoningEffort == "high"
    assert cfg.effective_limits().loopMax == 50
    assert cfg.effective_limits().hotTailKB == 8
    assert cfg.effective_limits().contextTokens == 128000
    saved = cfg.to_dict()["models"]["special"]
    assert saved["loopMax"] == 50
    assert "loopMax" not in cfg.to_dict()["models"]["plain"]
    plain = replace(cfg, defaultModel="plain")
    assert plain.effective_provider().reasoningEffort == "low"
    assert plain.effective_limits().loopMax == 10


def test_model_reasoning_efforts_thinking_map(tmp_path: Path) -> None:
    """models[].reasoningEfforts declares the model's own thinking levels."""
    cfg = ModelCfg(
        input=0,
        cache=0,
        output=0,
        reasoningEffort="medium",
        reasoningEfforts=("minimal", "low", "medium", "high", "max"),
    )
    assert cfg.reasoningEfforts == ("minimal", "low", "medium", "high", "max")
    data = _model_to_dict(cfg)
    assert data["reasoningEfforts"] == ["minimal", "low", "medium", "high", "max"]
    # effort outside the declared map is rejected
    with pytest.raises(ConfigError, match="not in"):
        ModelCfg(input=0, cache=0, output=0, reasoningEffort="ultra", reasoningEfforts=("low", "max"))
    # no map declared → effort must be one of REASONING_EFFORTS
    with pytest.raises(ConfigError, match="reasoningEffort must be one of"):
        ModelCfg(input=0, cache=0, output=0, reasoningEffort="ultra")
    # duplicate levels are rejected
    with pytest.raises(ConfigError, match="duplicates"):
        ModelCfg(input=0, cache=0, output=0, reasoningEfforts=("low", "low"))


def test_model_limits_reject_out_of_range(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["models"] = {"m": {"input": 0, "cache": 0, "output": 0, "loopMax": 999}}
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="loopMax"):
        load(p)


def test_models_rejects_bad_baseurl_and_negative_price(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["models"] = {"m": {"baseUrl": "ftp://nope", "input": 0, "cache": 0, "output": 0}}
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="http:// or https://"):
        load(p)
    raw["models"] = {"m": {"input": -1, "cache": 0, "output": 0}}
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match=">= 0"):
        load(p)


def test_pricing_only_config_migrates_to_models(tmp_path: Path) -> None:
    """Legacy pricing entries surface as editable models in the UI."""
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["pricing"] = {"legacy-model": {"input": 0.5, "cache": 0.05, "output": 1.5}}
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.models["legacy-model"] == ModelCfg(input=0.5, cache=0.05, output=1.5)
    assert cfg.effective_pricing()["legacy-model"].input == 0.5


def test_effective_pricing_models_win_over_pricing(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["pricing"] = {"m": {"input": 1.0, "cache": 1.0, "output": 1.0}}
    raw["models"] = {"m": {"input": 2.0, "cache": 0.2, "output": 3.0}}
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.effective_pricing()["m"] == PricingCfg(input=2.0, cache=0.2, output=3.0)


def test_pricing_rejects_negative_and_missing(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["pricing"] = {"m": {"input": -1, "cache": 0, "output": 1}}
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="must be >= 0"):
        load(p)
    raw["pricing"] = {"m": {"input": 1, "output": 1}}
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="cache is required"):
        load(p)


def test_cli_help_and_default_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare load creates ~/.thyca — verify via tmp home."""
    # Simulate fresh home by using tmp_path as THYCA dir directly
    p = tmp_path / "fresh.json"
    assert not p.exists()
    cfg = load(p)
    assert p.exists()
    assert cfg.provider.apiKeyEnv == "THYCA_TOKEN"


def test_config_guide_written_on_ensure_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    import thyca
    import thyca.config as cfgmod

    monkeypatch.setenv("HOME", str(tmp_path))
    guide = cfgmod.write_config_guide()
    assert guide is not None
    target = tmp_path / ".thyca" / "read_before_config.md"
    assert target.exists()
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600
    packaged = Path(thyca.__file__).parent / "seeds" / "guides" / "read_before_config.md"
    assert target.read_text(encoding="utf-8") == packaged.read_text(encoding="utf-8")
    # idempotent
    before = target.read_text(encoding="utf-8")
    assert cfgmod.write_config_guide() == target
    assert target.read_text(encoding="utf-8") == before
    assert os.environ["HOME"] == str(tmp_path)


def test_ensure_default_writes_guide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import thyca.config as cfgmod

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "newhome.json"
    cfgmod.ensure_default(p)
    # guide lands in ~/.thyca even when config path is custom
    assert (tmp_path / ".thyca" / "read_before_config.md").exists()


def test_legacy_single_provider_migrates_to_default(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "provider": {
                    "baseUrl": "https://legacy.example/v1",
                    "apiKeyEnv": "THYCA_TOKEN",
                    "model": "legacy-model",
                    "reasoningEffort": "low",
                    "apiKey": "legacy-secret",
                },
                "models": {
                    "legacy-model": {"input": 0, "cache": 0, "output": 0},
                    "other-host": {
                        "baseUrl": "https://other.example/v1",
                        "input": 0,
                        "cache": 0,
                        "output": 0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = load(p)
    assert cfg.defaultProvider == "default"
    assert cfg.defaultModel == "legacy-model"
    assert cfg.providers["default"].baseUrl == "https://legacy.example/v1"
    assert cfg.providers["default"].api_key() == "legacy-secret"
    # save writes the new shape; legacy block is gone
    save(cfg, p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert "provider" not in raw
    assert raw["providers"]["default"]["baseUrl"] == "https://legacy.example/v1"
    assert raw["defaultModel"] == "legacy-model"
    # per-model baseUrl escape hatch still wins after migration
    assert cfg.effective_provider_for("other-host").baseUrl == "https://other.example/v1"
    assert cfg.effective_provider().baseUrl == "https://legacy.example/v1"


def test_legacy_provider_type_error_preserved(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"provider": {"baseUrl": None}}), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"provider\.baseUrl"):
        load(p)


def test_model_resolves_through_its_own_provider() -> None:
    cfg = Config(
        providers={
            "default": ProviderEntry(baseUrl="https://a.example/v1", apiKey="key-a"),
            "second": ProviderEntry(baseUrl="https://b.example/v1", apiKey="key-b"),
        },
        defaultModel="model-a",
        models={
            "model-a": ModelCfg(),
            "model-b": ModelCfg(provider="second"),
        },
    )
    assert cfg.provider_id_for("model-a") == "default"
    assert cfg.provider_id_for("model-b") == "second"
    first = cfg.effective_provider_for("model-a")
    assert (first.baseUrl, first.api_key(), first.model) == (
        "https://a.example/v1",
        "key-a",
        "model-a",
    )
    second = cfg.effective_provider_for("model-b")
    assert (second.baseUrl, second.api_key(), second.model) == (
        "https://b.example/v1",
        "key-b",
        "model-b",
    )
    # unregistered model falls back to the default provider (never 500s)
    assert cfg.effective_provider_for("ghost").baseUrl == "https://a.example/v1"


def test_providers_reject_bad_shape(tmp_path: Path) -> None:
    cases = [
        ({"providers": {}}, "providers must not be empty"),
        ({"providers": {"bad id": {}}}, "must match"),
        (
            {"providers": {"default": {}}, "defaultProvider": "ghost"},
            "defaultProvider 'ghost' is not in providers",
        ),
        (
            {
                "providers": {"default": {}},
                "models": {"m": {"provider": "ghost"}},
            },
            r"models\['m'\]\.provider 'ghost' does not exist",
        ),
    ]
    for patch, message in cases:
        raw = default_config().to_dict()
        raw.update(patch)
        p = tmp_path / f"{len(list(tmp_path.iterdir()))}.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ConfigError, match=message):
            load(p)


def test_provider_id_for_dangling_ref_falls_back_to_default() -> None:
    # Unreachable via load (rejected), but hand-built configs must not
    # label a provider the request would never go to.
    cfg = Config(models={"m": ModelCfg(provider="ghost")})
    assert cfg.provider_id_for("m") == "default"
    assert cfg.effective_provider_for("m").baseUrl == cfg.providers["default"].baseUrl


def test_save_splits_keys_to_auth_json(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    cfg = Config(
        providers={
            "default": ProviderEntry(baseUrl="https://a.example/v1", apiKey="sk-a"),
            "second": ProviderEntry(baseUrl="https://b.example/v1", apiKey="sk-b"),
        },
        defaultModel="m",
    )
    save(cfg, p)
    auth_path = tmp_path / "auth.json"
    assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
    auth_raw = json.loads(auth_path.read_text(encoding="utf-8"))
    assert auth_raw == {
        "providers": {"default": {"apiKey": "sk-a"}, "second": {"apiKey": "sk-b"}}
    }
    conf_raw = json.loads(p.read_text(encoding="utf-8"))
    assert "apiKey" not in conf_raw["providers"]["default"]
    assert "apiKey" not in conf_raw["providers"]["second"]
    assert "sk-a" not in p.read_text(encoding="utf-8")
    assert "sk-b" not in p.read_text(encoding="utf-8")
    assert load(p) == cfg


def test_auth_json_wins_over_inline_key(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "providers": {
                    "default": {"baseUrl": "https://a.example/v1", "apiKey": "sk-inline"}
                },
                "defaultProvider": "default",
                "defaultModel": "m",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "auth.json").write_text(
        json.dumps({"providers": {"default": {"apiKey": "sk-auth"}}}), encoding="utf-8"
    )
    assert load(p).providers["default"].api_key() == "sk-auth"
    save(load(p), p)
    assert (
        json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))["providers"][
            "default"
        ]["apiKey"]
        == "sk-auth"
    )
    assert "apiKey" not in json.loads(p.read_text(encoding="utf-8"))["providers"]["default"]


def test_inline_key_migrates_to_auth_on_save(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "providers": {
                    "default": {"baseUrl": "https://a.example/v1", "apiKey": "sk-inline"}
                },
                "defaultProvider": "default",
                "defaultModel": "m",
            }
        ),
        encoding="utf-8",
    )
    assert load(p).providers["default"].api_key() == "sk-inline"
    save(load(p), p)
    assert (
        json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))["providers"][
            "default"
        ]["apiKey"]
        == "sk-inline"
    )
    assert "apiKey" not in json.loads(p.read_text(encoding="utf-8"))["providers"]["default"]


def test_keyless_save_keeps_live_auth_keys(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    save(
        Config(
            providers={
                "default": ProviderEntry(
                    baseUrl="https://a.example/v1", apiKey="sk-live"
                )
            },
            defaultModel="m",
        ),
        p,
    )
    save(Config(), p)
    auth_raw = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    assert auth_raw["providers"]["default"]["apiKey"] == "sk-live"
    assert load(p).providers["default"].apiKey == "sk-live"


def test_malformed_auth_json_is_config_error(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    save(default_config(), p)
    (tmp_path / "auth.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"auth\.json"):
        load(p)


def test_missing_auth_json_means_no_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("THYCA_TOKEN", raising=False)
    p = tmp_path / "config.json"
    save(default_config(), p)
    (tmp_path / "auth.json").unlink()
    with pytest.raises(ConfigError, match="THYCA_TOKEN not set"):
        load(p).providers["default"].api_key()


def test_provider_api_field_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["providers"] = {
        "default": {"baseUrl": "https://a.example/v1", "api": "openai_responses"},
        "second": {"baseUrl": "https://b.example/v1"},
    }
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.providers["default"].api == "openai_responses"
    # missing api → default chat (zero behavior change for old configs)
    assert cfg.providers["second"].api == "openai_chat"
    saved = cfg.to_dict()
    assert saved["providers"]["default"]["api"] == "openai_responses"
    assert saved["providers"]["second"]["api"] == "openai_chat"


def test_provider_api_rejects_unknown_kind(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["providers"]["default"]["api"] = "graphql"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"api must be one of openai_chat/openai_responses"):
        load(p)


def test_effective_provider_carries_api() -> None:
    cfg = Config(
        providers={"default": ProviderEntry(api="openai_responses")},
        models={"m": ModelCfg(provider="default")},
    )
    for model in ("m", "unregistered"):
        assert cfg.effective_provider_for(model).api == "openai_responses"


def test_soft_timeout_default_parse_and_validate(tmp_path: Path) -> None:
    assert default_config().limits.softTimeoutS == 60
    assert default_config().to_dict()["limits"]["softTimeoutS"] == 60
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["limits"]["softTimeoutS"] = 10
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert load(p).limits.softTimeoutS == 10
    assert load(p).effective_limits().softTimeoutS == 10


def test_soft_timeout_rejects_bad_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        LimitsCfg(softTimeoutS=0)
    with pytest.raises(ConfigError):
        LimitsCfg(softTimeoutS=301)
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["limits"]["softTimeoutS"] = "sixty"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"limits\.softTimeoutS must be an integer"):
        load(p)


def test_soft_timeout_passes_through_model_overrides(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["limits"] = {
        "loopMax": 10, "hotTailKB": 4, "contextTokens": 8000, "softTimeoutS": 12,
    }
    raw["models"] = {"m": {"input": 0, "cache": 0, "output": 0, "loopMax": 50}}
    raw["defaultModel"] = "m"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.effective_limits().loopMax == 50
    assert cfg.effective_limits().softTimeoutS == 12


def test_atomic_write_temp_created_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4: the .tmp file is 0600 at creation, never world-readable."""
    from thyca.config.store import _atomic_write_json

    old_umask = os.umask(0)  # fully permissive: creation mode is fully visible
    try:
        temporary = tmp_path / "config.json.tmp"
        target = tmp_path / "config.json"
        creations: list[int] = []
        real_os_open = os.open

        def spy_os_open(path: object, flags: int, *args: object) -> int:
            fd = real_os_open(path, flags, *args)  # type: ignore[arg-type]
            if os.fspath(path) == os.fspath(temporary) and flags & os.O_CREAT:
                creations.append(stat.S_IMODE(os.fstat(fd).st_mode))
            return fd

        real_write_text = Path.write_text

        def spy_write_text(self: Path, *args: object, **kwargs: object) -> object:
            result = real_write_text(self, *args, **kwargs)  # type: ignore[arg-type]
            if self == temporary:
                creations.append(stat.S_IMODE(self.stat().st_mode))
            return result

        monkeypatch.setattr(os, "open", spy_os_open)
        monkeypatch.setattr(Path, "write_text", spy_write_text)
        _atomic_write_json(target, {"a": 1})
        assert creations, "temp creation was not observed"
        assert creations == [0o600]
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    finally:
        os.umask(old_umask)


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import os
import stat
import pytest
from pathlib import Path

def test_x7_atomic_write_is_0600_and_self_cleaning(tmp_path: Path) -> None:
    from thyca.config.store import atomic_write_text

    old_umask = os.umask(0)
    try:
        target = tmp_path / "w.json"
        atomic_write_text(target, '{"a": 1}\n')
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
        assert list(tmp_path.glob("*.tmp")) == []
        missing_parent = tmp_path / "nope" / "w.json"
        with pytest.raises(OSError):
            atomic_write_text(missing_parent, "x")
        assert not (tmp_path / "nope").exists()
    finally:
        os.umask(old_umask)


def test_x25_single_range_table() -> None:
    from thyca.config import config_schema
    from thyca.config.defaults import LIMIT_RANGES
    from thyca.config.limits import LimitsCfg

    assert LIMIT_RANGES["loopMax"] == (1, 200)
    schema = config_schema()
    limits = {f["key"]: f for f in schema["sections"][1]["fields"]}
    assert (limits["limits.loopMax"]["min"], limits["limits.loopMax"]["max"]) == (1, 200)
    assert (limits["limits.softTimeoutS"]["min"], limits["limits.softTimeoutS"]["max"]) == (1, 300)
    assert LimitsCfg().loopMax == 200


def test_x25_lock_path_single_home() -> None:
    from thyca.config import compat, store

    assert compat._lock_path is store._lock_path
    assert store._lock_path(Path("/x/config.json")) == Path("/x/config.json.lock")


def test_x25_optional_int_strict() -> None:
    from thyca.config import ConfigError
    from thyca.config.validation import _optional_int

    assert _optional_int(None, "n") is None
    assert _optional_int("", "n") is None
    assert _optional_int(7, "n") == 7
    with pytest.raises(ConfigError, match="must be an integer"):
        _optional_int(50.0, "n")
    with pytest.raises(ConfigError, match="must be an integer"):
        _optional_int(True, "n")


def test_x26_model_base_url_rule() -> None:
    from thyca.config import default_config, resolve_model_base_url
    from thyca.config.models import ModelCfg

    assert resolve_model_base_url("http://p", None) == "http://p"
    assert resolve_model_base_url("http://p", ModelCfg()) == "http://p"
    assert (
        resolve_model_base_url("http://p", ModelCfg(baseUrl="http://m")) == "http://m"
    )
    cfg = default_config()
    assert cfg.effective_provider_for("gpt-4o-mini").baseUrl.startswith("http")


def test_m1_falsy_model_fields_raise_not_coerce() -> None:
    from thyca.config import ConfigError, _parse_dict

    with pytest.raises(ConfigError, match="provider must be a string"):
        _parse_dict({"models": {"m": {"provider": 0}}})
    with pytest.raises(ConfigError, match="reasoningEffort must be a string"):
        _parse_dict({"models": {"m": {"reasoningEffort": False}}})


def test_m1_mcp_server_command_is_required() -> None:
    from thyca.config import McpServerCfg

    with pytest.raises(TypeError):
        McpServerCfg()  # type: ignore[call-arg]
    assert McpServerCfg(command="x").command == "x"


def test_m1_dict_section_and_labels_gone() -> None:
    import thyca.config.schema as schema

    assert not hasattr(schema, "_dict_section")
    for key in ("model", "mcpServers", "pricing", "input", "cache", "output"):
        assert key not in schema._LABELS


def test_m1_symlink_config_path_gets_tightened_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    from thyca.config import default_config, save

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    real = tmp_path / ".thyca" / "config.json"
    save(default_config(), real)
    os.chmod(tmp_path / ".thyca", 0o755)
    link = tmp_path / "link-config.json"
    link.symlink_to(real)
    save(default_config(), link)
    assert stat.S_IMODE((tmp_path / ".thyca").stat().st_mode) == 0o700


# Moved from tests/test_b2_contracts.py (B2 batch).
import warnings
from thyca.config import (
    Config,
    ConfigError,
    ModelCfg,
    ProviderCfg,
    ProviderEntry,
    default_config,
    load,
)

def test_f5_provider_entry_rejects_non_http_base_url() -> None:
    """Fails pre-fix: ftp baseUrl was accepted until the HTTP layer."""
    with pytest.raises(ConfigError, match=r"must start with http:// or https://"):
        ProviderEntry(baseUrl="ftp://x.example/v1")


def test_f5_provider_effort_must_be_global_and_name_the_fix() -> None:
    """Fails pre-fix: junk effort flowed into the llm payloads."""
    with pytest.raises(
        ConfigError,
        match=r"must be one of low/high/max \(or declare models\[\]\.reasoningEfforts\)",
    ):
        ProviderEntry(reasoningEffort="ultra")
    with pytest.raises(ConfigError, match="must be one of low/high/max"):
        ProviderCfg(reasoningEffort="ultra")


def test_f5_stored_junk_effort_fails_load(tmp_path: Path) -> None:
    """Fails pre-fix: load accepted the junk effort."""
    raw = default_config().to_dict()
    raw["providers"]["default"]["reasoningEffort"] = "ultra"
    target = tmp_path / "config.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="must be one of low/high/max"):
        load(target)


def test_f5_global_check_skipped_only_for_declared_own_set() -> None:
    """Pins new behavior: own-set models keep resolving custom levels."""
    cfg = Config(
        models={
            "m": ModelCfg(
                reasoningEffort="medium",
                reasoningEfforts=("minimal", "medium"),
            )
        }
    )
    resolved = cfg.effective_provider_for("m")
    assert resolved.reasoningEffort == "medium"
    assert resolved.reasoningEfforts == ("minimal", "medium")
    # No own set anywhere: the global check still bites.
    with pytest.raises(ConfigError, match="must be one of low/high/max"):
        ProviderCfg(reasoningEffort="medium")


def test_f6_unknown_keys_warn_listing_keys_but_load(tmp_path: Path) -> None:
    """Fails pre-fix: unknown keys were dropped with no warning at all."""
    raw = default_config().to_dict()
    raw["futureTop"] = {"whatever": True}
    raw["limits"]["loopMaxX"] = 5
    raw["providers"]["default"]["basUrl"] = "https://typo.example/v1"
    target = tmp_path / "config.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.warns(UserWarning, match="ignoring unknown config keys") as caught:
        cfg = load(target)
    message = str(caught[0].message)
    assert "futureTop" in message
    assert "limits.loopMaxX" in message
    assert "providers.default.basUrl" in message
    # NEVER reject: newer-config-on-older-binary must not break.
    assert cfg.limits.loopMax == default_config().limits.loopMax
    assert cfg.providers["default"].baseUrl == "https://api.openai.com/v1"


def test_f6_clean_config_loads_silent(tmp_path: Path) -> None:
    """Pins new behavior: known-only configs warn nothing (no spam)."""
    target = tmp_path / "config.json"
    target.write_text(
        json.dumps(default_config().to_dict()), encoding="utf-8"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        load(target)
