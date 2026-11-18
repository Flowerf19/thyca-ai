"""Node tests for Profile dossier views and per-bot model settings inside #settings-dialog.

Same classic-script bundle harness as test_team_frontend: ``_run``/``node``
are reused from that module so the bundles stay identical.
"""
from test_team_frontend import _run, node  # noqa: F401  (fixture)


def _mock_fetch(roster_js, config_js, settings_js=None):
    settings_js = settings_js if settings_js is not None else _SETTINGS
    return """
        installFetch((url, opts = {}) => {
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/conversations') return { conversations: [] };
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
    """ % (roster_js, config_js)


_ROSTER = (
    "[{ id: 'thyca', name: 'Thyca', role: 'Manager', persona: 'Root persona', system: true, model: { id: 'default', thinking: null } },"
    " { id: 'coder', name: 'Coder', role: 'Coder', persona: 'Coder persona', model: { id: 'm1', thinking: 'custom' } }]"
)
_CONFIG = (
    "{ values: { defaultProvider: 'default', defaultModel: 'm2',"
    " limits: { loopMax: 80, hotTailKB: 4 },"
    " providers: { default: { baseUrl: 'https://x.invalid/v1', reasoningEffort: 'high', apiKey: '' },"
    " 'card-provider': { baseUrl: 'https://card.invalid/v1', reasoningEffort: 'low', apiKey: '' } },"
    " models: { m1: { provider: 'card-provider', reasoningEfforts: ['low', 'custom'] }, m2: {} } },"
    " meta: { providers: { default: false, 'card-provider': false } },"
    " schema: { sections: ["
    "  { key: 'limits', label: 'L', fields: [{ key: 'limits.loopMax', label: 'Vòng', type: 'number', min: 1, max: 200 }] },"
    "  { key: 'provider', label: 'P', fields: [{ key: 'provider.reasoningEffort', label: 'Suy luận', choices: ['low', 'high', 'max'] }] },"
    "  ] } }"
)
_SETTINGS = (
    "{ '/api/bots/thyca/settings': { bot_id: 'thyca', model: { id: 'default', thinking: null },"
    "    effective: { model: 'm2', provider: 'default', thinking: 'high' } },"
    " '/api/bots/coder/settings': { bot_id: 'coder', model: { id: 'm1', thinking: 'custom' },"
    "    effective: { model: 'm1', provider: 'card-provider', thinking: 'custom' } } }"
)


def _setup_dialog_bots(roster_js=_ROSTER):
    return """
        bots.length = 0;
        bots.push(...roster(%s));
    """ % roster_js


