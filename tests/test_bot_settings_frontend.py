"""Node tests for per-bot model settings on the Profile page
(thyca/webui/ui/profile/js/profile.js).

Same classic-script bundle harness as test_team_frontend: ``_run``/``node``
are reused from that module so the bundles stay identical.
"""
from test_team_frontend import _run, node  # noqa: F401  (fixture)


def _profile_fetch(roster, config, settings=None):
    settings = settings if settings is not None else _SETTINGS
    return """
        installFetch((url, opts) => {
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') return %s;
            if (url === '/api/memory/stats') return { files: [
                { name: 'USER.md', content: 'user file' },
                { name: 'SOUL.md', content: 'soul file' },
                { name: 'IDENTITY.md', content: 'identity file' } ] };
            const m = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
            if (m) {
                const key = '/api/bots/' + m[1] + '/settings';
                if (opts.method === 'POST') {
                    const body = JSON.parse(opts.body);
                    window.__posted = window.__posted || [];
                    window.__posted.push({ url, body });
                    return { bot_id: m[1], model: body.model,
                             effective: { model: body.model.id === 'default' ? 'm2' : body.model.id,
                                          provider: 'card-provider', thinking: body.model.thinking || 'high' } };
                }
                if (SETTINGS[key]) return SETTINGS[key];
                const err = new Error('bot not found'); err.status = 404; throw err;
            }
            return {};
        });
    """ % (roster, config)


_ROSTER = (
    "[{ id: 'thyca', name: 'Thyca', role: 'Manager', system: true },"
    " { id: 'coder', name: 'Coder', role: 'Coder' }]"
)
_CONFIG = (
    "{ values: { defaultProvider: 'default', defaultModel: 'm2',"
    " providers: { default: { baseUrl: 'https://x.invalid/v1', reasoningEffort: 'high', apiKey: '' },"
    " 'card-provider': { baseUrl: 'https://card.invalid/v1', reasoningEffort: 'low', apiKey: '' } },"
    " models: { m1: { provider: 'card-provider', reasoningEfforts: ['low', 'custom'] }, m2: {} } },"
    " meta: { providers: { default: false, 'card-provider': false } },"
    " schema: { sections: ["
    "  { key: 'limits', label: 'L', fields: [] },"
    "  { key: 'provider', label: 'P', fields: [{ key: 'provider.reasoningEffort', label: 'Suy luận', choices: ['low', 'high', 'max'] }] },"  # noqa: E501
    "  ] } }"
)
_SETTINGS = (
    "{ '/api/bots/thyca/settings': { bot_id: 'thyca', model: { id: 'default', thinking: null },"
    "    effective: { model: 'm2', provider: 'default', thinking: 'high' } },"
    " '/api/bots/coder/settings': { bot_id: 'coder', model: { id: 'm1', thinking: 'custom' },"
    "    effective: { model: 'm1', provider: 'card-provider', thinking: 'custom' } } }"
)


def test_profile_shared_by_default(node) -> None:
    """No ?bot= shows the shared canonical USER/SOUL/IDENTITY cards with the
    'Hồ sơ chung' title, nothing selected, no bot model form."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const root = document.querySelector('#profile-root').innerHTML;
        return {
            title: $('#profile-title').textContent,
            shared: selectedBot, tab: state.tab,
            sharedBtnHidden: $('#profile-shared').hidden,
            hasUser: root.includes('file-USER'),
            hasSoul: root.includes('file-SOUL'),
            hasIdentity: root.includes('file-IDENTITY'),
            hasBotForm: root.includes('bot-settings-form'),
            hasBotHeading: root.includes('Bạn và'),
        };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["title"] == "Hồ sơ chung"
    assert out["shared"] == "" and out["tab"] == ""
    assert out["hasUser"] and out["hasSoul"] and out["hasIdentity"]
    assert out["hasBotForm"] is False
    assert out["hasBotHeading"] is False
    assert out["sharedBtnHidden"] is True


def test_profile_sidebar_select_scopes_coder(node) -> None:
    """Sidebar coder click stays on Profile (?bot=coder): only the coder
    dossier + model form show — no all-bot picker, no shared canonical docs."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        const replaced = [];
        const _orig = globalThis.history.replaceState.bind(globalThis.history);
        globalThis.history.replaceState = (...a) => { replaced.push(a[2]); return _orig(...a); };
        await tick(); await boot(); await tick(20);
        selectBot('coder');
        await tick(200);
        const root = document.querySelector('#profile-root').innerHTML;
        return {
            replaced, route: ctx.route, tab: state.tab, selected: state.tab, shared: selectedBot,
            title: $('#profile-title').textContent,
            hasBotForm: root.includes('bot-settings-form'),
            hasPicker: root.includes('bot-settings-list'),
            hasSharedDoc: root.includes('file-USER'),
            sharedBtnHidden: $('#profile-shared').hidden,
            fields: document.querySelector('#bot-settings-fields').innerHTML,
        };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["replaced"] and out["replaced"][-1] == "/"
    assert out["selected"] == "coder" and out["tab"] == "coder" and out["shared"] == "coder"
    assert out["route"] == {"page": "profile", "search": "?bot=coder", "hash": ""}
    assert out["title"] == "Bạn và Coder"
    assert out["hasBotForm"] is True and out["hasPicker"] is False
    assert out["hasSharedDoc"] is False
    assert out["sharedBtnHidden"] is False
    assert "Thyca" not in out["fields"] or "Coder" in out["fields"]
    assert '<option value="m1" selected>' in out["fields"]
    assert '<option value="custom" selected>' in out["fields"]
    assert "card-provider" in out["fields"]     # read-only derived provider


