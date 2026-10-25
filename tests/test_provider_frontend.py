"""Provider single-editor flow, config-safety, and isolated browser checks."""
from __future__ import annotations

import json
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from test_team_frontend import _run, node  # noqa: F401 (fixture)
from test_webui_spa_frontend import WEBUI, _cdp_browser_result
from thyca.serve.static import content_type, safe_file

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_CSS = ROOT / "thyca/webui/ui/provider/providercss/provider.css"


def _config(models: dict | None = None) -> dict:
    return {
        "values": {
            "providers": {
                "alpha": {"baseUrl": "https://alpha.invalid/v1", "api": "openai_chat", "apiKey": "", "reasoningEffort": "high"},
                "beta": {"baseUrl": "https://beta.invalid/v1", "api": "openai_responses", "apiKey": "", "reasoningEffort": "low"},
            },
            "models": models or {
                "owned-alpha": {"provider": "alpha", "input": 0.1, "cache": 0.01, "output": 0.2},
                "owned-beta": {"provider": "beta", "input": 0.2, "cache": 0.02, "output": 0.4},
            },
            "defaultProvider": "alpha",
            "defaultModel": "global-only",
            "limits": {"loopMax": 80, "hotTailKB": 4, "contextTokens": 128000},
            "timeline": {"retentionDays": 30},
            "mcpServers": {"keep": {"url": "http://mock.invalid"}},
            "pricing": {"unowned/model": {"input": 3, "cache": 2, "output": 4}},
        },
        "meta": {"providers": {"alpha": True, "beta": True}},
        "schema": {"sections": [
            {"key": "provider", "fields": [{"key": "provider.reasoningEffort", "choices": ["low", "high", "max"], "default": "high"}]},
            {"key": "limits", "fields": [
                {"key": "limits.loopMax", "min": 1, "max": 200},
                {"key": "limits.hotTailKB", "min": 1, "max": 64},
                {"key": "limits.contextTokens", "min": 1000, "max": 2000000},
            ]},
        ]},
    }


def _mock_fetch(config: dict, *, models: list[str] | None = None, fail_config_post: bool = False,
                fail_initial_config_get: bool = False, fail_fresh_config_get: bool = False,
                fail_config_refresh: bool = False, fail_verify: bool = False,
                empty_models: bool = False, post_ready: bool = True) -> str:
    return f"""
        let serverConfig = {json.dumps(config)};
        const calls = [], configPosts = [], verifyPosts = [], testPosts = [], configSavedEvents = [];
        let configGets = 0;
        window.addEventListener('thyca:config-saved', event => configSavedEvents.push({{type: event.type, detail: event.detail ?? null}}));
        installFetch((url, opts) => {{
            const body = opts.body ? JSON.parse(opts.body) : null;
            calls.push({{url, method: opts.method || 'GET', body}});
            if (url === '/api/config' && opts.method === 'POST') {{
                configPosts.push(body);
                if ({str(fail_config_post).lower()}) return new Error('HTTP 500');
                const fetched = JSON.parse(JSON.stringify(body));
                const providers = Object.create(null);
                for (const id of Object.keys(fetched.providers || {{}})) {{
                    providers[id] = !!fetched.providers[id].apiKey || !!serverConfig.meta?.providers?.[id];
                    fetched.providers[id].apiKey = '';
                }}
                serverConfig = {{...serverConfig, values: fetched, meta: {{...serverConfig.meta, providers}}}};
                return {{ready: {str(post_ready).lower()}}};
            }}
            if (url === '/api/config') {{
                configGets++;
                if ({str(fail_initial_config_get).lower()} && configGets === 1) return new Error('initial config unavailable');
                if ({str(fail_fresh_config_get).lower()} && configGets === 2) return new Error('fresh config unavailable');
                if ({str(fail_config_refresh).lower()} && configPosts.length && configGets > 2) return new Error('post-save refresh unavailable');
                return serverConfig;
            }}
            if (url === '/api/onboarding/verify') {{ verifyPosts.push(body); return {"new Error('listing unavailable')" if fail_verify else "{models: []}" if empty_models else "{models: " + json.dumps(models or ['vendor/model-a', 'vendor/model-b']) + "}"}; }}
            if (url === '/api/providers/test') {{ testPosts.push(body); return new Error('HTTP 402'); }}
            if (url === '/api/bots') return {{bots: []}};
            if (url === '/api/sessions') return {{sessions: []}};
            if (url === '/api/config/status') return {{ready: true}};
            return {{}};
        }});
    """