# ---------------------------------------------------------------------------
# Profile page: canonical docs + read-only bot dossier (no model settings form)
# ---------------------------------------------------------------------------

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
            hasBotModelCard: root.includes('bot-model-card'),
            hasBotHeading: root.includes('Bạn và'),
        };
    """ % (_SETTINGS, _mock_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["title"] == "Hồ sơ chung"
    assert out["shared"] == "" and out["tab"] == ""
    assert out["hasUser"] and out["hasSoul"] and out["hasIdentity"]
    assert out["hasBotForm"] is False and out["hasBotModelCard"] is False
    assert out["hasBotHeading"] is False
    assert out["sharedBtnHidden"] is True


def test_profile_sidebar_select_shows_dossier_only(node) -> None:
    """Sidebar coder click stays on Profile (?bot=coder) and renders only the
    dossier card + TWIN.md — no #bot-model-card / #bot-settings-form."""
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
        await tick(20);
        const root = document.querySelector('#profile-root').innerHTML;
        return {
            replaced, route: ctx.route, tab: state.tab, shared: selectedBot,
            title: $('#profile-title').textContent,
            root,
            hasBotForm: root.includes('bot-settings-form'),
            hasBotModelCard: root.includes('bot-model-card'),
            hasSharedDoc: root.includes('file-USER'),
            sharedBtnHidden: $('#profile-shared').hidden,
        };
    """ % (_SETTINGS, _mock_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["replaced"] and out["replaced"][-1] == "/"
    assert out["tab"] == "coder" and out["shared"] == "coder"
    assert out["route"] == {"page": "profile", "search": "?bot=coder", "hash": ""}
    assert out["title"] == "Bạn và Coder"
    assert "Coder persona" in out["root"]
    assert "model: m1 · suy nghĩ custom" in out["root"]
    assert out["hasBotForm"] is False and out["hasBotModelCard"] is False
    assert out["hasSharedDoc"] is False
    assert out["sharedBtnHidden"] is False


def test_profile_deep_links_and_ghost(node) -> None:
    """?bot=coder / ?bot=thyca select directly; ?bot=ghost preserves the id
    with an honest error — never falling back to Thyca."""
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
    """ % (_SETTINGS, _mock_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["a"]["selected"] == "coder" and out["a"]["search"] == "?bot=coder"
    assert out["a"]["title"] == "Bạn và Coder"
    assert out["b"]["selected"] == "thyca" and "Thyca" in out["b"]["title"]
    assert out["c"]["selected"] == "ghost" and out["c"]["search"] == "?bot=ghost"
    assert out["c"]["shared"] == ""
    assert out["c"]["title"] == "Không tìm thấy bot"
    assert "Không tìm thấy bot" in out["c"]["root"]


def test_profile_shared_button_and_bot_settings_event(node) -> None:
    """'Hồ sơ chung' clears ?bot and restores shared docs; saving bot settings
    via thyca:bot-settings-saved refreshes the live dossier model chip."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const beforeDossier = document.querySelector('#profile-root').innerHTML;
        getBot('coder').model = { id: 'm2', thinking: 'low' };
        window.dispatchEvent(new CustomEvent('thyca:bot-settings-saved', { detail: { botId: 'coder' } }));
        await tick();
        const afterEvent = document.querySelector('#profile-root').innerHTML;
        $('#profile-shared').onclick();
        await tick();
        const sharedRoot = document.querySelector('#profile-root').innerHTML;
        return { beforeDossier, afterEvent, sharedRoot, tab: state.tab, shared: selectedBot, title: $('#profile-title').textContent };
    """ % (_SETTINGS, _mock_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert "model: m1 · suy nghĩ custom" in out["beforeDossier"]
    assert "model: m2 · suy nghĩ low" in out["afterEvent"]
    assert "file-USER" in out["sharedRoot"] and out["tab"] == "" and out["shared"] == ""
    assert out["title"] == "Hồ sơ chung"


def test_profile_canonical_edit_still_works(node) -> None:
    """Shared canonical file editing still posts /api/memory/canonical."""
    out = _run(node, """
        const posted = [];
        installFetch((url, opts = {}) => {
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/conversations') return { conversations: [] };
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
                 status: document.querySelector('#profile-status').textContent };
    """ % (_ROSTER, _CONFIG), via="profile", emit="profile")
    assert out["posted"] == [{"name": "USER.md", "content": "new content"}]
    assert "Đã lưu USER.md" in out["toast"] + out["status"]


def test_profile_sidebar_select_labels_scope(node) -> None:
    """The shared sidebar select buttons are relabeled page-locally."""
    out = _run(node, """
        const SETTINGS = %s;
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
    """ % (_SETTINGS, _mock_fetch(_ROSTER, _CONFIG)), via="profile", emit="profile")
    assert out["label"] == "Hồ sơ của Alpha"


def test_profile_provider_no_bot_scope(node) -> None:
    """Provider page has no bot editor; sidebar selection navigates to the
    Profile dossier (?bot=<id>)."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/provider/index.html';
        await tick(); await boot(); await tick(20);
        selectBot('coder');
        await tick();
        const noBotForm = !document.querySelector('.workspace').innerHTML.includes('bot-settings-form');
        const sharedVisible = !document.querySelector('#connection-editor').hidden
            && !document.querySelector('#save-status').hidden;
        return { navigated: ctx.lastNavigation, noBotForm, sharedVisible };
    """ % (_SETTINGS, _mock_fetch(_ROSTER, _CONFIG)), via="provider", emit="provider")
    assert out["navigated"] == {"page": "profile", "search": "?bot=coder"}
    assert out["noBotForm"] is True
    assert out["sharedVisible"] is True


# ---------------------------------------------------------------------------
# Settings dialog: per-bot model & thinking scoped by #settings-bot
# ---------------------------------------------------------------------------

def test_settings_dialog_bot_forms_are_isolated(node) -> None:
    """#settings-dialog loads the selected #settings-bot's own model settings
    (coder vs thyca), keeping them strictly isolated across switches."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        %s
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(30);
        const coderFields = document.querySelector('#bot-settings-fields').innerHTML;
        const coderSel = document.querySelector('#settings-bot').value;
        document.querySelector('#settings-bot').value = 'thyca';
        document.querySelector('#settings-bot').onchange({ target: document.querySelector('#settings-bot') });
        await tick(30);
        const thycaFields = document.querySelector('#bot-settings-fields').innerHTML;
        const thycaSel = document.querySelector('#settings-bot').value;
        return { coderSel, coderFields, thycaSel, thycaFields };
    """ % (_SETTINGS, _setup_dialog_bots(), _mock_fetch(_ROSTER, _CONFIG)), via="dialog")
    assert out["coderSel"] == "coder"
    assert '<option value="m1" selected>' in out["coderFields"]
    assert '<option value="custom" selected>' in out["coderFields"]
    assert "card-provider" in out["coderFields"]
    assert out["thycaSel"] == "thyca"
    assert '<option value="default" selected>' in out["thycaFields"]
    assert '<option value="m1" selected>' not in out["thycaFields"]