def test_profile_bot_forms_are_isolated(node) -> None:
    """Thyca's tab shows Thyca's own settings and persona, never coder's."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        const replaced = [];
        const _origRS = globalThis.history.replaceState.bind(globalThis.history);
        globalThis.history.replaceState = (...a) => { replaced.push(a[2]); return _origRS(...a); };
        await tick(); await boot(); await tick(20);
        selectBot('thyca');
        await tick(20);
        const root = document.querySelector('#profile-root').innerHTML;
        return {
            selected: state.tab,
            replaced: replaced, route: ctx.route,
            title: $('#profile-title').textContent,
            fields: document.querySelector('#bot-settings-fields').innerHTML,
            hasSharedDoc: root.includes('file-USER'),
        };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["selected"] == "thyca" and out["replaced"][-1] == "/"
    assert out["route"]["search"] == "?bot=thyca"
    assert out["title"] == "Bạn và Thyca"
    assert '<option value="default" selected>' in out["fields"]
    assert '<option value="m1" selected>' not in out["fields"]
    assert out["hasSharedDoc"] is False


def test_profile_deep_links_and_ghost(node) -> None:
    """?bot=coder / ?bot=thyca select directly; ?bot=ghost preserves the id
    with an honest error, disabled save and untouched URL — never Thyca."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.pathname = '/ui/profile/index.html';
        const probe = async (search) => {
            globalThis.location.search = search;
            state.tab = '';
            await boot(); await tick(20);
            const root = document.querySelector('#profile-root').innerHTML;
            return { selected: state.tab, shared: selectedBot,
                     search: ctx.route.search,
                     title: $('#profile-title').textContent,
                     root };
        };
        const a = await probe('?bot=coder');
        const b = await probe('?bot=thyca');
        const c = await probe('?bot=ghost');
        return { a, b, c };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["a"]["selected"] == "coder" and out["a"]["search"] == "?bot=coder"
    assert out["a"]["title"] == "Bạn và Coder"
    assert out["b"]["selected"] == "thyca" and "Thyca" in out["b"]["title"]
    assert out["c"]["selected"] == "ghost" and out["c"]["search"] == "?bot=ghost"
    assert out["c"]["shared"] == ""
    assert out["c"]["title"] == "Không tìm thấy bot"
    assert "Không tìm thấy bot" in out["c"]["root"]