def test_provider_page_has_one_editor_and_no_persist_before_explicit_save(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config(), models=['new/model'])}
        await __controller.ready; await tick(10);
        const markup = $('.workspace').innerHTML;
        const copy = {{titleMarkup: markup.includes('id="provider-title">Kết nối AI</h1>'), noEyebrow: !markup.includes('class="eyebrow"'), noSubtitle: !markup.includes('class="page-sub"'),
          namePlaceholder: markup.includes('placeholder="Không dấu, không khoảng trắng"'), noVisibleNameHint: !markup.includes('<small>Không dấu, không khoảng trắng.</small>'), nameHintSpacer: markup.includes('<small class="name-hint-space" aria-hidden="true"></small>'),
          loadButton: markup.includes(' Tải model</button>'), loadTitle: markup.includes('title="Chỉ tải danh sách, không gọi model hay lưu cấu hình."'),
          loadHintAbsent: !markup.includes('Tải chỉ kiểm tra danh sách'), discoveryHidden: $('#discovery-status').hidden, discoveryText: $('#discovery-status').textContent,
          keyTitle: markup.includes('title="Key được lưu trên máy này và gửi tới URL API khi kết nối."'), storedKeyNote: $('#pv-key-note').textContent, priceHint: markup.includes('Nhập đủ 3 giá hoặc để trống.'),
          priceTitle: markup.includes('title="Giá chỉ dùng để ước tính chi phí; API không trả giá này.'), advanced: markup.includes('<summary>Nâng cao</summary>'), currentDefault: $('#current-default').textContent}};
        openNewConnection();
        copy.newKeyNote = $('#pv-key-note').textContent; copy.newDiscoveryHidden = $('#discovery-status').hidden;
        $('#pv-id').value = 'fresh'; $('#pv-base-url').value = 'https://fresh.invalid/v1'; $('#pv-key').value = 'mock-fresh-key';
        await verifyQuick(); const listingStatus = $('#discovery-status').textContent; chooseModel('new/model');
        $('#m-in').value = '0.1'; $('#m-cache').value = '0.01'; $('#m-out').value = '0.2';
        return {{hasSingleForm: markup.includes('id="connection-form"') && !markup.includes('id="provider-form"') && !markup.includes('id="model-form"'),
                oneEditor: (markup.match(/class="paper-card/g) || []).length === 1,
                nativePicker: markup.includes('<select id="m-picker"'), primary: markup.includes('Lưu và dùng'),
                beforeSavePosts: configPosts.length, verifyPosts, testPosts, copy, listingStatus,
                model: selectedModelId(), advancedClosed: !$('#editor-advanced').open}};
    """, via="provider")
    assert out["copy"] == {
        "titleMarkup": True, "noEyebrow": True, "noSubtitle": True, "namePlaceholder": True, "noVisibleNameHint": True, "nameHintSpacer": True,
        "loadButton": True, "loadTitle": True, "loadHintAbsent": True,
        "discoveryHidden": True, "discoveryText": "", "keyTitle": True,
        "storedKeyNote": "Đã lưu key", "priceHint": True, "priceTitle": True, "advanced": True,
        "currentDefault": "Hiện tại: global-only · alpha", "newKeyNote": "", "newDiscoveryHidden": True,
    }, out["copy"]
    assert out == {
        "hasSingleForm": True, "oneEditor": True, "nativePicker": True, "primary": True,
        "beforeSavePosts": 0, "verifyPosts": [{"baseUrl": "https://fresh.invalid/v1", "apiKey": "mock-fresh-key"}],
        "listingStatus": "Đã tải 1 model.",
        "testPosts": [], "model": "new/model", "advancedClosed": True,
        "copy": {"titleMarkup": True, "noEyebrow": True, "noSubtitle": True, "namePlaceholder": True, "noVisibleNameHint": True, "nameHintSpacer": True,
                 "loadButton": True, "loadTitle": True, "loadHintAbsent": True,
                 "discoveryHidden": True, "discoveryText": "", "keyTitle": True,
                 "storedKeyNote": "Đã lưu key", "priceHint": True,
                 "priceTitle": True, "advanced": True, "currentDefault": "Hiện tại: global-only · alpha", "newKeyNote": "", "newDiscoveryHidden": True},
    }


def test_invalid_provider_name_stays_inline_and_does_not_start_network_work(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10); openNewConnection();
        $('#pv-id').value = 'bad name'; $('#pv-id').dispatch('input');
        const callsBefore = calls.length;
        const saved = await submitEditor(null, true);
        return {{saved, value: $('#pv-id').value, invalid: $('#pv-id').getAttribute('aria-invalid'),
                status: $('#editor-status').textContent, networkCalls: calls.length - callsBefore,
                configPosts: configPosts.length, verifyPosts: verifyPosts.length, testPosts: testPosts.length}};
    """, via="provider")
    assert out == {"saved": False, "value": "bad name", "invalid": "true",
                  "status": "Tên kết nối chỉ gồm chữ, số, _ và -.", "networkCalls": 0,
                  "configPosts": 0, "verifyPosts": 0, "testPosts": 0}


def test_new_connection_one_atomic_save_loads_fresh_config_and_keeps_default_provider(node: str) -> None:
    config = _config()
    config["values"]["limits"]["loopMax"] = 81
    out = _run(node, f"""
        {_mock_fetch(config, models=['fresh/model'])}
        await __controller.ready; await tick(10);
        serverConfig.values.limits.loopMax = 91; serverConfig.values.timeline.retentionDays = 21;
        const before = JSON.stringify(state.values);
        openNewConnection(); $('#pv-id').value = 'fresh'; $('#pv-base-url').value = 'https://fresh.invalid/v1'; $('#pv-key').value = 'mock-fresh-key';
        await verifyQuick(); chooseModel('fresh/model');
        $('#m-in').value = '0.5'; $('#m-cache').value = '0.05'; $('#m-out').value = '1.25';
        const notYetSaved = configPosts.length === 0 && JSON.stringify(state.values) === before;
        const saved = await submitEditor(null, true);
        return {{saved, notYetSaved, posts: configPosts, events: configSavedEvents, gets: configGets,
                after: state.values, meta: state.meta, keyInput: $('#pv-key').value,
                defaultLine: $('#current-default').textContent, calls: calls.map(call => call.url), testPosts,
                canonicalEditor: {{selected: selectedModelId(), picker: $('#m-picker').value, manual: state.manualModel,
                                  manualHidden: $('#manual-model-field').hidden, loaded: state.loadedModelId,
                                  provider: $('#m-provider').value, dirty: editorIsDirty(), saveStatus: $('#save-status').textContent}}}};
    """, via="provider")
    assert out["saved"] and out["notYetSaved"] and len(out["posts"]) == 1
    posted = out["posts"][0]
    assert posted["providers"]["fresh"]["baseUrl"] == "https://fresh.invalid/v1"
    assert posted["providers"]["fresh"]["apiKey"] == "mock-fresh-key"
    assert posted["models"]["fresh/model"] == {"provider": "fresh", "input": 0.5, "cache": 0.05, "output": 1.25}
    assert posted["defaultProvider"] == "alpha" and posted["defaultModel"] == "fresh/model"
    assert posted["models"]["owned-beta"] == {"provider": "beta", "input": 0.2, "cache": 0.02, "output": 0.4}
    assert posted["limits"]["loopMax"] == 91 and posted["timeline"]["retentionDays"] == 21
    assert posted["mcpServers"] == config["values"]["mcpServers"]
    assert posted["pricing"]["unowned/model"] == {"input": 3, "cache": 2, "output": 4}
    assert out["events"] == [{"type": "thyca:config-saved", "detail": None}]
    assert out["gets"] == 3 and out["keyInput"] == ""
    assert out["meta"]["providers"]["fresh"] is True and "mock-fresh-key" not in json.dumps(out["after"])
    assert "Hiện tại: fresh/model · fresh" == out["defaultLine"]
    assert out["calls"].count("/api/providers/test") == 0 and out["testPosts"] == []
    assert out["canonicalEditor"] == {"selected": "fresh/model", "picker": "model:fresh/model", "manual": False,
                                      "manualHidden": True, "loaded": "fresh/model", "provider": "fresh",
                                      "dirty": False, "saveStatus": "Đã lưu"}


def test_failed_manual_model_save_keeps_picker_text_and_price_draft(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config(), fail_config_post=True)}
        await __controller.ready; await tick(10); openNewConnection();
        $('#pv-id').value = 'manual-provider'; $('#pv-id').dispatch('input');
        $('#pv-base-url').value = 'https://manual.invalid/v1'; $('#pv-base-url').dispatch('input');
        $('#m-picker').value = ''; chooseModel('', {{manual: true}});
        $('#m-id').value = 'manual/model'; $('#m-id').dispatch('input');
        $('#m-in').value = '0.4'; $('#m-cache').value = '0.04'; $('#m-out').value = '0.8';
        const saved = await submitEditor(null, true);
        return {{saved, picker: $('#m-picker').value, manualOption: [...$('#m-picker').options].find(option => option.dataset.manual === 'true')?.value,
                manualVisible: !$('#manual-model-field').hidden, manualId: $('#m-id').value, selected: selectedModelId(),
                prices: [$('#m-in').value, $('#m-cache').value, $('#m-out').value], posts: configPosts.length}};
    """, via="provider")
    assert out == {"saved": False, "picker": "", "manualOption": "", "manualVisible": True,
                  "manualId": "manual/model", "selected": "manual/model", "prices": ["0.4", "0.04", "0.8"], "posts": 1}


def test_new_blank_key_does_not_borrow_default_key_for_listing_but_manual_model_works(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config(), empty_models=True)}
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value = 'new-id'; $('#pv-base-url').value = 'https://random.invalid/v1';
        const noRequest = await verifyQuick();
        const countAfterBlank = verifyPosts.length;
        $('#m-picker').value = ''; chooseModel('', {{manual: true}}); $('#m-id').value = 'manual/model';
        const saved = await submitEditor(null, false);
        return {{noRequest, countAfterBlank, status: $('#discovery-status').textContent, verifyPosts,
                saved, posts: configPosts, defaultModel: state.values.defaultModel, defaultProvider: state.values.defaultProvider}};
    """, via="provider")
    assert out["noRequest"] is False and out["countAfterBlank"] == 0
    assert out["status"] == "Nhập API key để tải model, hoặc nhập model ID." and out["verifyPosts"] == []
    assert out["saved"] and len(out["posts"]) == 1
    assert out["posts"][0]["models"]["manual/model"]["provider"] == "new-id"
    assert out["posts"][0]["defaultModel"] == "global-only" and out["defaultProvider"] == "alpha"


