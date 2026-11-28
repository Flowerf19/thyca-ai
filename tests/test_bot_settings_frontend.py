"""Node tests for Profile dossier views and per-bot settings inside Profile.

Same classic-script bundle harness as test_team_frontend: ``_run``/``node``
are reused from that module so the bundles stay identical.
"""
from test_team_frontend import _run


def _mock_fetch(roster_js, config_js, settings_js=None):
    settings_js = settings_js if settings_js is not None else _SETTINGS
    return f"""
        installFetch((url, opts = {{}}) => {{
            if (url === '/api/bots') return {{ bots: {roster_js} }};
            if (url === '/api/conversations') return {{ conversations: [] }};
            if (url === '/api/config/status') return {{ ready: true, version: 'x' }};
            if (url === '/api/config') return {config_js};
            if (url === '/api/memory/stats') return {{ files: [
                {{ name: 'USER.md', content: 'user file' }},
                {{ name: 'SOUL.md', content: 'soul file' }},
                {{ name: 'IDENTITY.md', content: 'identity file' }} ] }};
            const m = String(url).match(/^\\/api\\/bots\\/([^/]+)\\/settings$/);
            if (m) {{
                const key = '/api/bots/' + m[1] + '/settings';
                if (opts.method === 'POST') {{
                    const body = JSON.parse(opts.body);
                    window.__posted = window.__posted || [];
                    window.__posted.push({{ url, body }});
                    return {{ bot_id: m[1], model: body.model,
                             effective: {{ model: body.model.id === 'default' ? 'm2' : body.model.id,
                                          provider: 'card-provider', thinking: body.model.thinking || 'high' }} }};
                }}
                if (SETTINGS[key]) return SETTINGS[key];
                const err = new Error('bot not found'); err.status = 404; throw err;
            }}
            return {{}};
        }});
    """


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
    return f"""
        bots.length = 0;
        bots.push(...roster({roster_js}));
    """


def test_profile_shared_by_default(node) -> None:
    """No ?bot= shows canonical tabs defaulting to USER.md ('Về người dùng'),
    switching to SOUL.md/IDENTITY.md, and splitting ## headings into section cards."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const userRoot = document.querySelector('#profile-root').innerHTML;
        selectDoc('SOUL.md');
        const soulRoot = document.querySelector('#profile-root').innerHTML;
        selectDoc('IDENTITY.md');
        const identityRoot = document.querySelector('#profile-root').innerHTML;
        const tplHtml = document.querySelector('template#tpl-profile').innerHTML;
        return {{
            title: $('#profile-title').textContent,
            shared: selectedBot, tab: state.tab, doc: state.doc,
            noFooterBtns: !tplHtml.includes('profile-shared') && !tplHtml.includes('profile-reload'),
            noTabFilenames: !userRoot.includes('<small>USER.md</small>') && !userRoot.includes('<small>SOUL.md</small>') && !userRoot.includes('<small>IDENTITY.md</small>'),
            hasCleanTabLabels: userRoot.includes('><span>Về người dùng</span></button>')
                && userRoot.includes('><span>Giọng nói</span></button>')
                && userRoot.includes('><span>Danh tính</span></button>'),
            hasAllTabs: userRoot.includes('data-doc="USER.md"') && userRoot.includes('data-doc="SOUL.md"') && userRoot.includes('data-doc="IDENTITY.md"'),
            hasUserLabel: userRoot.includes('Về người dùng') && !userRoot.includes('Hồ sơ của bạn'),
            userOnlyFirst: userRoot.includes('id="file-USER.md"') && !userRoot.includes('id="file-SOUL.md"'),
            soulSwitched: soulRoot.includes('id="file-SOUL.md"') && !soulRoot.includes('id="file-USER.md"'),
            identitySwitched: identityRoot.includes('id="file-IDENTITY.md"') && !identityRoot.includes('id="file-USER.md"'),
            hasBotForm: userRoot.includes('bot-settings-form'),
            hasBotModelCard: userRoot.includes('bot-model-card'),
            hasBotHeading: userRoot.includes('Bạn và'),
        }};
    """, via="profile", emit="profile")
    assert out["title"] == "Hồ sơ chung"
    assert out["shared"] == "" and out["tab"] == "" and out["doc"] == "IDENTITY.md"
    assert out["noTabFilenames"] is True and out["hasCleanTabLabels"] is True
    assert out["hasAllTabs"] and out["hasUserLabel"] and out["userOnlyFirst"] and out["soulSwitched"] and out["identitySwitched"]
    assert out["hasBotForm"] is False and out["hasBotModelCard"] is False
    assert out["hasBotHeading"] is False
    assert out["noFooterBtns"] is True