def test_profile_shared_button_restores_catalog(node) -> None:
    """'Hồ sơ chung' clears ?bot, restores shared docs and never posts."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        const replaced = [];
        const _orig = globalThis.history.replaceState.bind(globalThis.history);
        globalThis.history.replaceState = (...a) => { replaced.push(a[2]); return _orig(...a); };
        await tick(); await boot(); await tick(20);
        const scoped = { botVisible: !!document.querySelector('#bot-model-card'),
                         search: ctx.route.search };
        $('#profile-shared').onclick();
        await tick();
        const root = document.querySelector('#profile-root').innerHTML;
        return { scoped, replaced, route: ctx.route,
                 selected: state.tab, shared: selectedBot,
                 title: $('#profile-title').textContent,
                 docs: root.includes('file-USER'),
                 noBotForm: !root.includes('bot-settings-form'),
                 posted: window.__posted || [] };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["scoped"]["botVisible"] is True
    assert out["replaced"][-1] == "/"
    assert out["selected"] == "" and out["shared"] == ""
    assert out["route"]["search"] == ""
    assert out["title"] == "Hồ sơ chung"
    assert out["docs"] is True and out["noBotForm"] is True
    assert out["posted"] == []


def test_profile_save_posts_settings_only(node) -> None:
    """Bot save POSTs only /api/bots/<id>/settings with the full replacement;
    canonical files and global config are untouched."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        const configPosts = [];
        const origFetch = globalThis.fetch;
        globalThis.fetch = async (url, opts) => {
            if (String(url).endsWith('/api/config') && opts && opts.method === 'POST') configPosts.push(1);
            if (String(url).endsWith('/api/memory/canonical') && opts && opts.method === 'POST') configPosts.push(1);
            return origFetch(url, opts);
        };
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        document.querySelector('#bot-model').value = 'default';
        document.querySelector('#bot-thinking').value = 'low';
        el.root.dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} });
        await tick();
        return { posted: window.__posted || [], configPosts,
                 dossier: el.root.innerHTML,
                 hint: document.querySelector('#bot-settings-hint').textContent,
                 enabled: !document.querySelector('#save-bot-settings').disabled };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["posted"] == [{
        "url": "/api/bots/coder/settings",
        "body": {"model": {"id": "default", "thinking": "low"}},
    }]
    assert out["configPosts"] == []
    assert out["hint"] == "Đã lưu." and out["enabled"] is True
    assert "model: default · suy nghĩ low" in out["dossier"]
    assert "model: m1 · suy nghĩ custom" not in out["dossier"]


def test_profile_provider_preview_follows_model(node) -> None:
    """Changing the model updates the read-only provider preview immediately
    (draft vs saved distinguished); unknown saved models get no fake route."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '?bot=thyca';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const before = document.querySelector('#bot-provider-preview').textContent;
        document.querySelector('#bot-model').value = 'm1';
        document.querySelector('#bot-model').dispatch('change', { target: document.querySelector('#bot-model') });
        await tick();
        const after = document.querySelector('#bot-provider-preview').textContent;
        const effortOpts = $$('#bot-thinking option').map(o => o.value);
        return { before, after, effortOpts };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert "default" in out["before"] and "chưa lưu" not in out["before"]
    assert "card-provider" in out["after"] and "chưa lưu" in out["after"]
    assert out["effortOpts"] == ["", "low", "custom"]


def test_profile_default_model_routes_provider(node) -> None:
    """Inheritance resolves through the configured defaultModel's registered
    provider, not defaultProvider."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        return { fields: document.querySelector('#bot-settings-fields').innerHTML,
                 preview: document.querySelector('#bot-provider-preview').textContent };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG.replace(
        "defaultModel: 'm2'", "defaultModel: 'm1'"))), via="profile", emit="profile")
    assert 'Theo mặc định chung (m1)' in out["fields"]
    assert "card-provider" in out["preview"] and " (dự kiến" not in out["preview"]


def test_profile_explicit_unregistered_default_uses_same_provider(node) -> None:
    """An explicit configured default needs no catalog registration, just like inheritance."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '?bot=thyca';
        await tick(); await boot(); await tick(20);
        const before = document.querySelector('#bot-provider-preview').textContent;
        const model = document.querySelector('#bot-model');
        model.value = 'unregistered-default';
        model.dispatch('change', { target: model });
        return { before, after: document.querySelector('#bot-provider-preview').textContent,
                 enabled: !document.querySelector('#save-bot-settings').disabled };
    """ % (_SETTINGS, _profile_fetch(_ROSTER, _CONFIG.replace(
        "defaultModel: 'm2'", "defaultModel: 'unregistered-default'"))),
        via="profile", emit="profile")
    assert out["before"] == out["after"] == "Provider: default"
    assert out["enabled"] is True


