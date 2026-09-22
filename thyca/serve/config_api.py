"""Config/onboarding/provider endpoints (split from server.py).

Functions take the handler duck-typed (``_json`` / ``_read_json``) plus the
config file path, so this module never imports the handler class.
"""
from __future__ import annotations

import sys
from pathlib import Path

from thyca import __version__
from thyca.app.onboarding import (
    ProviderProbeError,
    provider_ready,
    test_provider_api,
    validate_provider,
)
from thyca.config import ConfigError, config_schema, load, save


def _config_values(cfg) -> dict:
    """Config as UI values; API keys never leave the server."""
    values = cfg.to_dict()
    providers = values.get("providers")
    if isinstance(providers, dict):
        for entry in providers.values():
            if isinstance(entry, dict):
                entry["apiKey"] = ""
    return values


def _config_meta(cfg) -> dict:
    """Non-secret status the settings panel can show (validity via verify)."""
    stored = {pid: bool(entry.apiKey) for pid, entry in cfg.providers.items()}
    default = cfg.providers.get(cfg.defaultProvider)
    return {"hasApiKey": bool(default.apiKey) if default else False, "providers": stored}


def _merge_saved_key(raw: dict, cfg) -> dict:
    """Empty providers[id].apiKey in the payload keeps that provider's stored key.

    Keys never cross providers: an unknown id with an empty key merges to None
    (env fallback at call time), never to another provider's secret.
    """
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return raw
    merged = dict(providers)
    for pid, entry in providers.items():
        if not isinstance(entry, dict) or entry.get("apiKey") != "":
            continue
        stored = cfg.providers.get(pid)
        updated = dict(entry)
        updated["apiKey"] = stored.apiKey if stored is not None else None
        merged[pid] = updated
    raw = dict(raw)
    raw["providers"] = merged
    return raw


def _parse_config_payload(payload: dict, cfg):
    # Lazy: _parse_dict is M3-private; keep the import local (no top-level
    # edge toward config internals) until M3 offers a public API (TASK-015).
    from thyca.config import _parse_dict

    return _parse_dict(_merge_saved_key(payload, cfg))


def _load_config(config_file: Path | None):
    try:
        return load(config_file) if config_file else load()
    except ConfigError:
        return None


def config_status(handler, config_file: Path | None) -> None:
    cfg = _load_config(config_file)
    ready = cfg is not None and provider_ready(cfg)
    handler._json(200, {"ready": ready, "version": __version__})


def config_get(handler, config_file: Path | None) -> None:
    cfg = _load_config(config_file)
    if cfg is None:
        handler._json(503, {"error": "config unavailable"})
        return
    handler._json(
        200,
        {
            "schema": config_schema(),
            "values": _config_values(cfg),
            "meta": _config_meta(cfg),
        },
    )


def config_post(handler, config_file: Path | None) -> None:
    try:
        payload = handler._read_json()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    cfg = _load_config(config_file)
    if cfg is None:
        handler._json(503, {"error": "config unavailable"})
        return
    try:
        updated = _parse_config_payload(payload, cfg)
        save(updated, config_file) if config_file else save(updated)
    except ConfigError as exc:
        handler._json(422, {"error": str(exc)})
        return
    handler._json(200, {"ok": True, "ready": provider_ready(updated)})


def onboarding_verify(handler, config_file: Path | None) -> None:
    try:
        payload = handler._read_json()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    base_url = payload.get("baseUrl")
    if not isinstance(base_url, str) or not base_url.strip():
        handler._json(400, {"error": "invalid baseUrl"})
        return
    provider_id = payload.get("providerId")
    if provider_id is not None and (
        not isinstance(provider_id, str) or not provider_id.strip()
    ):
        handler._json(400, {"error": "invalid providerId"})
        return
    api_key = payload.get("apiKey")
    if not isinstance(api_key, str) or not api_key.strip():
        cfg = _load_config(config_file)
        if cfg is None:
            handler._json(503, {"error": "config unavailable"})
            return
        try:
            if provider_id:
                entry = cfg.providers.get(provider_id.strip())
                if entry is None:
                    handler._json(404, {"error": "provider not found"})
                    return
                api_key = entry.api_key()
            else:
                api_key = cfg.provider.api_key()
        except ConfigError:
            handler._json(422, {"error": "chưa có API key"})
            return
    try:
        models = validate_provider(base_url.strip(), api_key)
    except ProviderProbeError as exc:
        handler._json(422, {"error": str(exc)})
        return
    handler._json(200, {"models": models, "apiKeyOk": True})


def providers_test(handler, config_file: Path | None) -> None:
    try:
        payload = handler._read_json()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    cfg = _load_config(config_file)
    if cfg is None:
        handler._json(503, {"error": "config unavailable"})
        return
    provider_id = payload.get("providerId", cfg.defaultProvider)
    if not isinstance(provider_id, str) or not provider_id.strip():
        handler._json(400, {"error": "invalid providerId"})
        return
    provider_id = provider_id.strip()
    entry = cfg.providers.get(provider_id)
    if entry is None:
        handler._json(404, {"error": "provider not found"})
        return
    model = payload.get("model") or cfg.defaultModel
    if not isinstance(model, str) or not model.strip():
        handler._json(400, {"error": "invalid model"})
        return
    model = model.strip()
    base_url = entry.baseUrl
    registered = cfg.models.get(model)
    if registered is not None and registered.baseUrl:
        # Legacy per-model endpoint wins, exactly like the turn path.
        base_url = registered.baseUrl
    try:
        key = entry.api_key()
    except ConfigError:
        print(
            f"provider test provider={provider_id} model={model} "
            "ok=false error=no api key",
            file=sys.stderr,
            flush=True,
        )
        handler._json(422, {"error": "chưa có API key"})
        return
    try:
        result = test_provider_api(entry.api, base_url, key, model)
    except ProviderProbeError as exc:
        print(
            f"provider test provider={provider_id} model={model} "
            f"ok=false error={exc}",
            file=sys.stderr,
            flush=True,
        )
        handler._json(422, {"error": str(exc)})
        return
    print(
        f"provider test provider={provider_id} model={result['model']} "
        f"ok=true latency_ms={result['latency_ms']}",
        file=sys.stderr,
        flush=True,
    )
    handler._json(
        200,
        {
            "ok": True,
            "providerId": provider_id,
            "model": result["model"],
            "latencyMs": result["latency_ms"],
        },
    )