def test_profile_section_cards_partial_edit_and_add(node) -> None:
    """USER.md ## headings split into section cards without splitting ## inside
    fenced code blocks; editing or adding one section preserves preamble, HTML
    comments, and sibling sections byte-for-byte."""
    raw_user = (
        "# User\\n\\nIntro note.\\n\\n"
        "## Habits\\n<!-- keep:comment -->\\n- Thói quen 1\\n\\n"
        "## Context\\n\\n```md\\n## not-a-heading\\n```\\n- Chi tiết B\\n\\n"
        "## Goals\\n"
    )
    out = _run(node, """
        const posted = [];
        let savedUser = {};
        installFetch((url, opts = {{}}) => {{
            if (url === '/api/bots') return {{ bots: {} }};
            if (url === '/api/conversations') return {{ conversations: [] }};
            if (url === '/api/config') return {};
            if (url === '/api/memory/stats') return {{ files: [
                {{ name: 'USER.md', content: savedUser }},
                {{ name: 'SOUL.md', content: 'plain soul' }},
            ] }};
            if (url === '/api/memory/canonical' && opts.method === 'POST') {{
                const payload = JSON.parse(opts.body);
                posted.push(payload);
                if (payload.name === 'USER.md') savedUser = payload.content;
                return {{ ok: true }};
            }}
            return {{}};
        }});
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const beforeHtml = document.querySelector('#profile-root').innerHTML;
        openSectionEdit('USER.md', 2, null);
        $('#section-edit-body').value = '- Mục tiêu mới';
        await submitSectionEdit({{ preventDefault() {{}} }});
        await tick(20);
        const afterGoalEdit = savedUser;
        openSectionEdit('USER.md', -1, null);
        $('#section-edit-title').value = 'Projects';
        $('#section-edit-body').value = '- Dự án WebUI';
        await submitSectionEdit({{ preventDefault() {{}} }});
        await tick(20);
        const afterAdd = savedUser;
        selectDoc('SOUL.md');
        const plainHtml = document.querySelector('#profile-root').innerHTML;
        return {{ beforeHtml, afterGoalEdit, afterAdd, plainHtml, posted }};
    """.format(repr(raw_user).replace("'", '"').replace("\\\\n", "\\n"), _ROSTER, _CONFIG), via="profile", emit="profile")
    assert 'profile-section-grid' in out["beforeHtml"]
    assert out["beforeHtml"].count('<details class="profile-section-card"') == 3
    assert ' open>' not in out["beforeHtml"]
    assert '<!-- keep:comment -->' in out["afterGoalEdit"]
    assert '## Context\n\n```md\n## not-a-heading\n```\n- Chi tiết B' in out["afterGoalEdit"]
    assert '## Goals\n\n- Mục tiêu mới' in out["afterGoalEdit"]
    assert '## Projects\n\n- Dự án WebUI' in out["afterAdd"]
    assert len(out["posted"]) == 2
    assert 'profile-section-grid' not in out["plainHtml"] and 'profile-content' in out["plainHtml"]


def test_profile_sidebar_select_shows_dossier_and_settings(node) -> None:
    """Sidebar coder click stays on Profile (?bot=coder) and renders the
    dossier card, embedded #bot-settings-form (with collapsed #bot-advanced),
    and TWIN.md."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        const replaced = [];
        const _orig = globalThis.history.replaceState.bind(globalThis.history);
        globalThis.history.replaceState = (...a) => {{ replaced.push(a[2]); return _orig(...a); }};
        await tick(); await boot(); await tick(20);
        selectBot('coder');
        await tick(30);
        const root = document.querySelector('#profile-root').innerHTML;
        const fields = document.querySelector('#bot-settings-fields').innerHTML;
        const tplHtml = document.querySelector('template#tpl-profile').innerHTML;
        return {{
            replaced, route: ctx.route, tab: state.tab, shared: selectedBot,
            title: $('#profile-title').textContent,
            root, fields,
            hasBotForm: root.includes('bot-settings-form'),
            hasSharedDoc: root.includes('file-USER'),
            noFooterShared: !tplHtml.includes('profile-shared'),
            cleanSaveDisabled: $('#save-bot-settings').disabled,
            cleanCancelHidden: $('#cancel-bot-settings').hidden,
            effectiveHiddenOnExplicit: $('#bot-effective').hidden,
        }};
    """, via="profile", emit="profile")
    assert out["replaced"] and out["replaced"][-1] == "/"
    assert out["tab"] == "coder" and out["shared"] == "coder"
    assert out["route"] == {"page": "profile", "search": "?bot=coder", "hash": ""}
    assert out["title"] == "Bạn và Coder"
    assert "Coder persona" in out["root"]
    assert "Tính cách" in out["root"]
    assert out["hasBotForm"] is True
    assert '<option value="m1" selected>' in out["fields"]
    assert '<details class="profile-bot-advanced" id="bot-advanced">' in out["fields"]
    assert out["hasSharedDoc"] is False
    assert out["noFooterShared"] is True
    assert out["cleanSaveDisabled"] is True
    assert out["cleanCancelHidden"] is True
    assert out["effectiveHiddenOnExplicit"] is True