def test_profile_reload_config_keeps_scope_and_draft(node) -> None:
    """A catalog reload (new model m3) keeps the scope and the unsaved draft,
    and offers the new model."""
    out = _run(node, """
        const SETTINGS = %s;
        let catalogV2 = false;
        installFetch((url, opts) => {
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') return catalogV2 ? %s : %s;
            if (url === '/api/memory/stats') return { files: [{ name: 'USER.md', content: 'u' }] };
            const m = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
            if (m) {
                const key = '/api/bots/' + m[1] + '/settings';
                if (SETTINGS[key]) return SETTINGS[key];
                const err = new Error('bot not found'); err.status = 404; throw err;
            }
            return {};
        });
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        document.querySelector('#bot-model').value = 'm2';
        document.querySelector('#bot-thinking').value = 'high';
        catalogV2 = true;
        await refreshAll();
        await tick();
        return { selected: state.tab, search: globalThis.location.search,
                 model: document.querySelector('#bot-model').value,
                 thinking: document.querySelector('#bot-thinking').value,
                 fields: document.querySelector('#bot-settings-fields').innerHTML,
                 settingsKept: !!botCfg.settings };
    """ % (_SETTINGS, _ROSTER, _CONFIG.replace("m2: {}", "m2: {}, m3: {}"), _CONFIG),
        via="profile", emit="profile")
    assert out["selected"] == "coder" and out["search"] == "?bot=coder"
    assert out["model"] == "m2" and out["thinking"] == "high"  # draft preserved
    assert 'value="m3"' in out["fields"]  # new catalog model offered
    assert '<option value="m2" selected>' in out["fields"]
    assert '<option value="high" selected>' in out["fields"]
    assert out["settingsKept"] is True


def test_profile_stale_guards_and_save_twice(node) -> None:
    """Stale GET/POST never paint another bot; a duplicate submit while a save
    is in flight posts nothing; the second completed save posts again; the
    shared roundtrip blocks duplicates until the first op releases."""
    out = _run(node, """
        const SETTINGS = %s;
        const pending = [];
        installFetch((url, opts) => {
            if (String(url).endsWith('/settings') && !opts.method) {
                const gm = String(url).match(/^\/api\/bots\/([^/]+)\/settings$/);
                return { ok: true, status: 200, json: () => new Promise(r => pending.push({ bot: gm && gm[1], resolve: r, payload: null })) };
            }
            if (String(url).endsWith('/settings') && opts.method === 'POST') {
                const m = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
                const body = JSON.parse(opts.body);
                window.__posted = window.__posted || [];
                window.__posted.push({ url, body });
                return { ok: true, status: 200,
                         json: () => new Promise(r => pending.push({ bot: m && m[1], resolve: d => r(d), payload: body })) };
            }
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') return %s;
            if (url === '/api/memory/stats') return { files: [{ name: 'USER.md', content: 'u' }] };
            return {};
        });
        const eff = (bot, model) => ({ bot_id: bot, model,
            effective: { model: model.id, provider: 'card-provider', thinking: model.thinking || 'high' } });
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        const fireSubmit = () => el.root.dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} });
        await tick(); await boot(); await tick();
        // stale GET: switch away before the coder fetch resolves
        selectBot('coder');
        await tick();
        selectBot('thyca');
        await tick(20);
        pending.filter(p => !p.payload && p.bot === 'coder')
               .forEach(p => p.resolve(eff('coder', { id: 'm1', thinking: 'low' })));
        pending.filter(p => !p.payload && p.bot === 'thyca')
               .forEach(p => p.resolve(eff('thyca', { id: 'default', thinking: null })));
        await tick();
        const staleOk = state.tab === 'thyca'
            && !document.querySelector('#profile-root').innerHTML.includes('<option value="m1" selected>');
        // save twice on thyca: duplicate blocked, second completed posts again
        document.querySelector('#bot-model').value = 'default';
        document.querySelector('#bot-thinking').value = 'low';
        let first = fireSubmit();
        await tick();
        const blocked = document.querySelector('#save-bot-settings').disabled;
        fireSubmit();
        await tick();
        pending.filter(p => p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', p.payload)));
        await first; await tick();
        document.querySelector('#bot-thinking').value = 'max';
        const second = fireSubmit();
        await tick();
        pending.filter(p => p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', p.payload)));
        await second; await tick();
        const afterTwice = { posts: (window.__posted || []).filter(p => p.url.endsWith('/thyca/settings')).length,
                             still: state.tab,
                             hint: document.querySelector('#bot-settings-hint').textContent,
                             enabled: !document.querySelector('#save-bot-settings').disabled };
        // shared roundtrip: scoped save pending -> shared -> same bot blocks duplicate
        document.querySelector('#bot-model').value = 'm1';
        document.querySelector('#bot-thinking').value = 'custom';
        const third = fireSubmit();
        await tick();
        showShared();
        await tick();
        selectBot('thyca');
        await tick(20);
        const backBlocked = document.querySelector('#save-bot-settings').disabled;
        fireSubmit();
        await tick();
        pending.filter(p => p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', p.payload)));
        await third; await tick();
        // resolve the re-select GET, then the bot is usable again: fourth save works
        pending.filter(p => !p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', { id: 'default', thinking: 'low' })));
        await tick();
        document.querySelector('#bot-model').value = 'm1';
        document.querySelector('#bot-thinking').value = 'high';
        fireSubmit();
        await tick();
        pending.filter(p => p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', p.payload)));
        await tick();
        return { staleOk, blocked, afterTwice, backBlocked,
                 posts: window.__posted || [],
                 still: state.tab,
                 enabled: !document.querySelector('#save-bot-settings').disabled,
                 hint: document.querySelector('#bot-settings-hint').textContent };
    """ % (_SETTINGS, _ROSTER, _CONFIG), via="profile", emit="profile")
    assert out["staleOk"] is True
    assert out["blocked"] is True
    assert out["afterTwice"]["posts"] == 2  # first + second; duplicate posted nothing
    assert out["afterTwice"]["hint"] == "Đã lưu." and out["afterTwice"]["enabled"] is True
    assert out["backBlocked"] is True
    thycaPosts = [p for p in out["posts"] if p["url"] == "/api/bots/thyca/settings"]
    assert [p["body"] for p in thycaPosts] == [
        {"model": {"id": "default", "thinking": "low"}},
        {"model": {"id": "default", "thinking": "max"}},
        {"model": {"id": "m1", "thinking": "custom"}},
        {"model": {"id": "m1", "thinking": "high"}},
    ]
    assert out["still"] == "thyca" and out["enabled"] is True and out["hint"] == "Đã lưu."


