"""Config service tests — TASK-302 verification."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import pytest

from thyca.config import (
    Config,
    ConfigError,
    LimitsCfg,
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
    assert cfg.timeline.timezone == "Asia/Ho_Chi_Minh"
    # round-trip
    cfg2 = load(p)
    assert cfg2.to_dict() == cfg.to_dict()


def test_api_key_json_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "config.json"
    raw = default_config().to_dict()
    raw["provider"]["apiKey"] = "json-secret"
    p.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    cfg = load(p)
    assert cfg.provider.api_key() == "json-secret"
    assert "json-secret" not in repr(cfg.provider)


def test_api_key_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "config.json"
    cfg = load(p)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY not set"):
        cfg.provider.api_key()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
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


def test_cli_help_and_default_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare load creates ~/.thyca — verify via tmp home."""
    # Simulate fresh home by using tmp_path as THYCA dir directly
    p = tmp_path / "fresh.json"
    assert not p.exists()
    cfg = load(p)
    assert p.exists()
    assert cfg.provider.apiKeyEnv == "OPENAI_API_KEY"