def test_settings_dialog_bot_save_posts_settings_only(node) -> None:
    """Per-bot save inside #settings-dialog POSTs only /api/bots/<id>/settings,
    updates the live roster bot model, and fires thyca:bot-settings-saved."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        %s
        const configPosts = [];
        const savedEvents = [];
        window.addEventListener('thyca:bot-settings-saved', e => savedEvents.push(e.detail));
        const origFetch = globalThis.fetch;
        globalThis.fetch = async (url, opts = {}) => {
            if (String(url).endsWith('/api/config') && opts.method === 'POST') configPosts.push(1);
            if (String(url).endsWith('/api/memory/canonical') && opts.method === 'POST') configPosts.push(1);
            return origFetch(url, opts);
        };
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(30);
        document.querySelector('#bot-model').value = 'default';
        document.querySelector('#bot-thinking').value = 'low';
        document.querySelector('#bot-settings-form').dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} });
        await tick(30);
        return { posted: window.__posted || [], configPosts, savedEvents,
                 liveModel: getBot('coder').model,
                 hint: document.querySelector('#bot-settings-hint').textContent,
                 enabled: !document.querySelector('#save-bot-settings').disabled };
    """ % (_SETTINGS, _setup_dialog_bots(), _mock_fetch(_ROSTER, _CONFIG)), via="dialog")
    assert out["posted"] == [{
        "url": "/api/bots/coder/settings",
        "body": {"model": {"id": "default", "thinking": "low"}},
    }]
    assert out["configPosts"] == []
    assert out["hint"] == "Đã lưu." and out["enabled"] is True
    assert out["liveModel"] == {"id": "default", "thinking": "low"}
    assert out["savedEvents"] and out["savedEvents"][0]["botId"] == "coder"