def test_profile_deep_links_and_ghost(node) -> None:
    """?bot=coder / ?bot=thyca select directly; ?bot=ghost preserves the id
    with an honest error — never falling back to Thyca."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.pathname = '/ui/profile/index.html';
        const probe = async (search) => {{
            globalThis.location.search = search;
            state.tab = '';
            await boot(); await tick(20);
            const root = document.querySelector('#profile-root').innerHTML;
            return {{ selected: state.tab, shared: selectedBot,
                     search: ctx.route.search,
                     title: $('#profile-title').textContent,
                     root }};
        }};
        const a = await probe('?bot=coder');
        const b = await probe('?bot=thyca');
        const c = await probe('?bot=ghost');
        return {{ a, b, c }};
    """, via="profile", emit="profile")
    assert out["a"]["selected"] == "coder" and out["a"]["search"] == "?bot=coder"
    assert out["a"]["title"] == "Bạn và Coder"
    assert out["b"]["selected"] == "thyca" and "Thyca" in out["b"]["title"]
    assert out["c"]["selected"] == "ghost" and out["c"]["search"] == "?bot=ghost"
    assert out["c"]["shared"] == ""
    assert out["c"]["title"] == "Không tìm thấy bot"
    assert "Không tìm thấy bot" in out["c"]["root"]


def test_profile_shared_button_and_studio_shortcut(node) -> None:
    """'Hồ sơ chung' clears ?bot and restores shared docs; '#open-studio' opens
    the shared expression studio pre-selected to the current profile bot."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);
        openBotStudio('coder');
        const studioOpen = document.querySelector('#expressions-dialog').open;
        const studioBot = document.querySelector('#expression-bot').value;
        el.root.dispatch('click', {{ target: {{ closest: sel => sel === '[data-action]' ? {{ dataset: {{ action: 'show-shared' }} }} : null }} }});
        await tick();
        const sharedRoot = document.querySelector('#profile-root').innerHTML;
        return {{ studioOpen, studioBot, sharedRoot, tab: state.tab, shared: selectedBot, title: $('#profile-title').textContent }};
    """, via="profile", emit="profile")
    assert out["studioOpen"] is True and out["studioBot"] == "coder"
    assert "file-USER" in out["sharedRoot"] and out["tab"] == "" and out["shared"] == ""
    assert out["title"] == "Hồ sơ chung"


def test_profile_canonical_edit_still_works(node) -> None:
    """Shared canonical file editing still posts /api/memory/canonical."""
    out = _run(node, f"""
        const posted = [];
        installFetch((url, opts = {{}}) => {{
            if (url === '/api/bots') return {{ bots: {_ROSTER} }};
            if (url === '/api/conversations') return {{ conversations: [] }};
            if (url === '/api/config') return {_CONFIG};
            if (url === '/api/memory/stats') return {{ files: [{{ name: 'USER.md', content: 'old' }}] }};
            if (url === '/api/memory/canonical' && opts.method === 'POST') {{
                posted.push(JSON.parse(opts.body));
                return {{ ok: true }};
            }}
            return {{}};
        }});
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        openEdit('USER.md', null);
        document.querySelector('#edit-content').value = 'new content';
        document.querySelector('#edit-form').dispatch('submit', {{ target: document.querySelector('#edit-form'), preventDefault() {{}} }});
        await tick(20);
        return {{ posted, toast: $('#toast').textContent,
                 status: document.querySelector('#profile-status').textContent }};
    """, via="profile", emit="profile")
    assert out["posted"] == [{"name": "USER.md", "content": "new content"}]
    assert "Đã lưu USER.md" in out["toast"] + out["status"]


def test_profile_sidebar_select_labels_scope(node) -> None:
    """The shared sidebar select buttons are relabeled page-locally."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const host = document.querySelector('#team-bot-list');
        ctx.sidebar.appendChild(host);
        host.replaceChildren();
        bots.length = 0;
        bots.push(...roster([{{ id: 'a', name: 'Alpha' }}]));
        const btn = document.createElement('button');
        btn.dataset.action = 'select'; btn.dataset.botId = 'a';
        btn.setAttribute('aria-label', 'Trò chuyện với Alpha');
        host.appendChild(btn);
        fixSidebarLabels();
        return {{ label: btn.getAttribute('aria-label') }};
    """, via="profile", emit="profile")
    assert out["label"] == "Hồ sơ của Alpha"


