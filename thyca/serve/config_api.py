"""Config/onboarding/provider endpoints (split from server.py).

Functions take the handler duck-typed (``_json`` / ``_read_json``) plus the
config file path, so this module never imports the handler class.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from thyca import __version__
from thyca.app.onboarding import (
    ProviderProbeError,
    provider_ready,
    test_provider_api,
    validate_provider,
)
from thyca.config import (
    ConfigError,
    atomic_write_text,
    config_path,
    config_schema,
    load,
    merge_saved_keys,
    resolve_model_base_url,
    save,
)
from thyca.serve.errors import _with_json


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


def _parse_config_payload(payload: dict, cfg):
    # Lazy: _parse_dict is M3-private; keep the import local (no top-level
    # edge toward config internals) until M3 offers a public API (TASK-015).
    from thyca.config import _parse_dict

    return _parse_dict(merge_saved_keys(payload, cfg))


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
    """Config snapshot: values, metadata, and revision read atomically.

    The whole snapshot is taken under the coordination lock so a POST
    cannot slip in between the values read and the revision read (which
    would pair stale values with a current revision and defeat the
    conditional write). A snapshot whose revision cannot be established
    fails closed instead of sending values clients would then save back
    unconditionally.
    """
    from thyca.config.coordination import CoordinationError, hold

    root = _config_root(config_file)
    try:
        with hold(root):
            cfg = _load_config(config_file)
            if cfg is None:
                handler._json(503, {"error": "config unavailable"})
                return
            revision = _config_revision(config_file, root)
            if revision is None:
                handler._json(503, {"error": "config unavailable"})
                return
            handler._json(200, {
                "schema": config_schema(),
                "values": _config_values(cfg),
                "meta": _config_meta(cfg),
                "revision": revision,
            })
    except CoordinationError as exc:
        handler._json(503, {"error": str(exc)})


def _config_root(config_file: Path | None) -> Path:
    """Thyca data dir owning ``config.json`` (bot inventory lives here)."""
    if config_file is not None:
        return config_file.parent
    return config_path().parent


def _decode_key(data: bytes) -> bytes | None:
    """32 key bytes from hex text, else None (corrupt/unusable)."""
    if len(data) != 64:
        return None
    try:
        raw = bytes.fromhex(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    return raw if len(raw) == 32 else None


def _revision_key(root: Path) -> bytes | None:
    """Opaque machine-local HMAC key for config revisions.

    Published atomically via tmp-file + fsync + rename at 0600, so an
    interruption during first creation can never leave a torn key at the
    final path: a crash leaves at most tmp litter and the next call
    retries creation. Concurrent first uses in any process converge
    through the atomic publish (a lost create race simply reads the
    winner). A present-but-invalid file fails closed — delete it to
    recover — rather than weakening the scheme. Reads/writes of this
    file never take other locks; callers hold coordination across use.
    """
    import secrets as _secrets

    path = root / "config_revision.key"
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        pass
    except OSError:
        return None
    else:
        return _decode_key(data)
    try:
        atomic_write_text(path, _secrets.token_hex(32), mode=0o600)
    except FileExistsError:
        pass  # lost the creation race; read the winner below
    except OSError:
        return None
    try:
        with open(path, "rb") as handle:
            return _decode_key(handle.read())
    except OSError:
        return None


def _read_bytes_or_missing(path: Path) -> tuple[bytes | None, bool]:
    """Raw file bytes, distinguishing absent from unreadable.

    Returns ``(data, unreadable)``: missing files yield ``(None, False)``
    (auth state legitimately starts empty); any other read failure yields
    ``(None, True)`` so callers fail closed instead of hashing a lie.
    """
    try:
        return path.read_bytes(), False
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, True


def _config_revision(config_file: Path | None, root: Path) -> str | None:
    """Opaque revision covering config content plus effective key state.

    HMAC-SHA256 over ``config.json`` bytes plus the auth-file bytes with a
    machine-local key, so key-only edits change the revision while the
    value itself reveals nothing about any secret (no unsalted digests).
    Missing auth state hashes as empty; any unreadable input (or an
    unusable key) yields ``None`` so callers fail closed. Callers must
    hold the coordination lock across the snapshot this feeds.
    """
    import hashlib
    import hmac

    from thyca.config.auth import auth_path_for

    key = _revision_key(root)
    if key is None:
        return None
    target = config_file if config_file is not None else config_path()
    config_bytes, bad_config = _read_bytes_or_missing(target)
    auth_bytes, bad_auth = _read_bytes_or_missing(auth_path_for(target))
    if bad_config or bad_auth or config_bytes is None:
        return None
    return hmac.new(key, config_bytes + b"\x00" + (auth_bytes or b""),
                      hashlib.sha256).hexdigest()


def _model_references(root: Path, removed: list[str]) -> tuple[dict[str, list[str]], bool]:
    """Explicit bot references to ``removed`` models.

    Returns ``(references, unreadable)``. ``references`` maps each removed
    model id to the sorted bot ids explicitly configured to it (a bot set
    to ``"default"`` inherits the global default and is never counted).
    ``unreadable`` is True when the inventory cannot be established — a
    missing/unparsable card or settings file must never silently prove
    “unused”. Paused and system bots count like any other bot; only real
    bot directories are scanned (registry semantics via ``list()``).
    """
    import os

    from thyca.bots.registry import BotRegistry

    wanted = set(removed)
    refs = {model: [] for model in removed}
    # A genuinely absent inventory (fresh data dir) authorizes; an
    # inaccessible one must not. ``isdir`` is False for both, so probe
    # explicitly: only a true missing path means "no bots". Anything
    # else abnormal (permissions, a non-directory entry) fails closed.
    # Symlinked roots keep today's follow-the-link read behavior.
    if not os.path.isdir(Path(root) / "bots"):
        try:
            os.stat(Path(root) / "bots")
        except FileNotFoundError:
            return refs, False
        except OSError:
            return refs, True
        return refs, True
    try:
        docs = BotRegistry(root).list()
    except Exception:
        return refs, True
    for doc in docs:
        if not doc.ok or doc.card is None:
            return refs, True
        model = doc.card.model
        if model is not None and model.id != "default" and model.id in wanted:
            refs[model.id].append(doc.card.bot_id)
    for model in refs:
        refs[model].sort()
    return refs, False


def _check_model_removal(root: Path, removed: list[str]) -> tuple[int, dict] | None:
    """Blocking verdict for removing ``removed`` models, or None to proceed.

    Fail closed: unreadable inventory blocks with a generic 503 that asks
    the user to resolve the bot configuration and retry (never suggesting
    removal, never leaking paths). Referenced models block with 409 naming
    the affected bots so the user can move them first.
    """
    refs, unreadable = _model_references(root, removed)
    if unreadable:
        return 503, {
            "error": (
                "không kiểm tra được bot đang dùng model; "
                "kiểm tra lại cấu hình bot rồi thử lại"
            )
        }
    blocking = {model: bots for model, bots in refs.items() if bots}
    if not blocking:
        return None
    parts = []
    for model in sorted(blocking):
        names = blocking[model]
        shown = ", ".join(names[:5])
        if len(names) > 5:
            shown += f" và {len(names) - 5} bot khác"
        parts.append(f"{model} (bot: {shown})")
    return 409, {
        "code": "model_in_use",
        "error": "không thể xóa model đang được bot sử dụng: " + "; ".join(parts),
        "referencedBy": {model: blocking[model] for model in sorted(blocking)},
    }


def _apply_config_post(handler, config_file: Path | None, root: Path, payload: dict) -> None:
    # Opt-in conditional write: callers that merged onto a fresh GET send
    # its revision back. A mismatch means a newer config landed in
    # between — report 409 with no values/keys echoed instead of
    # silently replacing it. Callers without a revision keep the legacy
    # unconditional replace (residual: unprotected external POSTs are not
    # safe against each other and must not be assumed so).
    base_revision = payload.pop("baseRevision", None)
    if base_revision is not None:
        current = _config_revision(config_file, root)
        if current is None:
            handler._json(503, {"error": "config unavailable"})
            return
        if current != base_revision:
            handler._json(409, {
                "code": "config_changed",
                "error": (
                    "cấu hình đã thay đổi ở nơi khác; tải lại "
                    "rồi thử lại"
                ),
            })
            return
    cfg = _load_config(config_file)
    if cfg is None:
        handler._json(503, {"error": "config unavailable"})
        return
    try:
        updated = _parse_config_payload(payload, cfg)
    except ConfigError as exc:
        handler._json(422, {"error": str(exc)})
        return
    removed = [name for name in cfg.models if name not in updated.models]
    if removed:
        verdict = _check_model_removal(root, removed)
        if verdict is not None:
            status, body = verdict
            handler._json(status, body)
            return
    # Default-deletion rule (checked live under the same lock, so
    # concurrent default switches serialize): the currently registered
    # default cannot be removed via any config replacement — not by
    # abandoning it, not by switching to another default in the same
    # transaction, and not by keeping its name as an unregistered legacy
    # fallback. Switch the default in a separate operation first, then
    # delete. An unregistered legacy default is not a registry entry, so
    # keeping it never triggers this rule.
    old_default = cfg.defaultModel or ""
    if old_default and old_default in removed:
        handler._json(422, {
            "error": (
                "không thể xóa model mặc định: chọn model mặc định "
                "khác trước, rồi xóa"
            )
        })
        return
    try:
        save(updated, config_file) if config_file else save(updated)
    except ConfigError as exc:
        handler._json(422, {"error": str(exc)})
        return
    except Exception:
        traceback.print_exc(file=sys.stderr)
        handler._json(503, {"error": "config unavailable"})
        return
    handler._json(200, {"ok": True, "ready": provider_ready(updated)})


def config_post(handler, config_file: Path | None) -> None:
    """Full config replacement, guarded against unsafe model removal.

    The whole load–check–save runs under the model coordination lock so a
    bot model assignment cannot slip in between the reference check and
    the save (lock order: coordination → config file → per-bot).
    """
    from thyca.config.coordination import CoordinationError, hold

    def run(payload: dict) -> None:
        root = _config_root(config_file)
        try:
            with hold(root):
                _apply_config_post(handler, config_file, root, payload)
        except CoordinationError as exc:
            handler._json(503, {"error": str(exc)})

    _with_json(handler, run)


def onboarding_verify(handler, config_file: Path | None) -> None:
    def run(payload: dict) -> None:
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

    _with_json(handler, run)


def providers_test(handler, config_file: Path | None) -> None:
    def run(payload: dict) -> None:
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
        raw_model = payload.get("model")
        # Missing model falls back to the default; an explicit "" is a 400.
        model = cfg.defaultModel if raw_model is None else raw_model
        if not isinstance(model, str) or not model.strip():
            handler._json(400, {"error": "invalid model"})
            return
        model = model.strip()
        # Legacy per-model endpoint wins, exactly like the turn path.
        base_url = resolve_model_base_url(entry.baseUrl, cfg.models.get(model))
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

    _with_json(handler, run)