def test_settings_dialog_bot_provider_preview_and_default_routing(node) -> None:
    """Changing #bot-model updates #bot-provider-preview (draft vs saved) and
    #bot-thinking choices; default inheritance routes via defaultModel's provider."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        %s
        selectedBot = 'thyca';
        await tick();
        openSettingsDialog();
        await tick(30);
        const before = document.querySelector('#bot-provider-preview').textContent;
        document.querySelector('#bot-model').value = 'm1';
        document.querySelector('#bot-model').dispatch('change', { target: document.querySelector('#bot-model') });
        await tick();
        const after = document.querySelector('#bot-provider-preview').textContent;
        const effortOpts = $$('#bot-thinking option').map(o => o.value);
        return { before, after, effortOpts };
    """ % (_SETTINGS, _setup_dialog_bots(), _mock_fetch(_ROSTER, _CONFIG)), via="dialog")
    assert "default" in out["before"] and "chưa lưu" not in out["before"]
    assert "card-provider" in out["after"] and "chưa lưu" in out["after"]
    assert out["effortOpts"] == ["", "low", "custom"]

    out2 = _run(node, """
        const SETTINGS = %s;
        %s
        %s
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(30);
        return { fields: document.querySelector('#bot-settings-fields').innerHTML,
                 preview: document.querySelector('#bot-provider-preview').textContent };
    """ % (_SETTINGS, _setup_dialog_bots(), _mock_fetch(_ROSTER, _CONFIG.replace(
        "defaultModel: 'm2'", "defaultModel: 'm1'"))), via="dialog")
    assert "Theo mặc định chung (m1)" in out2["fields"]
    assert "card-provider" in out2["preview"] and " (dự kiến" not in out2["preview"]

    out3 = _run(node, """
        const SETTINGS = %s;
        %s
        %s
        selectedBot = 'thyca';
        await tick();
        openSettingsDialog();
        await tick(30);
        const before = document.querySelector('#bot-provider-preview').textContent;
        const model = document.querySelector('#bot-model');
        model.value = 'unregistered-default';
        model.dispatch('change', { target: model });
        return { before, after: document.querySelector('#bot-provider-preview').textContent,
                 enabled: !document.querySelector('#save-bot-settings').disabled };
    """ % (_SETTINGS, _setup_dialog_bots(), _mock_fetch(_ROSTER, _CONFIG.replace(
        "defaultModel: 'm2'", "defaultModel: 'unregistered-default'"))), via="dialog")
    assert out3["before"] == out3["after"] == "Provider: default"
    assert out3["enabled"] is True


def test_settings_dialog_bot_unknown_model_honest(node) -> None:
    """A saved model id missing from catalog stays selected with an honest
    suffix and keeps Save disabled until a known id is chosen."""
    out = _run(node, """
        const SETTINGS = {
            '/api/bots/thyca/settings': { bot_id: 'thyca', model: { id: 'default', thinking: null },
                effective: { model: 'm2', provider: 'default', thinking: 'high' } },
            '/api/bots/coder/settings': { bot_id: 'coder', model: { id: 'retired-model', thinking: 'low' },
                effective: { model: 'm2', provider: 'default', thinking: 'low' } },
        };
        %s
        %s
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(30);
        const sel = document.querySelector('#bot-model');
        sel.value = 'retired-model';
        sel.dispatch('change', { target: sel });
        await tick();
        const fields = document.querySelector('#bot-settings-fields').innerHTML;
        return { fields,
                 saveDisabled: document.querySelector('#save-bot-settings').disabled,
                 preview: document.querySelector('#bot-provider-preview').textContent };
    """ % (_setup_dialog_bots(), _mock_fetch(_ROSTER, _CONFIG)), via="dialog")
    assert '<option value="retired-model" selected>' in out["fields"]
    assert "không còn trong danh mục" in out["fields"]
    assert 'id="bot-model-notice" hidden' not in out["fields"]
    assert out["saveDisabled"] is True
    assert out["preview"] == "Provider: — (model không còn trong danh mục)"


def test_settings_dialog_bot_injection_safe_label(node) -> None:
    """Hostile defaultModel strings render HTML-escaped in #bot-model options."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        %s
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(30);
        const html = document.querySelector('#bot-settings-fields').innerHTML;
        return { html, img: html.includes('<img'), escaped: html.includes('&lt;img') };
    """ % (_SETTINGS, _setup_dialog_bots(), _mock_fetch(_ROSTER, _CONFIG.replace(
        "defaultModel: 'm2'", "defaultModel: '</option></select><img src=x onerror=alert(1)>'"))), via="dialog")
    assert out["img"] is False
    assert "&lt;img src=x onerror=alert(1)&gt;" in out["html"]
    assert out["escaped"] is True