def test_profile_draft_survives_stale_completion(node) -> None:
    """A stale coder POST resolving while thyca's unsaved draft is open must
    not repaint thyca's form; the draft then posts with its own values."""
    out = _run(node, """
        const SETTINGS = %s;
        const pending = [];
        installFetch((url, opts) => {
            if (String(url).endsWith('/settings') && !opts.method) {
                const gm = String(url).match(/^\/api\/bots\/([^/]+)\/settings$/);
                return { ok: true, status: 200, json: () => new Promise(r => pending.push({ bot: gm && gm[1], resolve: r, payload: null })) };
            }
            if (String(url).endsWith('/settings') && opts.method === 'POST') {
                const m = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
                const body = JSON.parse(opts.body);
                window.__posted = window.__posted || [];
                window.__posted.push({ url, body });
                return { ok: true, status: 200,
                         json: () => new Promise(r => pending.push({ bot: m && m[1], resolve: d => r(d), payload: body })) };
            }
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') return %s;
            if (url === '/api/memory/stats') return { files: [{ name: 'USER.md', content: 'u' }] };
            return {};
        });
        const eff = (bot, model) => ({ bot_id: bot, model,
            effective: { model: model.id, provider: 'card-provider', thinking: model.thinking || 'high' } });
        const fireSubmit = () => el.root.dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} });
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick();
        // settle coder's initial GET so its save can start
        pending.filter(p => !p.payload && p.bot === 'coder')
               .forEach(p => p.resolve(eff('coder', { id: 'm1', thinking: 'custom' })));
        await tick();
        // coder posts while thyca will be scoped with an unsaved draft
        const savingCoder = fireSubmit();
        await tick();
        selectBot('thyca');
        await tick(20);
        pending.filter(p => !p.payload && p.bot === 'thyca')
               .forEach(p => p.resolve(eff('thyca', { id: 'default', thinking: null })));
        await tick();
        document.querySelector('#bot-model').value = 'm2';
        document.querySelector('#bot-thinking').value = 'low';
        await tick();
        pending.filter(p => p.payload && p.bot === 'coder').forEach(p => p.resolve(eff('coder', p.payload)));
        await savingCoder; await tick();
        const draftKept = [document.querySelector('#bot-model').value, document.querySelector('#bot-thinking').value,
                           document.querySelector('#bot-settings-hint').textContent];
        const thycaSave = fireSubmit();
        await tick();
        pending.filter(p => p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', p.payload)));
        await thycaSave; await tick();
        return { draftKept, posts: window.__posted || [], still: state.tab,
                 enabled: !document.querySelector('#save-bot-settings').disabled,
                 hint: document.querySelector('#bot-settings-hint').textContent };
    """ % (_SETTINGS, _ROSTER, _CONFIG), via="profile", emit="profile")
    assert out["draftKept"][0:2] == ["m2", "low"]           # unsaved thyca draft survives
    assert out["draftKept"][2] == "" or "Đang lưu" not in out["draftKept"][2]
    thycaPosts = [p for p in out["posts"] if p["url"] == "/api/bots/thyca/settings"]
    assert [p["body"] for p in thycaPosts] == [{"model": {"id": "m2", "thinking": "low"}}]
    assert out["still"] == "thyca" and out["enabled"] is True and out["hint"] == "Đã lưu."