def test_existing_keyless_draft_uses_own_provider_and_typed_key_takes_precedence(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10);
        openExistingConnection('alpha'); $('#pv-base-url').value = 'https://alpha.invalid/v1';
        await verifyQuick();
        $('#pv-key').value = 'mock-typed-key'; await verifyQuick();
        return {{verifyPosts}};
    """, via="provider")
    assert out["verifyPosts"] == [
        {"baseUrl": "https://alpha.invalid/v1", "providerId": "alpha"},
        {"baseUrl": "https://alpha.invalid/v1", "apiKey": "mock-typed-key"},
    ]


def test_listing_failure_and_empty_response_keep_manual_entry_available(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config(), fail_verify=True)}
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value = 'manual'; $('#pv-base-url').value = 'https://manual.invalid/v1'; $('#pv-key').value = 'mock-key';
        const listing = await verifyQuick();
        $('#m-picker').value = ''; chooseModel('', {{manual: true}}); $('#m-id').value = 'known/manual';
        const saved = await submitEditor(null, false);
        return {{listing, status: $('#discovery-status').textContent, saved, posts: configPosts.length,
                id: configPosts[0]?.defaultModel, tests: testPosts.length}};
    """, via="provider")
    assert out["listing"] is False and out["status"] == "Không tải được model: listing unavailable"
    assert out["saved"] and out["posts"] == 1 and out["id"] == "global-only" and out["tests"] == 0
    empty = _run(node, f"""
        {_mock_fetch(_config(), empty_models=True)}
        await __controller.ready; await tick(10); openNewConnection();
        $('#pv-id').value = 'empty'; $('#pv-base-url').value = 'https://empty.invalid/v1'; $('#pv-key').value = 'mock-empty';
        const listed = await verifyQuick();
        return {{listed, status: $('#discovery-status').textContent, manualAvailable: !![...$('#m-picker').options].find(option => option.dataset.manual === 'true')}};
    """, via="provider")
    assert empty == {"listed": True, "status": "Không có model. Có thể nhập thủ công.", "manualAvailable": True}


def test_late_listing_is_ignored_after_url_key_or_identity_changes(node: str) -> None:
    out = _run(node, f"""
        let release;
        {_mock_fetch(_config())}
        const originalFetch = globalThis.fetch;
        installFetch((url, opts) => url === '/api/onboarding/verify' ? new Promise(resolve => {{ release = () => resolve({{models:['stale/model']}}); }}) : originalFetch(url, opts));
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value = 'draft'; $('#pv-base-url').value = 'https://one.invalid/v1'; $('#pv-key').value = 'mock-one';
        const pending = verifyQuick(); await tick(); const loadingStatus = $('#discovery-status').textContent;
        $('#pv-base-url').value = 'https://two.invalid/v1'; $('#pv-base-url').dispatch('input');
        release(); const result = await pending;
        return {{result, discovery: state.discovery, verifying: state.verifying, loadingStatus, status: $('#discovery-status').textContent,
                inputKey: $('#pv-key').value}};
    """, via="provider")
    assert out["result"] is False and out["discovery"] is None and out["verifying"] is False
    assert out["loadingStatus"] == "Đang tải model…" and out["status"] == "URL/key đã đổi. Tải lại model." and out["inputKey"] == "mock-one"


def test_name_changes_do_not_invalidate_an_inflight_model_listing(node: str) -> None:
    out = _run(node, f"""
        let release, requestPayload;
        {_mock_fetch(_config())}
        const originalFetch = globalThis.fetch;
        installFetch((url, opts) => url === '/api/onboarding/verify' ? new Promise(resolve => {{ requestPayload = JSON.parse(opts.body); release = () => resolve({{models:['late/model']}}); }}) : originalFetch(url, opts));
        await __controller.ready; await tick(10); openNewConnection();
        $('#pv-base-url').value = 'https://provider.invalid/v1'; $('#pv-key').value = 'mock-only';
        const pending = verifyQuick(); await tick();
        $('#pv-id').value = 'named-after-discovery'; $('#pv-id').dispatch('input');
        release(); const result = await pending;
        return {{result, discovery: state.discovery, posts: requestPayload, name: $('#pv-id').value}};
    """, via="provider")
    assert out["result"] is True and out["discovery"]["models"] == ["late/model"]
    assert out["name"] == "named-after-discovery" and out["posts"] == {"baseUrl": "https://provider.invalid/v1", "apiKey": "mock-only"}