def test_settings_dialog_bot_stale_guards_and_save_twice(node) -> None:
    """Stale GET/POST responses never repaint another bot; duplicate submit
    while saving posts nothing; roundtrip close/switch keeps Save locked until complete."""
    out = _run(node, """
        const pending = [];
        %s
        installFetch((url, opts = {}) => {
            if (String(url).endsWith('/settings') && opts.method !== 'POST') {
                const gm = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
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
            if (url === '/api/conversations') return { conversations: [] };
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') return %s;
            return {};
        });
        const eff = (bot, model) => ({ bot_id: bot, model,
            effective: { model: model.id, provider: 'card-provider', thinking: model.thinking || 'high' } });
        const fireSubmit = () => document.querySelector('#bot-settings-form').dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} });
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(10);
        // stale GET: switch away to thyca before coder's GET resolves
        selectSettingsBot('thyca');
        await tick(20);
        pending.filter(p => !p.payload && p.bot === 'coder')
               .forEach(p => p.resolve(eff('coder', { id: 'm1', thinking: 'low' })));
        pending.filter(p => !p.payload && p.bot === 'thyca')
               .forEach(p => p.resolve(eff('thyca', { id: 'default', thinking: null })));
        await tick(20);
        const staleOk = currentSettingsBot() === 'thyca'
            && !document.querySelector('#bot-settings-fields').innerHTML.includes('<option value="m1" selected>');
        // save twice on thyca: duplicate blocked while pending, second completed posts again
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
                             hint: document.querySelector('#bot-settings-hint').textContent,
                             enabled: !document.querySelector('#save-bot-settings').disabled };
        // switch coder -> back to thyca while save is pending keeps Save blocked
        document.querySelector('#bot-model').value = 'm1';
        document.querySelector('#bot-thinking').value = 'custom';
        const third = fireSubmit();
        await tick();
        selectSettingsBot('coder');
        await tick();
        selectSettingsBot('thyca');
        await tick(20);
        const backBlocked = document.querySelector('#save-bot-settings').disabled;
        fireSubmit();
        await tick();
        pending.filter(p => p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', p.payload)));
        await third; await tick();
        pending.filter(p => !p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', { id: 'default', thinking: 'low' })));
        await tick(20);
        document.querySelector('#bot-model').value = 'm1';
        document.querySelector('#bot-thinking').value = 'high';
        fireSubmit();
        await tick();
        pending.filter(p => p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', p.payload)));
        await tick(20);
        return { staleOk, blocked, afterTwice, backBlocked,
                 posts: window.__posted || [],
                 still: currentSettingsBot(),
                 enabled: !document.querySelector('#save-bot-settings').disabled,
                 hint: document.querySelector('#bot-settings-hint').textContent };
    """ % (_setup_dialog_bots(), _ROSTER, _CONFIG), via="dialog")
    assert out["staleOk"] is True and out["blocked"] is True
    assert out["afterTwice"]["posts"] == 2
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


def test_settings_dialog_bot_draft_survives_stale_completion_and_error(node) -> None:
    """Stale coder POST completion never overwrites thyca's unsaved draft;
    failed POST keeps the draft editable with an error hint."""
    out = _run(node, """
        const pending = [];
        let failNextPost = false;
        %s
        installFetch((url, opts = {}) => {
            if (String(url).endsWith('/settings') && opts.method !== 'POST') {
                const gm = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
                return { ok: true, status: 200, json: () => new Promise(r => pending.push({ bot: gm && gm[1], resolve: r, reject: null, payload: null })) };
            }
            if (String(url).endsWith('/settings') && opts.method === 'POST') {
                const m = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
                const body = JSON.parse(opts.body);
                if (failNextPost) {
                    failNextPost = false;
                    return Promise.reject(new Error('save rejected'));
                }
                window.__posted = window.__posted || [];
                window.__posted.push({ url, body });
                return { ok: true, status: 200,
                         json: () => new Promise(r => pending.push({ bot: m && m[1], resolve: d => r(d), payload: body })) };
            }
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/conversations') return { conversations: [] };
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') return %s;
            return {};
        });
        const eff = (bot, model) => ({ bot_id: bot, model,
            effective: { model: model.id, provider: 'card-provider', thinking: model.thinking || 'high' } });
        const fireSubmit = () => document.querySelector('#bot-settings-form').dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} });
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(20);
        pending.filter(p => !p.payload && p.bot === 'coder')
               .forEach(p => p.resolve(eff('coder', { id: 'm1', thinking: 'custom' })));
        await tick(20);
        const savingCoder = fireSubmit();
        await tick();
        selectSettingsBot('thyca');
        await tick(20);
        pending.filter(p => !p.payload && p.bot === 'thyca')
               .forEach(p => p.resolve(eff('thyca', { id: 'default', thinking: null })));
        await tick(20);
        document.querySelector('#bot-model').value = 'm2';
        document.querySelector('#bot-thinking').value = 'low';
        await tick();
        pending.filter(p => p.payload && p.bot === 'coder').forEach(p => p.resolve(eff('coder', p.payload)));
        await savingCoder; await tick();
        const draftKept = [document.querySelector('#bot-model').value, document.querySelector('#bot-thinking').value,
                           document.querySelector('#bot-settings-hint').textContent];
        failNextPost = true;
        await fireSubmit();
        await tick(20);
        const afterError = { hint: document.querySelector('#bot-settings-hint').textContent,
                             model: document.querySelector('#bot-model').value,
                             thinking: document.querySelector('#bot-thinking').value,
                             enabled: !document.querySelector('#save-bot-settings').disabled };
        const thycaSave = fireSubmit();
        await tick();
        pending.filter(p => p.payload && p.bot === 'thyca').forEach(p => p.resolve(eff('thyca', p.payload)));
        await thycaSave; await tick();
        return { draftKept, afterError, posts: window.__posted || [],
                 hint: document.querySelector('#bot-settings-hint').textContent };
    """ % (_setup_dialog_bots(), _ROSTER, _CONFIG), via="dialog")
    assert out["draftKept"][0:2] == ["m2", "low"]
    assert "Đang lưu" not in out["draftKept"][2]
    assert "Không lưu được: save rejected" in out["afterError"]["hint"]
    assert out["afterError"]["model"] == "m2" and out["afterError"]["thinking"] == "low"
    assert out["afterError"]["enabled"] is True
    thycaPosts = [p for p in out["posts"] if p["url"] == "/api/bots/thyca/settings"]
    assert [p["body"] for p in thycaPosts] == [{"model": {"id": "m2", "thinking": "low"}}]
    assert out["hint"] == "Đã lưu."