def test_profile_inline_editor_preview_dirty_guard_and_cancel(node) -> None:
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        openEdit('USER.md', null);
        const editingOpen = document.querySelector('#profile-root').innerHTML.includes('id="edit-form"');
        const dirtyInitially = $('#edit-dirty').hidden;
        $('#edit-content').value = 'Bản nháp mới';
        $('#edit-content').oninput();
        const dirtyAfterType = $('#edit-dirty').hidden;
        const previewHtml = $('#edit-preview').innerHTML;
        globalThis.confirm = () => false;
        selectDoc('SOUL.md');
        const stayedAfterDoc = state.doc === 'USER.md' && state.editing === 'USER.md';
        showTab('coder');
        const stayedOnDirtyEdit = state.tab === '' && state.editing === 'USER.md' && stayedAfterDoc;
        const canceledBlocked = closeEdit();
        globalThis.confirm = () => true;
        const canceledConfirmed = closeEdit();
        const afterCancelHtml = document.querySelector('#profile-root').innerHTML;
        return {{
            editingOpen, dirtyInitially, dirtyAfterType, previewHtml, stayedOnDirtyEdit,
            canceledBlocked, canceledConfirmed,
            stillEditing: afterCancelHtml.includes('id="edit-form"'),
            restoredAll: afterCancelHtml.includes('id="file-USER.md"')
                && afterCancelHtml.includes('data-doc="SOUL.md"')
                && afterCancelHtml.includes('data-doc="IDENTITY.md"'),
        }};
    """, via="profile", emit="profile")
    assert out == {
        "editingOpen": True, "dirtyInitially": True, "dirtyAfterType": False,
        "previewHtml": "<p>Bản nháp mới</p>", "stayedOnDirtyEdit": True,
        "canceledBlocked": False, "canceledConfirmed": True,
        "stillEditing": False, "restoredAll": True,
    }


def test_profile_bot_dossier_top_bar_navigates_shared_without_settings_button(node) -> None:
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        const markup = document.querySelector('#profile-root').innerHTML;
        const sharedOk = showShared();
        return {{
            hasBotGrid: markup.includes('profile-bot-grid')
                && markup.includes('profile-bot-aside')
                && markup.includes('id="bot-settings-form"')
                && markup.includes('id="open-studio"')
                && markup.includes('data-action="show-shared"')
                && !markup.includes('id="profile-open-settings"'),
            sharedOk, tabAfter: state.tab,
        }};
    """, via="profile", emit="profile")
    assert out == {"hasBotGrid": True, "sharedOk": True, "tabAfter": ""}


def test_profile_route_dirty_guard_and_reload_failure(node) -> None:
    """onRoute respects confirmDiscardEdit for both valid and unknown bot IDs,
    and a failed post-save stats reload still closes the editor without claiming ok."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        let failStats = false;
        installFetch((url, opts = {{}}) => {{
            if (url === '/api/bots') return {{ bots: {_ROSTER} }};
            if (url === '/api/conversations') return {{ conversations: [] }};
            if (url === '/api/config') return {_CONFIG};
            if (url === '/api/memory/stats') {{
                if (failStats) throw new Error('stats failed');
                return {{ files: [
                    {{ name: 'USER.md', content: 'old' }},
                    {{ name: 'SOUL.md', content: 'soul' }},
                ] }};
            }}
            if (url === '/api/memory/canonical' && opts.method === 'POST') return {{ ok: true }};
            return {{}};
        }});
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(20);
        openEdit('USER.md', null);
        $('#edit-content').value = 'dirty draft';
        $('#edit-content').oninput();
        globalThis.confirm = () => false;
        __controller.onRoute({{ page: 'profile', search: '?bot=coder', hash: '' }});
        const keptOnCoder = state.tab === '' && state.editing === 'USER.md' && ctx.route.search === '';
        __controller.onRoute({{ page: 'profile', search: '?bot=ghost', hash: '' }});
        const keptOnGhost = state.tab === '' && state.editing === 'USER.md' && ctx.route.search === '';
        failStats = true;
        await submitEdit({{ preventDefault() {{}} }});
        const rootHtml = document.querySelector('#profile-root').innerHTML;
        return {{
            keptOnCoder, keptOnGhost,
            closedForm: !rootHtml.includes('id="edit-form"'),
            updatedText: rootHtml.includes('dirty draft'),
            statusText: $('#profile-status').textContent,
            statusClass: $('#profile-status').className,
        }};
    """, via="profile", emit="profile")
    assert out["keptOnCoder"] is True and out["keptOnGhost"] is True
    assert out["closedForm"] is True and out["updatedText"] is True
    assert "chưa tải lại được" in out["statusText"] and "s-error" in out["statusClass"]


def test_profile_provider_no_bot_scope(node) -> None:
    """Provider page has no bot editor; sidebar selection navigates to the
    Profile dossier (?bot=<id>)."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.search = '';
        globalThis.location.pathname = '/ui/provider/index.html';
        await tick(); await boot(); await tick(20);
        selectBot('coder');
        await tick();
        const noBotForm = !document.querySelector('.workspace').innerHTML.includes('bot-settings-form');
        const sharedVisible = !document.querySelector('#connection-editor').hidden
            && !document.querySelector('#save-status').hidden;
        return {{ navigated: ctx.lastNavigation, noBotForm, sharedVisible }};
    """, via="provider", emit="provider")
    assert out["navigated"] == {"page": "profile", "search": "?bot=coder"}
    assert out["noBotForm"] is True
    assert out["sharedVisible"] is True