def test_provider_effort_draft_survives_model_picker_render(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10); openExistingConnection('alpha');
        $('#pv-effort').value = 'max'; $('#pv-effort').dispatch('change');
        chooseModel('owned-alpha');
        const before = $('#pv-effort').value;
        const saved = await submitEditor(null, true);
        return {{before, saved, posted: configPosts[0]?.providers.alpha.reasoningEffort}};
    """, via="provider")
    assert out == {"before": "max", "saved": True, "posted": "max"}


def test_saved_custom_effort_values_survive_render_and_primary_save(node: str) -> None:
    config = _config({"custom/model": {"provider": "alpha", "input": 0.2, "cache": 0.02, "output": 0.4,
                                         "reasoningEfforts": ["low"], "reasoningEffort": "custom-model-effort"}})
    config["values"]["providers"]["alpha"]["reasoningEffort"] = "custom-provider-effort"
    out = _run(node, f"""
        {_mock_fetch(config)}
        await __controller.ready; await tick(10); openExistingConnection('alpha'); chooseModel('custom/model');
        const before = {{providerEffort: $('#pv-effort').value, modelEffort: $('#m-effort').value,
                        modelOptions: [...$('#m-effort').options].map(option => option.value)}};
        const saved = await submitEditor(null, true);
        return {{before, saved, postedProviderEffort: configPosts[0]?.providers.alpha.reasoningEffort,
                postedModelEffort: configPosts[0]?.models['custom/model'].reasoningEffort}};
    """, via="provider")
    assert out["before"]["providerEffort"] == "custom-provider-effort"
    assert out["before"]["modelEffort"] == "custom-model-effort"
    assert "custom-model-effort" in out["before"]["modelOptions"]
    assert out["saved"] and out["postedProviderEffort"] == "custom-provider-effort"
    assert out["postedModelEffort"] == "custom-model-effort"


def test_dirty_status_tracks_editor_and_confirmed_reload_resets_its_baseline(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10); const initial = $('#save-status').textContent;
        openNewConnection(); $('#pv-base-url').value = 'https://draft.invalid/v1'; $('#pv-base-url').dispatch('input');
        const dirty = $('#save-status').textContent;
        globalThis.confirm = () => false; const canceled = await reloadConfig(); const retained = $('#pv-base-url').value;
        globalThis.confirm = () => true; const reloaded = await reloadConfig();
        return {{initial, dirty, canceled, retained, reloaded, clean: editorIsDirty(), after: $('#save-status').textContent}};
    """, via="provider")
    assert out == {"initial": "Đã tải cấu hình", "dirty": "Chưa lưu thay đổi", "canceled": False,
                  "retained": "https://draft.invalid/v1", "reloaded": True, "clean": False, "after": "Đã tải cấu hình"}


def test_switching_existing_connections_clears_raw_key_and_preserves_canceled_draft(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10);
        $('#pv-key').value = 'mock-alpha';
        globalThis.confirm = () => false;
        const canceled = openExistingConnection('beta');
        const retained = $('#pv-key').value;
        globalThis.confirm = () => true;
        const switched = openExistingConnection('beta');
        return {{canceled, retained, switched, key: $('#pv-key').value, keyNote: $('#pv-key-note').textContent,
                keyPlaceholder: $('#pv-key').placeholder, id: $('#pv-id').value,
                url: $('#pv-base-url').value, connection: state.editingProvider}};
    """, via="provider")
    assert out == {"canceled": False, "retained": "mock-alpha", "switched": True, "key": "", "keyNote": "Đã lưu key",
                  "keyPlaceholder": "Để trống để giữ key", "id": "beta", "url": "https://beta.invalid/v1", "connection": "beta"}


def test_model_selection_is_idempotent_and_prompts_once_for_changed_model(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config(), models=['new/model', 'vendor/model-b'])}
        await __controller.ready; await tick(10);
        openExistingConnection('alpha'); await verifyQuick();
        chooseModel('new/model'); $('#m-in').value = '7'; $('#m-cache').value = '0.7'; $('#m-out').value = '9';
        let same = 0; globalThis.confirm = () => {{ same++; return true; }};
        chooseModel('new/model'); const sameDraft = [$('#m-in').value, $('#m-cache').value, $('#m-out').value];
        let changed = 0; globalThis.confirm = () => {{ changed++; return true; }};
        chooseModel('vendor/model-b');
        return {{same, sameDraft, changed, model: selectedModelId(), prices: [$('#m-in').value, $('#m-cache').value, $('#m-out').value]}};
    """, via="provider")
    assert out == {"same": 0, "sameDraft": ["7", "0.7", "9"], "changed": 1, "model": "vendor/model-b", "prices": ["", "", ""]}


def test_model_collision_requires_owner_connection_and_preserves_metadata(node: str) -> None:
    existing = {"shared/model": {"provider": "beta", "baseUrl": "https://legacy.invalid/v1", "input": 0.2,
                                  "cache": 0.02, "output": 0.8, "reasoningEfforts": ["low", "high"], "contextTokens": 32000}}
    out = _run(node, f"""
        {_mock_fetch(_config(existing), models=['shared/model'])}
        await __controller.ready; await tick(10);
        openExistingConnection('alpha'); await verifyQuick();
        const collision = chooseModel('shared/model');
        const disabledOwner = modelProviderId(state.values, 'shared/model');
        openExistingConnection('beta'); await verifyQuick(); chooseModel('shared/model');
        const before = {{provider: $('#m-provider').value, prices: [$('#m-in').value, $('#m-cache').value, $('#m-out').value], warning: $('#legacy-url-warning').textContent}};
        return {{collision, disabledOwner, before, selected: selectedModelId(), context: $('#m-context').value}};
    """, via="provider")
    assert out["collision"] is False and out["disabledOwner"] == "beta"
    assert out["before"] == {"provider": "beta", "prices": [0.2, 0.02, 0.8], "warning": "Model này đang giữ URL riêng https://legacy.invalid/v1; khi lưu sẽ không bị thay đổi."}
    assert out["selected"] == "shared/model" and out["context"] == 32000