def test_settings_dialog_bot_catalog_refreshes_on_config_saved(node) -> None:
    """External thyca:config-saved skips background fetches while closed, waits
    until an in-flight bot save completes when open, and offers newly saved models."""
    out = _run(node, """
        %s
        let catalog = { values: { defaultProvider: 'alpha', defaultModel: 'm1', limits: { loopMax: 80 },
                                  providers: { alpha: {} }, models: { m1: { provider: 'alpha' }, m2: { provider: 'alpha' } } },
                        schema: { sections: [{ key: 'limits', label: 'L', fields: [] },
                                             { key: 'provider', fields: [{ key: 'provider.reasoningEffort', choices: ['low', 'high'] }] }] } };
        let settings = { bot_id: 'coder', model: { id: 'm1', thinking: 'low' },
                         effective: { model: 'm1', provider: 'alpha', thinking: 'low' } };
        let configGets = 0, releaseSave;
        installFetch((url, opts = {}) => {
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') { configGets++; return catalog; }
            if (url === '/api/bots/coder/settings' && opts.method !== 'POST') return settings;
            if (url === '/api/bots/coder/settings' && opts.method === 'POST') {
                const model = JSON.parse(opts.body).model;
                return new Promise(resolve => { releaseSave = () => {
                    settings = { bot_id: 'coder', model, effective: { model: model.id, provider: 'alpha', thinking: model.thinking } };
                    resolve(settings);
                }; });
            }
            return {};
        });
        selectedBot = 'coder';
        await tick();
        // Closed dialog does not fetch immediately on thyca:config-saved
        window.dispatchEvent(new Event('thyca:config-saved'));
        await tick(10);
        const closedGets = configGets;

        openSettingsDialog();
        await tick(30);
        const baselineGets = configGets;
        document.querySelector('#bot-model').value = 'm2';
        document.querySelector('#bot-model').dispatch('change', { target: document.querySelector('#bot-model') });
        document.querySelector('#bot-thinking').value = 'high';
        document.querySelector('#bot-settings-form').dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} });
        await tick(10);
        const saving = !!botCfg.savingFor;
        catalog = { values: { defaultProvider: 'alpha', defaultModel: 'm3', limits: { loopMax: 80 },
                              providers: { alpha: {} }, models: { m1: { provider: 'alpha' }, m2: { provider: 'alpha' }, m3: { provider: 'alpha' } } },
                    schema: { sections: [{ key: 'limits', label: 'L', fields: [] },
                                         { key: 'provider', fields: [{ key: 'provider.reasoningEffort', choices: ['low', 'high'] }] }] } };
        window.dispatchEvent(new Event('thyca:config-saved'));
        await tick(10);
        const whileSavingGets = configGets;
        releaseSave();
        for (let n = 0; n < 80 && configGets === baselineGets; n++) await tick(2);
        await tick(20);
        return { closedGets, baselineGets, saving, whileSavingGets, configGets,
                 model: document.querySelector('#bot-model').value,
                 thinking: document.querySelector('#bot-thinking').value,
                 hasM3: document.querySelector('#bot-settings-fields').innerHTML.includes('value="m3"') };
    """ % _setup_dialog_bots(), via="dialog")
    assert out["closedGets"] == 0
    assert out["saving"] is True and out["whileSavingGets"] == out["baselineGets"]
    assert out["configGets"] == out["baselineGets"] + 1
    assert out["model"] == "m2" and out["thinking"] == "high" and out["hasM3"] is True