def test_profile_bot_settings_forms_are_isolated(node) -> None:
    """Profile loads each bot's own settings (coder vs thyca), keeping them
    strictly isolated across bot switches."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);
        const coderFields = $('#bot-settings-fields').innerHTML;
        selectBot('thyca');
        await tick(30);
        const thycaFields = $('#bot-settings-fields').innerHTML;
        return {{ coderFields, thycaFields }};
    """, via="profile", emit="profile")
    assert '<option value="m1" selected>' in out["coderFields"]
    assert '<option value="custom" selected>' in out["coderFields"]
    assert "card-provider" in out["coderFields"]
    assert '<option value="default" selected>' in out["thycaFields"]
    assert '<option value="m1" selected>' not in out["thycaFields"]


def test_profile_bot_save_posts_settings_only(node) -> None:
    """Per-bot save inside Profile POSTs only /api/bots/<id>/settings,
    updates the live roster bot model, and fires thyca:bot-settings-saved."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        const configPosts = [];
        const savedEvents = [];
        window.addEventListener('thyca:bot-settings-saved', e => savedEvents.push(e.detail));
        const origFetch = globalThis.fetch;
        globalThis.fetch = async (url, opts = {{}}) => {{
            if (String(url).endsWith('/api/config') && opts.method === 'POST') configPosts.push(1);
            if (String(url).endsWith('/api/memory/canonical') && opts.method === 'POST') configPosts.push(1);
            return origFetch(url, opts);
        }};
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);
        const cleanDisabled = $('#save-bot-settings').disabled;
        $('#bot-model').value = 'default';
        $('#bot-model').onchange();
        const dirtyEnabled = !$('#save-bot-settings').disabled;
        const dirtyCancelVisible = !$('#cancel-bot-settings').hidden;
        const effectiveShownOnDefault = !$('#bot-effective').hidden;
        $('#cancel-bot-settings').onclick();
        const afterCancelModel = $('#bot-model').value;
        const afterCancelDisabled = $('#save-bot-settings').disabled;
        $('#bot-model').value = 'default';
        $('#bot-model').onchange();
        $('#bot-thinking').value = 'low';
        $('#bot-thinking').onchange();
        await submitBotSettings();
        await tick(30);
        return {{
            posted: window.__posted || [],
            configPosts,
            savedEvents,
            liveModel: getBot('coder').model,
            hint: $('#bot-settings-hint').textContent,
            cleanDisabled, dirtyEnabled, dirtyCancelVisible, effectiveShownOnDefault,
            afterCancelModel, afterCancelDisabled,
            disabledAfterSave: $('#save-bot-settings').disabled,
            cancelHiddenAfterSave: $('#cancel-bot-settings').hidden,
        }};
    """, via="profile", emit="profile")
    assert out["posted"] == [{
        "url": "/api/bots/coder/settings",
        "body": {"model": {"id": "default", "thinking": "low"}},
    }]
    assert out["configPosts"] == []
    assert out["cleanDisabled"] is True and out["dirtyEnabled"] is True and out["dirtyCancelVisible"] is True
    assert out["effectiveShownOnDefault"] is True
    assert out["afterCancelModel"] == "m1" and out["afterCancelDisabled"] is True
    assert out["hint"] == "Đã lưu." and out["disabledAfterSave"] is True and out["cancelHiddenAfterSave"] is True
    assert out["liveModel"] == {"id": "default", "thinking": "low"}
    assert out["savedEvents"] and out["savedEvents"][0]["botId"] == "coder"


def test_profile_bot_provider_preview_and_default_routing(node) -> None:
    """Changing #bot-model updates #bot-provider-preview (draft vs saved) and
    #bot-thinking choices; default inheritance routes via defaultModel's provider."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        {_mock_fetch(_ROSTER, _CONFIG)}
        globalThis.location.search = '?bot=thyca';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);
        const before = $('#bot-provider-preview').textContent;
        $('#bot-model').value = 'm1';
        $('#bot-model').onchange();
        await tick();
        const after = $('#bot-provider-preview').textContent;
        const effortOpts = [...$('#bot-thinking').children].map(o => o.value);
        return {{ before, after, effortOpts }};
    """, via="profile", emit="profile")
    assert "default" in out["before"] and "chưa lưu" not in out["before"]
    assert "card-provider" in out["after"] and "chưa lưu" in out["after"]
    assert out["effortOpts"] == ["", "low", "custom"]

    out2 = _run(node, """
        const SETTINGS = {};
        {}
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);
        return {{ fields: $('#bot-settings-fields').innerHTML,
                 preview: $('#bot-provider-preview').textContent }};
    """.format(_SETTINGS, _mock_fetch(_ROSTER, _CONFIG.replace(
        "defaultModel: 'm2'", "defaultModel: 'm1'"))), via="profile", emit="profile")
    assert "Theo mặc định chung (m1)" in out2["fields"]
    assert "card-provider" in out2["preview"] and " (dự kiến" not in out2["preview"]