def test_profile_unknown_model_honest(node) -> None:
    """A saved model id missing from the catalog stays selected with an
    honest suffix; Save stays disabled until the user picks a valid id."""
    out = _run(node, """
        const SETTINGS = {
            '/api/bots/thyca/settings': { bot_id: 'thyca', model: { id: 'default', thinking: null },
                effective: { model: 'm2', provider: 'default', thinking: 'high' } },
            '/api/bots/coder/settings': { bot_id: 'coder', model: { id: 'retired-model', thinking: 'low' },
                effective: { model: 'm2', provider: 'default', thinking: 'low' } },
        };
        %s
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const sel = document.querySelector('#bot-model');
        sel.value = 'retired-model';
        sel.dispatch('change', { target: sel });
        await tick();
        const fields = document.querySelector('#bot-settings-fields').innerHTML;
        return { fields,
                 saveDisabled: document.querySelector('#save-bot-settings').disabled,
                 preview: document.querySelector('#bot-provider-preview').textContent };
    """ % _profile_fetch(_ROSTER, _CONFIG), via="profile", emit="profile")
    assert '<option value="retired-model" selected>' in out["fields"]
    assert "không còn trong danh mục" in out["fields"]
    assert 'id="bot-model-notice" hidden' not in out["fields"]
    assert out["saveDisabled"] is True
    assert out["preview"] == "Provider: — (model không còn trong danh mục)"


def test_profile_injection_safe_label(node) -> None:
    """A hostile defaultModel string renders escaped in both option text and
    the inherit label — no injected element or handler (real DOM parsing)."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const html = document.querySelector('#bot-settings-fields').innerHTML;
        return { html, img: html.includes('<img'), onerror: html.includes('onerror'),
                 escaped: html.includes('&lt;img') };
    """ % (_SETTINGS,
           _profile_fetch(_ROSTER, _CONFIG.replace(
               "defaultModel: 'm2'", "defaultModel: '</option></select><img src=x onerror=alert(1)>'"))),
        via="profile", emit="profile")
    # No real <img element may exist; the payload must only appear escaped.
    # (The word 'onerror' legitimately survives inside the escaped text.)
    assert out["img"] is False
    assert "&lt;img src=x onerror=alert(1)&gt;" in out["html"]
    assert out["escaped"] is True


def test_profile_canonical_edit_still_works(node) -> None:
    """Shared canonical file editing still posts /api/memory/canonical and
    confirms; bot settings are untouched."""
    out = _run(node, """
        const posted = [];
        installFetch((url, opts) => {
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/config') return %s;
            if (url === '/api/memory/stats') return { files: [{ name: 'USER.md', content: 'old' }] };
            if (url === '/api/memory/canonical' && opts.method === 'POST') {
                posted.push(JSON.parse(opts.body));
                return { ok: true };
            }
            return {};
        });
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        openEdit('USER.md', null);
        document.querySelector('#edit-content').value = 'new content';
        document.querySelector('#edit-form').dispatch('submit', { target: document.querySelector('#edit-form'), preventDefault() {} });
        await tick(20);
        return { posted, toast: $('#toast').textContent,
                 status: document.querySelector('#profile-status').textContent,
                 settingsPosts: (window.__posted || []).filter(p => String(p.url).includes('/settings/')) };
    """ % (_ROSTER, _CONFIG), via="profile", emit="profile")
    assert out["posted"] == [{"name": "USER.md", "content": "new content"}]
    assert "Đã lưu USER.md" in out["toast"] + out["status"]
    assert out["settingsPosts"] == []