def test_settings_dialog_reopen_while_save_pending_completes_cleanly(node) -> None:
    """Closing and reopening #settings-dialog on the same bot while its POST is
    still in flight keeps the pending save alive, preserves draft/disabled state,
    and finishes cleanly with 'Đã lưu.' when the server responds."""
    out = _run(node, """
        %s
        let releaseSave;
        const savedEvents = [];
        window.addEventListener('thyca:bot-settings-saved', e => savedEvents.push(e.detail));
        installFetch((url, opts = {}) => {
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') return %s;
            if (url === '/api/bots/coder/settings' && opts.method !== 'POST') {
                return { bot_id: 'coder', model: { id: 'm1', thinking: 'low' },
                         effective: { model: 'm1', provider: 'card-provider', thinking: 'low' } };
            }
            if (url === '/api/bots/coder/settings' && opts.method === 'POST') {
                const model = JSON.parse(opts.body).model;
                return new Promise(resolve => { releaseSave = () => resolve({
                    bot_id: 'coder', model,
                    effective: { model: model.id, provider: 'card-provider', thinking: model.thinking }
                }); });
            }
            return {};
        });
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(30);
        document.querySelector('#bot-model').value = 'm2';
        document.querySelector('#bot-model').dispatch('change', { target: document.querySelector('#bot-model') });
        document.querySelector('#bot-thinking').value = 'high';
        const saving = document.querySelector('#bot-settings-form').dispatch('submit', { target: document.querySelector('#bot-settings-form'), preventDefault() {} });
        await tick(10);
        document.querySelector('#settings-dialog').close();
        openSettingsDialog();
        await tick(20);
        const whilePending = {
            model: document.querySelector('#bot-model').value,
            thinking: document.querySelector('#bot-thinking').value,
            hint: document.querySelector('#bot-settings-hint').textContent,
            disabled: document.querySelector('#save-bot-settings').disabled,
        };
        releaseSave();
        await saving;
        await tick(20);
        return {
            whilePending,
            hintAfter: document.querySelector('#bot-settings-hint').textContent,
            disabledAfter: document.querySelector('#save-bot-settings').disabled,
            liveModel: getBot('coder').model,
            savedEvents,
        };
    """ % (_setup_dialog_bots(), _CONFIG), via="dialog")
    assert out["whilePending"] == {"model": "m2", "thinking": "high", "hint": "Đang lưu…", "disabled": True}
    assert out["hintAfter"] == "Đã lưu."
    assert out["disabledAfter"] is False
    assert out["liveModel"] == {"id": "m2", "thinking": "high"}
    assert out["savedEvents"] and out["savedEvents"][0]["botId"] == "coder"