def test_profile_bot_unknown_model_and_xss_safe_label(node) -> None:
    """A saved model missing from catalog stays selected with an honest notice
    and keeps Save disabled; hostile defaultModel strings are HTML-escaped."""
    out = _run(node, f"""
        const SETTINGS = {{
            '/api/bots/thyca/settings': {{ bot_id: 'thyca', model: {{ id: 'default', thinking: null }},
                effective: {{ model: 'm2', provider: 'default', thinking: 'high' }} }},
            '/api/bots/coder/settings': {{ bot_id: 'coder', model: {{ id: 'retired-model', thinking: 'low' }},
                effective: {{ model: 'm2', provider: 'default', thinking: 'low' }} }},
        }};
        {_mock_fetch(_ROSTER, _CONFIG.replace("defaultModel: 'm2'", "defaultModel: '<img src=x onerror=1>'"))}
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);
        const fields = $('#bot-settings-fields').innerHTML;
        return {{
            fields,
            saveDisabled: $('#save-bot-settings').disabled,
            preview: $('#bot-provider-preview').textContent,
        }};
    """, via="profile", emit="profile")
    assert '<option value="retired-model" selected>' in out["fields"]
    assert "không còn trong danh mục" in out["fields"]
    assert 'id="bot-model-notice" hidden' not in out["fields"]
    assert "<img src=x" not in out["fields"]
    assert "&lt;img src=x onerror=1&gt;" in out["fields"]
    assert out["saveDisabled"] is True
    assert out["preview"] == "Provider: — (model không còn trong danh mục)"