def test_primary_save_does_not_silently_transfer_a_model_owned_by_another_provider(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10);
        openExistingConnection('alpha'); $('#m-picker').value = ''; chooseModel('', {{manual: true}}); $('#m-id').value = 'owned-beta';
        const result = await submitEditor(null, true);
        return {{result, posts: configPosts.length, status: $('#editor-status').textContent}};
    """, via="provider")
    assert out["result"] is False and out["posts"] == 0
    assert "Model này đã được lưu. Chọn model trong danh sách" in out["status"]


def test_advanced_transfer_is_explicit_and_preserves_unknown_model_metadata(node: str) -> None:
    existing = {"owned-beta": {"provider": "beta", "baseUrl": "https://legacy.invalid/v1", "input": 0.2,
                                 "cache": 0.02, "output": 0.4, "reasoningEfforts": ["low", "high"], "contextTokens": 64000}}
    out = _run(node, f"""
        {_mock_fetch(_config(existing))}
        await __controller.ready; await tick(10);
        openExistingConnection('beta'); chooseModel('owned-beta');
        $('#m-provider').value = 'alpha'; $('#m-provider').dispatch('change');
        globalThis.confirm = () => true;
        const saved = await submitEditor(null, true);
        return {{saved, posted: configPosts[0]?.models['owned-beta'], defaultModel: configPosts[0]?.defaultModel,
                defaultProvider: configPosts[0]?.defaultProvider}};
    """, via="provider")
    assert out["saved"]
    assert out["posted"] == {"provider": "alpha", "baseUrl": "https://legacy.invalid/v1", "input": 0.2, "cache": 0.02,
                              "output": 0.4, "reasoningEfforts": ["low", "high"], "contextTokens": 64000}
    assert out["defaultModel"] == "owned-beta" and out["defaultProvider"] == "alpha"


def test_duplicate_provider_from_fresh_snapshot_and_prepost_read_failure_do_not_post(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value = 'raced'; $('#pv-base-url').value = 'https://raced.invalid/v1'; $('#pv-key').value = 'mock-raced'; chooseModel('manual/model', {{manual: true}}); $('#m-id').value = 'manual/model';
        serverConfig.values.providers.raced = {{baseUrl:'https://external.invalid/v1',apiKey:'',reasoningEffort:'high'}};
        const duplicate = await submitEditor(null, true);
        const duplicatePosts = configPosts.length;
        const duplicateStatus = $('#editor-status').textContent;
        delete serverConfig.values.providers.raced;
        const oldFetch = globalThis.fetch;
        let failedFresh = false;
        installFetch((url, opts) => {{ if (url === '/api/config' && opts.method !== 'POST' && !failedFresh) {{ failedFresh=true; return new Error('fresh read unavailable'); }} return oldFetch(url,opts); }});
        const readFailure = await submitEditor(null, true);
        return {{duplicate, duplicatePosts, duplicateStatus,
                readFailure, totalPosts: configPosts.length, key: $('#pv-key').value}};
    """, via="provider")
    assert out["duplicate"] is False and out["duplicatePosts"] == 0
    assert "đã có" in out["duplicateStatus"]
    assert out["readFailure"] is False and out["totalPosts"] == 0 and out["key"] == "mock-raced"


def test_post_failure_preserves_draft_and_post_success_refresh_failure_is_committed(node: str) -> None:
    failure = _run(node, f"""
        {_mock_fetch(_config(), fail_config_post=True)}
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value = 'fail'; $('#pv-base-url').value = 'https://fail.invalid/v1'; $('#pv-key').value = 'mock-fail'; chooseModel('model/fail', {{manual: true}}); $('#m-id').value='model/fail';
        const before = JSON.stringify(state.values); const result = await submitEditor(null, true);
        return {{result, key: $('#pv-key').value, same: JSON.stringify(state.values) === before,
                posts: configPosts.length, events: configSavedEvents.length, message: $('#editor-status').textContent}};
    """, via="provider")
    assert failure["result"] is False and failure["key"] == "mock-fail" and failure["same"]
    assert failure["posts"] == 1 and failure["events"] == 0 and "HTTP 500" in failure["message"]
    committed = _run(node, f"""
        {_mock_fetch(_config(), fail_config_refresh=True, post_ready=False)}
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value = 'saved'; $('#pv-base-url').value = 'https://saved.invalid/v1'; $('#pv-key').value = 'mock-secret'; chooseModel('model/saved', {{manual: true}}); $('#m-id').value='model/saved';
        const result = await submitEditor(null, true);
        return {{result, posts: configPosts.length, events: configSavedEvents, input: $('#pv-key').value,
                canonical: JSON.stringify(state.values), status: $('#editor-status').textContent, save: $('#save-status').textContent}};
    """, via="provider")
    assert committed["result"] and committed["posts"] == 1 and committed["input"] == ""
    assert committed["events"] == [{"type": "thyca:config-saved", "detail": None}]
    assert "mock-secret" not in committed["canonical"] and "Đã lưu thành công" in committed["status"]
    assert "Cấu hình chưa sẵn sàng" in committed["status"] and committed["save"] == "Đã lưu"


