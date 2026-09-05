"""Config service tests — TASK-302 verification."""

from __future__ import annotations

import json
from dataclasses import replace
import re
import stat
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
    TimelineCfg,
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
    raw["provider"]["apiKey"] = "json-secret"
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
    raw["provider"]["apiKeyEnv"] = "MY_KEY"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load(p)
    assert cfg.provider.apiKeyEnv == "MY_KEY"
    monkeypatch.delenv("MY_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_KEY not set"):
        cfg.provider.api_key()
    monkeypatch.setenv("MY_KEY", "secret")
    assert cfg.provider.api_key() == "secret"


def test_invalid_limits_type_is_config_error(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["limits"]["loopMax"] = "ten"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="limits.loopMax must be an integer"):
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

    monkeypatch.setattr("thyca.config.FileLock", BrokenLock)
    p = tmp_path / "config.json"
    with pytest.raises(ConfigError, match="cannot lock"):
        save(default_config(), p)
    assert not p.exists()


def test_config_rejects_wrong_field_types(tmp_path: Path) -> None:
    cases = [
        ({"provider": {"baseUrl": None}}, "provider.baseUrl"),
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
    cfg = Config(provider=ProviderCfg(model="gpt-4o"))
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


def test_model_limits_override_global(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["provider"]["model"] = "special"
    raw["provider"]["reasoningEffort"] = "low"
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
    plain = replace(cfg, provider=replace(cfg.provider, model="plain"))
    assert plain.effective_provider().reasoningEffort == "low"
    assert plain.effective_limits().loopMax == 10


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

    import thyca.config as cfgmod

    monkeypatch.setenv("HOME", str(tmp_path))
    guide = cfgmod.write_config_guide()
    assert guide is not None
    target = tmp_path / ".thyca" / "read_after_config.md"
    assert target.exists()
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600
    packaged = Path(cfgmod.__file__).parent / "read_after_config.md"
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
    assert (tmp_path / ".thyca" / "read_after_config.md").exists()