def test_profile_bot_advanced_limits_default_collapsed_validation_and_reset(node) -> None:
    """Per-bot limits live inside collapsed #bot-advanced ('Nâng cao'), enforce
    contextTokens 32000..1000000, preserve drafts across bot switches, save
    custom overrides, and reset cleanly via #bot-limits-reset."""
    out = _run(node, rf"""
        const SETTINGS = {{
            '/api/bots/thyca/settings': {{
                bot_id: 'thyca',
                model: {{ id: 'default', thinking: null }},
                limits: {{ loopMax: null, hotTailKB: null, contextTokens: null, softTimeoutS: null }},
                effective: {{
                    model: 'm2', provider: 'default', thinking: 'high',
                    limits: {{ loopMax: 200, hotTailKB: 4, contextTokens: 272000, softTimeoutS: 60 }},
                }},
            }},
            '/api/bots/coder/settings': {{
                bot_id: 'coder',
                model: {{ id: 'm1', thinking: 'custom' }},
                limits: {{ loopMax: null, hotTailKB: null, contextTokens: null, softTimeoutS: null }},
                effective: {{
                    model: 'm1', provider: 'card-provider', thinking: 'custom',
                    limits: {{ loopMax: 200, hotTailKB: 4, contextTokens: 272000, softTimeoutS: 60 }},
                }},
            }},
        }};
        const posted = [];
        installFetch((url, opts = {{}}) => {{
            if (url === '/api/bots') return {{ bots: {_ROSTER} }};
            if (url === '/api/conversations') return {{ conversations: [] }};
            if (url === '/api/memory/stats') return {{ files: [{{ name: 'USER.md', content: 'u' }}] }};
            if (url === '/api/config') return {{
                values: {{
                    providers: {{ default: {{}}, 'card-provider': {{}} }},
                    defaultProvider: 'default',
                    defaultModel: 'm2',
                    models: {{ m1: {{ provider: 'card-provider', reasoningEfforts: ['low', 'custom'] }}, m2: {{ provider: 'default' }} }},
                    limits: {{ loopMax: 200, hotTailKB: 4, contextTokens: 272000, softTimeoutS: 60 }},
                }},
                schema: {{ sections: [
                    {{ key: 'provider', fields: [{{ key: 'provider.reasoningEffort', choices: ['low', 'medium', 'high', 'xhigh', 'max'] }}] }},
                    {{ key: 'limits', fields: [
                        {{ key: 'limits.loopMax', min: 1, max: 200, default: 200 }},
                        {{ key: 'limits.hotTailKB', min: 1, max: 64, default: 4 }},
                        {{ key: 'limits.contextTokens', min: 32000, max: 1000000, default: 272000 }},
                        {{ key: 'limits.softTimeoutS', min: 1, max: 300, default: 60 }},
                    ] }},
                ] }},
            }};
            if (url.endsWith('/settings') && opts.method === 'POST') {{
                const body = JSON.parse(opts.body);
                posted.push({{ url, body }});
                const prev = SETTINGS[url];
                const nextLimits = body.limits === undefined
                    ? prev.limits
                    : (body.limits === null
                        ? {{ loopMax: null, hotTailKB: null, contextTokens: null, softTimeoutS: null }}
                        : {{ loopMax: null, hotTailKB: null, contextTokens: null, softTimeoutS: null, ...body.limits }});
                SETTINGS[url] = {{
                    ...prev,
                    model: body.model,
                    limits: nextLimits,
                    effective: {{
                        ...prev.effective,
                        limits: {{
                            loopMax: nextLimits.loopMax ?? 200,
                            hotTailKB: nextLimits.hotTailKB ?? 4,
                            contextTokens: nextLimits.contextTokens ?? 272000,
                            softTimeoutS: nextLimits.softTimeoutS ?? 60,
                        }},
                    }},
                }};
                return SETTINGS[url];
            }}
            if (SETTINGS[url]) return SETTINGS[url];
            return {{}};
        }});
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);

        const defaultBadge = $('#bot-limits-badge').textContent;
        const defaultResetHidden = $('#bot-limits-reset').hidden;
        const fieldsHtml = $('#bot-settings-fields').innerHTML;
        const collapsedByDefault = fieldsHtml.includes('<details class="profile-bot-advanced" id="bot-advanced">');

        $('#bot-limit-contextTokens').value = '31999';
        await submitBotSettings();
        const lowErr = $('#bot-settings-hint').textContent;
        const openedOnErr = $('#bot-advanced').open;
        const postsAfterLow = posted.length;

        $('#bot-limit-contextTokens').value = '1000001';
        await submitBotSettings();
        const highErr = $('#bot-settings-hint').textContent;
        const postsAfterHigh = posted.length;

        $('#bot-limit-contextTokens').value = '128000';
        $('#bot-limit-loopMax').value = '50';
        $('#bot-limit-loopMax').oninput();
        const dirtyBadge = $('#bot-limits-badge').textContent;
        const dirtyResetHidden = $('#bot-limits-reset').hidden;

        selectBot('thyca');
        await tick(30);
        const thycaBadge = $('#bot-limits-badge').textContent;

        selectBot('coder');
        await tick(30);
        const restoredDraft = {{
            ctx: $('#bot-limit-contextTokens').value,
            loop: $('#bot-limit-loopMax').value,
        }};

        await submitBotSettings();
        await tick(20);
        const afterSaveBadge = $('#bot-limits-badge').textContent;

        $('#bot-limits-reset').onclick();
        await submitBotSettings();
        await tick(20);
        const afterResetBadge = $('#bot-limits-badge').textContent;

        return {{
            defaultBadge, defaultResetHidden, collapsedByDefault,
            lowErr, openedOnErr, postsAfterLow,
            highErr, postsAfterHigh,
            dirtyBadge, dirtyResetHidden,
            thycaBadge, restoredDraft,
            afterSaveBadge, afterResetBadge,
            posted,
        }};
    """, via="profile", emit="profile")
    assert out["defaultBadge"] == "Mặc định" and out["defaultResetHidden"] is True
    assert out["collapsedByDefault"] is True
    assert "32000" in out["lowErr"] and "1000000" in out["lowErr"]
    assert out["openedOnErr"] is True and out["postsAfterLow"] == 0
    assert "32000" in out["highErr"] and "1000000" in out["highErr"]
    assert out["postsAfterHigh"] == 0
    assert out["dirtyBadge"] == "Tự cấu hình" and out["dirtyResetHidden"] is False
    assert out["thycaBadge"] == "Mặc định"
    assert out["restoredDraft"] == {"ctx": "128000", "loop": "50"}
    assert out["afterSaveBadge"] == "Tự cấu hình"
    assert out["afterResetBadge"] == "Mặc định"
    assert out["posted"] == [
        {
            "url": "/api/bots/coder/settings",
            "body": {
                "model": {"id": "m1", "thinking": "custom"},
                "limits": {
                    "loopMax": 50,
                    "hotTailKB": None,
                    "contextTokens": 128000,
                    "softTimeoutS": None,
                },
            },
        },
        {
            "url": "/api/bots/coder/settings",
            "body": {
                "model": {"id": "m1", "thinking": "custom"},
                "limits": None,
            },
        },
    ]