def test_proto_names_are_safe_and_config_only_save_does_not_change_default_model(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value = '__proto__'; $('#pv-base-url').value = 'https://proto.invalid/v1'; $('#pv-key').value = 'mock-proto';
        chooseModel('__proto__', {{manual: true}}); $('#m-id').value = '__proto__';
        const saved = await submitEditor(null, false);
        openNewConnection(); $('#pv-id').value = 'constructor'; $('#pv-base-url').value = 'https://constructor.invalid/v1'; $('#pv-key').value = 'mock-constructor';
        chooseModel('constructor', {{manual: true}}); $('#m-id').value = 'constructor';
        const constructorSaved = await submitEditor(null, false);
        return {{saved, constructorSaved, hasProvider: Object.hasOwn(configPosts[0].providers, '__proto__'),
                hasModel: Object.hasOwn(configPosts[0].models, '__proto__'), defaultModel: configPosts[0].defaultModel,
                defaultProvider: configPosts[0].defaultProvider, ownMeta: Object.hasOwn(state.meta.providers,'__proto__'),
                stored: state.meta.providers['__proto__'], constructorProvider: Object.hasOwn(configPosts[1].providers,'constructor'),
                constructorModel: Object.hasOwn(configPosts[1].models,'constructor'), events: configSavedEvents.length}};
    """, via="provider")
    assert out == {"saved": True, "constructorSaved": True, "hasProvider": True, "hasModel": True, "defaultModel": "global-only",
                  "defaultProvider": "alpha", "ownMeta": True, "stored": True, "constructorProvider": True,
                  "constructorModel": True, "events": 2}


def test_model_prices_validate_all_or_none_and_unknown_config_survives_save(node: str) -> None:
    config = _config({"existing/model": {"provider": "alpha", "baseUrl": "https://legacy.invalid/v1", "input": 0.2,
                                          "cache": 0.02, "output": 0.4, "reasoningEfforts": ["low", "high"]}})
    out = _run(node, f"""
        {_mock_fetch(config)}
        await __controller.ready; await tick(10);
        chooseModel('existing/model'); $('#m-in').value='0.3'; $('#m-cache').value='0.03'; $('#m-out').value='0.6';
        const before = JSON.stringify(state.values);
        const result = await submitEditor(null, false);
        return {{result, posted: configPosts[0], before, warning: $('#legacy-url-warning').textContent}};
    """, via="provider")
    assert out["result"]
    posted = out["posted"]
    assert posted["models"]["existing/model"]["provider"] == "alpha"
    assert posted["models"]["existing/model"]["baseUrl"] == "https://legacy.invalid/v1"
    assert posted["models"]["existing/model"]["reasoningEfforts"] == ["low", "high"]
    assert posted["models"]["existing/model"]["input"] == 0.3 and posted["pricing"]["unowned/model"] == config["values"]["pricing"]["unowned/model"]
    assert "URL riêng" in out["warning"]
    invalid = _run(node, f"""
        {_mock_fetch(_config())}
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value='partial'; $('#pv-base-url').value='https://p.invalid/v1'; chooseModel('partial/model', {{manual:true}}); $('#m-id').value='partial/model';
        $('#m-in').value='1'; const result=await submitEditor(null,true);
        return {{result, posts:configPosts.length, invalid:$('#m-cache').getAttribute('aria-invalid'), status:$('#editor-status').textContent}};
    """, via="provider")
    assert invalid["result"] is False and invalid["posts"] == 0 and invalid["invalid"] == "true"
    assert "Điền đủ cả ba" in invalid["status"]


def test_async_config_commits_ignore_stale_probe_and_provider_delete_guard(node: str) -> None:
    out = _run(node, f"""
        let release;
        {_mock_fetch(_config())}
        const originalFetch=globalThis.fetch;
        installFetch((url,opts)=>url==='/api/providers/test'?(()=>{{testPosts.push(JSON.parse(opts.body));return new Promise(resolve=>{{release=()=>resolve({{model:'old/model',latencyMs:1}})}})}})():originalFetch(url,opts));
        await __controller.ready; await tick(10);
        const probe=testProvider('alpha'); await tick();
        await commitValues(fresh=>{{const values=JSON.parse(JSON.stringify(fresh.values)); values.defaultModel='owned-beta'; return values;}},{{note:'saved'}});
        release(); await probe;
        const guarded=await deleteProvider();
        return {{testPosts, result:state.testing.alpha, guarded, providers:Object.keys(state.values.providers)}};
    """, via="provider")
    assert out["testPosts"] == [{"providerId": "alpha", "model": "owned-alpha"}]
    assert "bỏ qua kết quả thử cũ" in out["result"]["text"]
    assert out["guarded"] is False and out["providers"] == ["alpha", "beta"]


def test_provider_test_result_stays_with_its_connection_while_switching(node: str) -> None:
    out = _run(node, f"""
        let release;
        {_mock_fetch(_config())}
        const originalFetch = globalThis.fetch;
        installFetch((url, opts) => url === '/api/providers/test' ? new Promise(resolve => {{ release = () => resolve({{model:'owned-alpha',latencyMs:4}}); }}) : originalFetch(url, opts));
        await __controller.ready; await tick(10);
        const pending = testProvider('alpha'); await tick(); openExistingConnection('beta');
        release(); await pending;
        const beta = {{editing: state.editingProvider, resultHidden: $('#test-result').hidden, alpha: state.testing.alpha?.text}};
        openExistingConnection('alpha');
        return {{beta, alphaVisible: !$('#test-result').hidden, alphaText: $('#test-result').textContent,
                betaResult: state.testing.beta || null}};
    """, via="provider")
    assert out["beta"] == {"editing": "beta", "resultHidden": True,
                           "alpha": "Gọi model thành công · owned-alpha · 4ms"}
    assert out["alphaVisible"] is True and "owned-alpha" in out["alphaText"] and out["betaResult"] is None


def test_cancel_reload_and_busy_save_preserve_single_editor_draft(node: str) -> None:
    out = _run(node, f"""
        let release;
        {_mock_fetch(_config())}
        const originalFetch=globalThis.fetch;
        installFetch((url,opts)=>url==='/api/config'&&opts.method==='POST'?new Promise(resolve=>{{configPosts.push(JSON.parse(opts.body)); release=()=>{{serverConfig.values=JSON.parse(opts.body); resolve({{ready:true}});}};}}):originalFetch(url,opts));
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value='draft'; $('#pv-base-url').value='https://draft.invalid/v1'; $('#pv-key').value='mock-draft';
        globalThis.confirm=()=>false; const reload=await reloadConfig(); const retained=$('#pv-key').value;
        chooseModel('draft/model',{{manual:true}}); $('#m-id').value='draft/model';
        const first=submitEditor(null,true); await tick(); const duplicate=await submitEditor(null,true);
        const busyReload=await reloadConfig(); release(); await first;
        return {{reload,retained,duplicate,busyReload,posts:configPosts.length,keyAfter:$('#pv-key').value,saving:state.saving}};
    """, via="provider")
    assert out == {"reload": False, "retained": "mock-draft", "duplicate": False, "busyReload": False,
                  "posts": 1, "keyAfter": "", "saving": False}


def test_manual_id_key_never_enters_location_history_or_storage(node: str) -> None:
    out = _run(node, f"""
        {_mock_fetch(_config(), models=['new/model'])}
        await __controller.ready; await tick(10);
        openNewConnection(); $('#pv-id').value='secret'; $('#pv-base-url').value='https://secret.invalid/v1'; $('#pv-key').value='mock-never-log';
        const promise=verifyQuick(); await promise;
        return {{verifyPosts, href:location.href, history:history.state, localStorage:JSON.stringify(localStorage._m), eventPayloads:configSavedEvents,
                markup:$('.workspace').innerHTML.includes('mock-never-log')}};
    """, via="provider")
    assert out["verifyPosts"][0]["apiKey"] == "mock-never-log"
    assert "mock-never-log" not in out["href"] and "mock-never-log" not in json.dumps(out["history"])
    assert "mock-never-log" not in out["localStorage"] and out["eventPayloads"] == []
    assert out["markup"] is False


def test_provider_styles_are_scoped_to_provider_shell() -> None:
    css = re.sub(r"/\*.*?\*/", "", PROVIDER_CSS.read_text(encoding="utf-8"), flags=re.S)
    selectors = [part.strip() for part in re.findall(r"([^{}]+)\{", css) if not part.strip().startswith("@media")]
    assert selectors and all(all(selector.strip().startswith(".provider-shell") for selector in group.split(",")) for group in selectors)


@pytest.mark.skipif(not shutil.which("google-chrome"), reason="Google Chrome is required for the isolated Provider browser check")
def test_provider_single_editor_journey_and_layout_at_desktop_and_mobile(tmp_path: Path) -> None:
    initial_script = r"""
(async()=>{const wait=async p=>{const end=Date.now()+15000;while(Date.now()<end){if(p())return;await new Promise(r=>setTimeout(r,25));}throw Error('timeout initial editor')};
const finish=v=>document.body.dataset.spaTestResult=JSON.stringify(v);try{
await wait(()=>window.ThycaRouter&&document.readyState==='complete'&&ThycaRouter.setupState()==='ready');await ThycaRouter.navigate('provider');await wait(()=>document.querySelector('#connection-form'));
await fetch('/api/test/reset');document.querySelector('#new-connection').click();
for(const [id,value] of [['#pv-id','browser-preview'],['#pv-base-url','http://127.0.0.1:1/v1'],['#pv-key','mock-browser-key']]){const el=document.querySelector(id);el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));}
const s=await fetch('/api/test/state').then(r=>r.json());finish({stage:'typed-before-load',form:!!document.querySelector('#connection-form'),name:document.querySelector('#pv-id').value,url:document.querySelector('#pv-base-url').value,posts:s.configPosts,tests:s.testPosts,verify:s.verifyPosts,oneCard:document.querySelectorAll('.connection-editor').length===1});
}catch(e){finish({error:String(e),stack:e.stack})}})();
"""
    selected_script = r"""