def test_settings_dialog_config_reload_never_paints_other_bot_draft(node) -> None:
    """A GET /api/config that resolves after the user switched #settings-bot
    must not paint the previous bot's unsaved draft into the new bot's form,
    and an edit typed while that GET is in flight must survive its repaint."""
    out = _run(node, """
        const SETTINGS = %s;
        %s
        let holdConfig = false;
        let releaseConfig = null;
        installFetch((url, opts = {}) => {
            if (url === '/api/bots') return { bots: %s };
            if (url === '/api/conversations') return { conversations: [] };
            if (url === '/api/config/status') return { ready: true, version: 'x' };
            if (url === '/api/config') {
                if (holdConfig) return new Promise(r => { releaseConfig = () => r(%s); });
                return %s;
            }
            const m = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
            if (m && SETTINGS['/api/bots/' + m[1] + '/settings']) return SETTINGS['/api/bots/' + m[1] + '/settings'];
            return {};
        });
        selectedBot = 'coder';
        await tick();
        openSettingsDialog();
        await tick(30);
        const opened = { bot: document.querySelector('#settings-bot').value,
                         model: document.querySelector('#bot-model').value };
        // Unsaved coder draft: m1/custom -> m2/low.
        document.querySelector('#bot-model').value = 'm2';
        document.querySelector('#bot-thinking').value = 'low';
        // Config reload starts, then the user switches to thyca mid-flight.
        holdConfig = true;
        const reloading = loadSettingsConfig();
        await tick();
        selectSettingsBot('thyca');
        await tick(30);
        releaseConfig();
        await reloading;
        await tick(10);
        const afterSwitch = { bot: document.querySelector('#settings-bot').value,
                              model: document.querySelector('#bot-model').value,
                              thinking: document.querySelector('#bot-thinking').value };
        // Same-bot in-flight edit: start another reload, edit, then resolve.
        holdConfig = true;
        const reloading2 = loadSettingsConfig();
        await tick();
        document.querySelector('#bot-model').value = 'm1';
        document.querySelector('#bot-thinking').value = 'low';
        releaseConfig();
        await reloading2;
        await tick(10);
        const afterInFlightEdit = { model: document.querySelector('#bot-model').value,
                                    thinking: document.querySelector('#bot-thinking').value };
        return { opened, afterSwitch, afterInFlightEdit };
    """ % (_SETTINGS, _setup_dialog_bots(), _ROSTER, _CONFIG, _CONFIG), via="dialog")
    assert out["opened"] == {"bot": "coder", "model": "m1"}
    assert out["afterSwitch"] == {"bot": "thyca", "model": "default", "thinking": ""}
    assert out["afterInFlightEdit"] == {"model": "m1", "thinking": "low"}


def test_settings_dialog_limits_save_does_not_pin_unpinned_default_model(node) -> None:
    """Saving limits.contextTokens when the default model has no contextTokens
    of its own (it inherits the global limit) must only write the global
    field — it must not create a per-model pin that shadows future globals."""
    out = _run(node, r"""
        const server = {
            revision: 'rev-1',
            values: {
                providers: { alpha: { baseUrl: 'https://a.invalid', apiKey: '' } },
                models: { m1: { provider: 'alpha' } },
                defaultProvider: 'alpha',
                defaultModel: 'm1',
                limits: { loopMax: 80, hotTailKB: 4, contextTokens: 272000 },
            },
            schema: { sections: [{ key: 'limits', label: 'Limits', fields: [
                { key: 'limits.contextTokens', label: 'Ngữ cảnh tối đa', type: 'number', min: 1000, max: 2000000 },
            ] }] },
        };
        const posted = [];
        installFetch((url, opts) => {
            if (url === '/api/bots') return { bots: [] };
            if (url === '/api/conversations') return { conversations: [] };
            if (url === '/api/config/status') return { ready: true };
            if (url === '/api/config' && opts.method === 'POST') {
                const body = JSON.parse(opts.body);
                posted.push(body);
                delete body.baseRevision;
                server.values = body;
                server.revision = 'rev-2';
                return {};
            }
            if (url === '/api/config') return server;
            return {};
        });
        await tick();
        await loadSettingsConfig();
        const shown = document.querySelector('#config-fields').innerHTML;
        const input = document.createElement('input');
        input.type = 'number';
        input.dataset.section = 'limits';
        input.dataset.name = 'contextTokens';
        input.dataset.label = 'Ngữ cảnh tối đa';
        Object.defineProperty(input, 'valueAsNumber', { get: () => 205000 });
        document.querySelector('#config-form').appendChild(input);
        await submitSettingsConfig(document.querySelector('#config-form'));
        return { shown, posted };
    """, via="dialog")
    assert 'value="272000"' in out["shown"]
    assert len(out["posted"]) == 1
    assert out["posted"][0]["limits"]["contextTokens"] == 205000
    assert "contextTokens" not in out["posted"][0]["models"]["m1"]