def test_profile_sidebar_select_labels_scope(node) -> None:
    """The shared sidebar select buttons are relabeled page-locally."""
    out = _run(node, """
        %s
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const host = document.querySelector('#team-bot-list');
        ctx.sidebar.appendChild(host);
        host.replaceChildren();
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'Alpha' }]));
        const btn = document.createElement('button');
        btn.dataset.action = 'select'; btn.dataset.botId = 'a';
        btn.setAttribute('aria-label', 'Trò chuyện với Alpha');
        host.appendChild(btn);
        fixSidebarLabels();
        return { label: btn.getAttribute('aria-label') };
    """ % _profile_fetch(_ROSTER, _CONFIG), via="profile", emit="profile")
    assert out["label"] == "Hồ sơ của Alpha"


def test_profile_provider_no_bot_scope(node) -> None:
    """Provider page has no bot editor; sidebar selection navigates to the
    Profile dossier (?bot=<id>). Provider ?bot deep links hand off too."""
    out = _run(node, """
        %s
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/provider/index.html';
        await tick(); await boot(); await tick(20);
        let navigated = null;
        try {
            Object.defineProperty(globalThis.location, 'href', {
                configurable: true,
                get() { return this._href || 'http://127.0.0.1:19502/ui/provider/index.html'; },
                set(v) { navigated = v; },
            });
        } catch (e) { navigated = 'define-failed:' + e.message; }
        selectBot('coder');
        await tick();
        const noBotForm = !document.querySelector('.workspace').innerHTML.includes('bot-settings-form');
        const sharedVisible = !document.querySelector('#connection-editor').hidden
            && !document.querySelector('#save-status').hidden;
        return { navigated: ctx.lastNavigation, noBotForm, sharedVisible };
    """ % _profile_fetch(_ROSTER, _CONFIG), via="provider", emit="provider")
    assert out["navigated"] == {"page": "profile", "search": "?bot=coder"}
    assert out["noBotForm"] is True
    assert out["sharedVisible"] is True


def test_profile_config_refresh_drains_after_pending_bot_settings_save(node) -> None:
    out = _run(node, """
        const cards = [{ id: 'coder', name: 'Coder', role: 'Coder', persona: 'persona', model: { id: 'm1', thinking: 'low' } }];
        let catalog = { values: { defaultProvider: 'alpha', defaultModel: 'm1', providers: { alpha: {} }, models: { m1: { provider: 'alpha' }, m2: { provider: 'alpha' } } },
                        schema: { sections: [{ key: 'provider', fields: [{ key: 'provider.reasoningEffort', choices: ['low', 'high'] }] }] } };
        let settings = { bot_id: 'coder', model: { id: 'm1', thinking: 'low' }, effective: { model: 'm1', provider: 'alpha', thinking: 'low' } };
        let configGets = 0, settingsGets = 0, releaseSave;
        installFetch((url, opts = {}) => {
            if (url === '/api/bots') return { bots: cards };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/memory/stats') return { files: [] };
            if (url === '/api/config') { configGets++; return catalog; }
            if (url === '/api/bots/coder/settings' && opts.method !== 'POST') { settingsGets++; return settings; }
            if (url === '/api/bots/coder/settings' && opts.method === 'POST') {
                const model = JSON.parse(opts.body).model;
                return new Promise(resolve => { releaseSave = () => { settings = { bot_id: 'coder', model, effective: { model: model.id, provider: 'alpha', thinking: model.thinking } }; resolve(settings); }; });
            }
            return {};
        });
        location.search = '?bot=coder'; await tick(); await boot(); await tick(20);
        document.querySelector('#bot-model').value = 'm2'; document.querySelector('#bot-model').dispatch('change', { target: document.querySelector('#bot-model') });
        document.querySelector('#bot-thinking').value = 'high';
        const baselineGets = configGets;
        el.root.dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} }); await tick(5);
        const saving = !!botCfg.savingFor;
        catalog = { values: { defaultProvider: 'alpha', defaultModel: 'm3', providers: { alpha: {} }, models: { m1: { provider: 'alpha' }, m2: { provider: 'alpha' }, m3: { provider: 'alpha' } } },
                    schema: { sections: [{ key: 'provider', fields: [{ key: 'provider.reasoningEffort', choices: ['low', 'high'] }] }] } };
        window.dispatchEvent(new Event('thyca:config-saved'));
        const whileSavingGets = configGets;
        await __controller.onEnter(ctx.route);
        const afterEnterGets = configGets;
        releaseSave();
        for (let n = 0; n < 80 && configGets === baselineGets; n++) await tick(2);
        await tick(20);
        return { saving, baselineGets, whileSavingGets, afterEnterGets, configGets, settingsGets,
                 model: document.querySelector('#bot-model').value, thinking: document.querySelector('#bot-thinking').value,
                 hasM3: document.querySelector('#bot-settings-fields').innerHTML.includes('value="m3"'),
                 dirty: catalogDirty, savingFor: !!botCfg.savingFor };
    """, via="profile", emit="profile")
    assert out["saving"] is True and out["whileSavingGets"] == out["baselineGets"]
    assert out["afterEnterGets"] == out["baselineGets"]
    assert out["configGets"] == out["baselineGets"] + 1 and out["settingsGets"] >= 2
    assert out["model"] == "m2" and out["thinking"] == "high" and out["hasM3"]
    assert out["dirty"] is False and out["savingFor"] is False