(async()=>{const wait=async p=>{const end=Date.now()+15000;while(Date.now()<end){if(p())return;await new Promise(r=>setTimeout(r,25));}throw Error('timeout model selection')};const finish=v=>document.body.dataset.spaTestResult=JSON.stringify(v);try{
await wait(()=>window.ThycaRouter&&document.readyState==='complete'&&ThycaRouter.setupState()==='ready');await ThycaRouter.navigate('provider');await wait(()=>document.querySelector('#connection-form'));await fetch('/api/test/reset');
document.querySelector('#new-connection').click();for(const [id,value] of [['#pv-id','browser-preview'],['#pv-base-url','http://127.0.0.1:1/v1'],['#pv-key','mock-browser-key']]){const el=document.querySelector(id);el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));}
document.querySelector('#v-submit').click();await wait(()=>document.querySelector('#m-picker option[data-model]'));
const picker=document.querySelector('#m-picker');const option=[...picker.options].find(item=>item.dataset.model===__MODEL_ID__);if(!option)throw Error('native option not found');picker.value=option.value;picker.dispatchEvent(new Event('change',{bubbles:true}));
for(const [id,value] of [['#m-in','0.5'],['#m-cache','0.05'],['#m-out','1.25']])document.querySelector(id).value=value;
const s=await fetch('/api/test/state').then(r=>r.json());const page=document.querySelector('.provider-shell'),scroll=page.querySelector('.page-scroll'),nav=page.querySelector('.page-nav');finish({stage:'loaded-selected-before-save',route:ThycaRouter.currentRoute(),root:location.pathname,selection:picker.value,model:document.querySelector('#m-picker').selectedOptions[0]?.dataset.model,prices:['#m-in','#m-cache','#m-out'].map(id=>document.querySelector(id).value),advancedClosed:!document.querySelector('#editor-advanced').open,configPosts:s.configPosts,testPosts:s.testPosts,verifyPosts:s.verifyPosts,cardCount:page.querySelectorAll('.paper-card').length,overflow:[document.documentElement,document.body,page,scroll,page.querySelector('.page-inner')].some(el=>el.scrollWidth>el.clientWidth+1),headerGap:nav.getBoundingClientRect().bottom<=scroll.getBoundingClientRect().top+1,primaryVisible:(()=>{const r=document.querySelector('#save-use').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight})()});
}catch(e){finish({error:String(e),stack:e.stack})}})();
"""
    save_script = r"""