def test_profile_bot_catalog_refreshes_on_config_saved(node) -> None:
    """When thyca:config-saved fires while Profile is active, the bot model
    dropdown reloads the fresh catalog without discarding unsaved drafts."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        let extraModel = false;
        installFetch((url) => {{
            if (url === '/api/bots') return {{ bots: {_ROSTER} }};
            if (url === '/api/conversations') return {{ conversations: [] }};
            if (url === '/api/memory/stats') return {{ files: [{{ name: 'USER.md', content: 'u' }}] }};
            if (url === '/api/config') return {{
                values: {{
                    defaultProvider: 'default',
                    defaultModel: 'm2',
                    providers: {{ default: {{}}, 'card-provider': {{}} }},
                    models: extraModel
                        ? {{ m1: {{ provider: 'card-provider' }}, m2: {{}}, m3: {{ provider: 'default' }} }}
                        : {{ m1: {{ provider: 'card-provider' }}, m2: {{}} }},
                }},
                schema: {{ sections: [] }},
            }};
            if (SETTINGS[url]) return SETTINGS[url];
            return {{}};
        }});
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);
        const before = $('#bot-settings-fields').innerHTML.includes('value="m3"');
        extraModel = true;
        window.dispatchEvent(new Event('thyca:config-saved'));
        await tick(30);
        const after = $('#bot-settings-fields').innerHTML.includes('value="m3"');
        return {{ before, after }};
    """, via="profile", emit="profile")
    assert out == {"before": False, "after": True}


def test_profile_inherited_thinking_hint_clear_and_open_sections_reset(node) -> None:
    """Switching #bot-thinking to inherited resolves the provider default (not the
    old override), reverting an invalid limit clears the stale error hint, and
    showShared/submitEdit clear openSections."""
    out = _run(node, f"""
        const SETTINGS = {_SETTINGS};
        let savedUser = '# User\\n\\n## One\\n- A\\n\\n## Two\\n- B\\n';
        installFetch((url, opts = {{}}) => {{
            if (url === '/api/bots') return {{ bots: {_ROSTER} }};
            if (url === '/api/conversations') return {{ conversations: [] }};
            if (url === '/api/config') return {_CONFIG};
            if (url === '/api/memory/stats') return {{ files: [
                {{ name: 'USER.md', content: savedUser }},
                {{ name: 'SOUL.md', content: '# Soul\\n\\n## Voice\\n- Calm\\n' }},
            ] }};
            if (url === '/api/memory/canonical' && opts.method === 'POST') {{
                const body = JSON.parse(opts.body);
                if (body.name === 'USER.md') savedUser = body.content;
                return {{ ok: true }};
            }}
            if (url === '/api/bots/coder/settings' && opts.method === 'POST') {{
                throw new Error('server busy');
            }}
            if (SETTINGS[url]) return SETTINGS[url];
            return {{}};
        }});
        globalThis.location.search = '?bot=coder';
        globalThis.location.pathname = '/ui/profile/index.html';
        await tick(); await boot(); await tick(30);
        $('#bot-thinking').value = '';
        $('#bot-thinking').onchange();
        const inheritedEff = $('#bot-effective').textContent;
        const inheritedHidden = $('#bot-effective').hidden;
        await submitBotSettings();
        const saveFailHint = $('#bot-settings-hint').textContent;
        const saveRetryEnabled = !$('#save-bot-settings').disabled;
        $('#bot-thinking').value = 'low';
        $('#bot-thinking').onchange();
        const hintAfterNextEdit = $('#bot-settings-hint').textContent;
        $('#bot-thinking').value = 'custom';
        $('#bot-thinking').onchange();
        $('#bot-limit-contextTokens').value = '100';
        await submitBotSettings();
        const errHint = $('#bot-settings-hint').textContent;
        $('#bot-limit-contextTokens').value = '';
        $('#bot-limit-contextTokens').oninput();
        const hintAfterRevert = $('#bot-settings-hint').textContent;

        state.openSections.add(0);
        globalThis.requestAnimationFrame = () => {{}};
        showShared('file-SOUL.md');
        const openAfterShared = state.openSections.size;
        state.openSections.add(0);
        openEdit('USER.md', null);
        $('#edit-content').value = '# User\\n\\n## Renamed\\n- C\\n';
        await submitEdit({{ preventDefault() {{}} }});
        const openAfterFullSave = state.openSections.size;
        return {{
            inheritedEff, inheritedHidden, saveFailHint, saveRetryEnabled,
            hintAfterNextEdit, errHint, hintAfterRevert,
            openAfterShared, openAfterFullSave,
        }};
    """, via="profile", emit="profile")
    assert out["inheritedHidden"] is False
    assert out["inheritedEff"] == "Đang dùng: m1 · low"
    assert "Không lưu được:" in out["saveFailHint"] and "server busy" in out["saveFailHint"]
    assert out["saveRetryEnabled"] is True
    assert out["hintAfterNextEdit"] == "Chưa lưu"
    assert "32000" in out["errHint"]
    assert out["hintAfterRevert"] == ""
    assert out["openAfterShared"] == 0 and out["openAfterFullSave"] == 0