def test_profile_refreshes_dirty_catalog_on_enter_and_preserves_valid_draft(node) -> None:
    out = _run(node, """
        const cards = [{ id: 'coder', name: 'Coder', role: 'Coder', persona: 'persona', model: { id: 'm1', thinking: 'low' } }];
        let catalog = { values: { defaultProvider: 'alpha', defaultModel: 'm1', providers: { alpha: {} },
                                 models: { m1: { provider: 'alpha', reasoningEfforts: ['low'] }, m2: { provider: 'alpha' } } },
                        schema: { sections: [{ key: 'provider', fields: [{ key: 'provider.reasoningEffort', choices: ['low', 'high'] }] }] } };
        let configGets = 0;
        const settings = { bot_id: 'coder', model: { id: 'm1', thinking: 'low' },
                           effective: { model: 'm1', provider: 'alpha', thinking: 'low' } };
        installFetch(url => {
            if (url === '/api/bots') return { bots: cards };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/memory/stats') return { files: [] };
            if (url === '/api/config') { configGets++; return catalog; }
            if (url === '/api/bots/coder/settings') return settings;
            return {};
        });
        location.search = '?bot=coder'; await tick(); await boot(); await tick(10);
        const baselineGets = configGets;
        document.querySelector('#bot-model').value = 'm2'; document.querySelector('#bot-model').dispatch('change', { target: document.querySelector('#bot-model') });
        document.querySelector('#bot-thinking').value = 'high';
        catalog = { values: { defaultProvider: 'alpha', defaultModel: 'm3', providers: { alpha: {} },
                             models: { m1: { provider: 'alpha' }, m2: { provider: 'alpha' }, m3: { provider: 'alpha' } } },
                    schema: { sections: [{ key: 'provider', fields: [{ key: 'provider.reasoningEffort', choices: ['low', 'high'] }] }] } };
        window.dispatchEvent(new Event('thyca:config-saved'));
        const inactive = { gets: configGets, hasM3: document.querySelector('#bot-settings-fields').innerHTML.includes('value="m3"') };
        await __controller.onEnter(ctx.route); await tick(10);
        return { baselineGets, inactive, gets: configGets, model: document.querySelector('#bot-model').value,
                 thinking: document.querySelector('#bot-thinking').value,
                 hasM3: document.querySelector('#bot-settings-fields').innerHTML.includes('value="m3"'),
                 scope: state.tab, status: $('#profile-status').textContent };
    """, via="profile", emit="profile")
    assert out["inactive"] == {"gets": out["baselineGets"], "hasM3": False}
    assert out["gets"] == out["baselineGets"] + 1 and out["model"] == "m2" and out["thinking"] == "high"
    assert out["hasM3"] and out["scope"] == "coder" and out["status"] == ""