(async()=>{const wait=async p=>{const end=Date.now()+15000;while(Date.now()<end){if(p())return;await new Promise(r=>setTimeout(r,25));}throw Error('timeout save')};const finish=v=>document.body.dataset.spaTestResult=JSON.stringify(v);try{
await wait(()=>window.ThycaRouter&&document.readyState==='complete'&&ThycaRouter.setupState()==='ready');await ThycaRouter.navigate('provider');await wait(()=>document.querySelector('#connection-form'));await fetch('/api/test/reset');
document.querySelector('#new-connection').click();for(const [id,value] of [['#pv-id','browser-preview'],['#pv-base-url','http://127.0.0.1:1/v1'],['#pv-key','mock-browser-key']]){const el=document.querySelector(id);el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));}
document.querySelector('#v-submit').click();await wait(()=>[...document.querySelector('#m-picker').options].some(item=>item.dataset.model===__MODEL_ID__));const picker=document.querySelector('#m-picker');const option=[...picker.options].find(item=>item.dataset.model===__MODEL_ID__);picker.value=option.value;picker.dispatchEvent(new Event('change',{bubbles:true}));
for(const [id,value] of [['#m-in','0.5'],['#m-cache','0.05'],['#m-out','1.25']])document.querySelector(id).value=value;
const before=await fetch('/api/test/state').then(r=>r.json());document.querySelector('#save-use').click();
const until=Date.now()+15000;let after;while(Date.now()<until){after=await fetch('/api/test/state').then(r=>r.json());if(after.configPosts.length)break;await new Promise(r=>setTimeout(r,25));}
if(!after?.configPosts.length)throw Error('no explicit save observed '+JSON.stringify({status:document.querySelector('#editor-status').textContent,provider:document.querySelector('#m-provider').value,picker:picker.value,selected:document.querySelector('#m-picker').selectedOptions[0]?.dataset.model,disabled:document.querySelector('#save-use').disabled,form:document.querySelector('#connection-form').checkValidity()}));await wait(()=>document.querySelector('#current-default').textContent.includes(__MODEL_ID__));finish({route:ThycaRouter.currentRoute(),before,after,posted:after.configPosts[0],defaultLine:document.querySelector('#current-default').textContent,key:document.querySelector('#pv-key').value,saveStatus:document.querySelector('#save-status').textContent,tests:after.testPosts,primaryVisible:(()=>{const r=document.querySelector('#save-use').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight})()});
}catch(e){finish({error:String(e),stack:e.stack})}})();
"""
    long_model = "vendor/" + "long-model-name-" * 5 + "x"
    selected_script = selected_script.replace("__MODEL_ID__", json.dumps(long_model))
    save_script = save_script.replace("__MODEL_ID__", json.dumps(long_model))
    state = {"config": _config(), "api_requests": [], "verify_posts": [], "config_posts": [], "test_posts": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None: pass
        def json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode(); self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/test/reset":
                for key in ("api_requests", "verify_posts", "config_posts", "test_posts"): state[key].clear()
                state["config"] = _config()
                self.json({"ok": True}); return
            if path == "/api/test/state":
                self.json({"verifyPosts": state["verify_posts"], "configPosts": state["config_posts"], "testPosts": state["test_posts"]}); return
            if path.startswith("/api/"):
                state["api_requests"].append(("GET", path))
                if path == "/api/config": payload = state["config"]
                elif path == "/api/config/status": payload = {"ready": True}
                elif path == "/api/bots": payload = {"bots": []}
                elif path == "/api/sessions": payload = {"sessions": []}
                else: payload = {}
                self.json(payload); return
            target = safe_file(WEBUI, path)
            if target is None: self.send_error(404); return
            body = target.read_bytes()
            if path == "/":
                scenario = urlsplit(self.path).query
                selected = selected_script if "stage=selected" in scenario else (save_script if "stage=save" in scenario else initial_script)
                body = body.replace(b"</head>", b"<script>window.__providerErrors=[];window.addEventListener('error',e=>window.__providerErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__providerErrors.push(String(e.reason)))</script></head>")
                body = body.replace(b"</body>", b"<script>" + selected.encode() + b"</script></body>")
            self.send_response(200); self.send_header("Content-Type", content_type(target)); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self) -> None:
            path = urlsplit(self.path).path; body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            state["api_requests"].append(("POST", path))
            if path == "/api/onboarding/verify":
                state["verify_posts"].append(body); self.json({"models": [long_model, "vendor/model-b"]})
            elif path == "/api/config":
                state["config_posts"].append(body); clean = json.loads(json.dumps(body))
                providers = {}
                for pid, entry in clean.get("providers", {}).items(): providers[pid] = bool(entry.get("apiKey")) or state["config"].get("meta", {}).get("providers", {}).get(pid, False); entry["apiKey"] = ""
                state["config"] = {**state["config"], "values": clean, "meta": {"providers": providers}}
                self.json({"ready": True})
            elif path == "/api/providers/test": state["test_posts"].append(body); self.json({"error": "HTTP 402"}, 402)
            else: self.json({})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    browser = shutil.which("google-chrome"); base = f"http://127.0.0.1:{server.server_port}/"
    results = {}
    try:
        initial = _cdp_browser_result(browser, base + "?stage=initial", 1280, Path("/tmp/thyca-provider-single-editor-initial.png"), tmp_path / "provider-initial")
        assert "error" not in initial, initial
        assert initial["stage"] == "typed-before-load" and initial["oneCard"] and initial["posts"] == [] and initial["tests"] == [] and initial["verify"] == []
        results["initial"] = initial
        selected = _cdp_browser_result(browser, base + "?stage=selected", 1280, Path("/tmp/thyca-provider-single-editor-selected.png"), tmp_path / "provider-selected")
        assert "error" not in selected, selected
        assert selected["stage"] == "loaded-selected-before-save" and selected["model"] == long_model
        assert selected["prices"] == ["0.5", "0.05", "1.25"] and selected["advancedClosed"]
        assert selected["cardCount"] == 1 and selected["configPosts"] == selected["testPosts"] == []
        assert selected["verifyPosts"] == [{"baseUrl": "http://127.0.0.1:1/v1", "apiKey": "mock-browser-key"}]
        assert not selected["overflow"] and selected["headerGap"] and selected["primaryVisible"]
        results["selected"] = selected
        for width, name, mobile in ((1280, "desktop", False), (320, "320", True), (375, "375", True)):
            saved = _cdp_browser_result(browser, base + "?stage=save", width, Path(f"/tmp/thyca-provider-single-editor-{name}.png"), tmp_path / f"provider-save-{name}", mobile=mobile)
            assert "error" not in saved, saved
            assert saved["after"]["verifyPosts"] == [{"baseUrl": "http://127.0.0.1:1/v1", "apiKey": "mock-browser-key"}]
            assert len(saved["after"]["configPosts"]) == 1 and saved["after"]["testPosts"] == []
            posted = saved["posted"]
            assert posted["providers"]["browser-preview"]["apiKey"] == "mock-browser-key"
            assert posted["models"][long_model] == {"provider": "browser-preview", "input": 0.5, "cache": 0.05, "output": 1.25}
            assert posted["defaultModel"] == long_model and posted["defaultProvider"] == "alpha"
            assert saved["key"] == "" and long_model in saved["defaultLine"] and saved["primaryVisible"]
            results[name] = saved
        artifacts = [Path(f"/tmp/thyca-provider-single-editor-{name}.png") for name in ("initial", "selected", "desktop", "320", "375")]
        for image in artifacts:
            for suffix in (".json", ".html"):
                artifact = image.with_suffix(suffix)
                if artifact.exists():
                    artifact.write_text(artifact.read_text(encoding="utf-8").replace("mock-browser-key", "[redacted]"), encoding="utf-8")
        evidence = json.dumps(results, ensure_ascii=False, indent=2).replace("mock-browser-key", "[redacted]")
        Path("/tmp/thyca-provider-single-editor-browser.json").write_text(evidence + "\n", encoding="utf-8")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
