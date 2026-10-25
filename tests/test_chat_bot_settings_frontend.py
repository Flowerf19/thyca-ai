"""Node tests for chat composer inheritance (ui/chat/js/data.js + chat.js).

Same classic-script bundle harness as test_team_frontend: ``_run``/``node``
are reused from that module so the bundles stay identical.
"""
from test_team_frontend import _run, node  # noqa: F401  (fixture)

_CONFIG = (
    "{ values: { defaultModel: 'm2', models: { m1: { reasoningEfforts: ['low', 'custom'] }, m2: {} } },"
    " schema: { sections: ["
    "  { key: 'limits', label: 'L', fields: [] },"
    "  { key: 'provider', label: 'P', fields: [{ key: 'provider.reasoningEffort', label: 'Suy luận', choices: ['low', 'high', 'max'] }] },"  # noqa: E501
    "  ] } }"
)


def test_chat_composer_inherits_by_default(node) -> None:
    """Model/effort selects default to inheritance and sendTurn omits both —
    the backend resolves the recipient card's settings."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca', name: 'Thyca' }, { id: 'coder', name: 'Coder' }]));
        renderTeam();
        currentSession = 's1'; selectedBot = 'coder';
        const bodies = [];
        installFetch((url, opts) => {
            if (url === '/api/config') return %s;
            if (url === '/api/bots/coder/settings')
                return { bot_id: 'coder', model: { id: 'default', thinking: null },
                         effective: { model: 'm1', provider: 'card-provider', thinking: 'low' } };
            if (url === '/api/sessions/s1/turn/stream' && opts.method === 'POST') {
                bodies.push(JSON.parse(opts.body));
                return { ok: true, status: 200, body: streamOf([
                    { type: 'turn.completed', detail: { title: 'T', messages: [] } },
                ]) };
            }
            if (url === '/api/sessions') return { sessions: [] };
            return {};
        });
        await loadConfig();
        const inheritOpts = $$('#composer-model option').map(o => [o.value, o.textContent]);
        await refreshComposerInherit('coder');
        const modelLabel = document.querySelector('#composer-model').options[0].textContent;
        const effortLabel = document.querySelector('#thinking-effort').options[0].textContent;
        const inheritEffortOpts = $$('#thinking-effort option').map(o => o.value);
        await sendTurn('hi', 'coder');
        return { inheritOpts, modelLabel, effortLabel, inheritEffortOpts, bodies };
    """ % _CONFIG, via="chat")
    assert out["inheritOpts"][0][0] == "" and "Theo cấu hình bot" in out["inheritOpts"][0][1]
    assert "m1" in [o[0] for o in out["inheritOpts"]]
    assert "minimax" not in str(out["inheritOpts"])          # no literal mock model
    assert "m1" in out["modelLabel"] and "card-provider" in out["modelLabel"]
    assert "low" in out["effortLabel"]                      # effective effort, not model/provider
    assert "card-provider" not in out["effortLabel"]
    # Inheriting from m1 (custom set low/custom): only its set is offered.
    assert out["inheritEffortOpts"] == ["", "low", "custom"]
    assert out["bodies"] == [{"text": "hi", "retry": False}]


def test_chat_switching_bots_resets_and_effort_follows_model(node) -> None:
    """Switching bots resets per-turn choices to inheritance; explicit model
    choices stay model-aware; appearance-only updates keep explicit choices."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca', name: 'Thyca' }, { id: 'coder', name: 'Coder' }]));
        renderTeam();
        currentSession = 's1'; selectedBot = 'thyca';
        installFetch((url) => {
            if (url === '/api/config') return %s;
            if (url === '/api/bots/coder/settings')
                return { bot_id: 'coder', model: { id: 'm1', thinking: 'low' },
                         effective: { model: 'm1', provider: 'default', thinking: 'low' } };
            return {};
        });
        await loadConfig();
        document.querySelector('#composer-model').value = 'm1';
        document.querySelector('#composer-model').dispatch('change', { target: document.querySelector('#composer-model') });
        const perModel = $$('#thinking-effort option').map(o => o.value);
        document.querySelector('#thinking-effort').value = 'custom';
        // switching bots resets per-turn choices to inheritance
        selectedBot = 'coder';
        updateComposer();
        const reset = [document.querySelector('#composer-model').value, document.querySelector('#thinking-effort').value];
        // same-bot appearance-only update keeps explicit choices
        document.querySelector('#composer-model').value = 'm1';
        document.querySelector('#composer-model').dispatch('change', { target: document.querySelector('#composer-model') });
        document.querySelector('#thinking-effort').value = 'custom';
        updateComposer();
        const kept = [document.querySelector('#composer-model').value, document.querySelector('#thinking-effort').value];
        document.querySelector('#composer-model').value = '';
        document.querySelector('#composer-model').dispatch('change', { target: document.querySelector('#composer-model') });
        const global = $$('#thinking-effort option').map(o => o.value);
        return { perModel, reset, kept, global };
    """ % _CONFIG, via="chat")
    assert out["perModel"] == ["", "low", "custom"]
    assert out["reset"] == ["", ""]
    assert out["kept"] == ["m1", "custom"]
    assert out["global"] == ["", "low", "high", "max"]


def test_chat_stale_settings_never_repaints_newer_bot(node) -> None:
    """A slow settings fetch for bot A resolving after the user selected B
    is dropped: B's transcript, title, selection and inherit label stay."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'Alpha' }, { id: 'b', name: 'Beta' }]));
        renderTeam();
        currentSession = ''; selectedBot = '';
        const pending = [];
        installFetch((url, opts) => {
            if (String(url).endsWith('/a/settings'))
                return { ok: true, status: 200, json: () => new Promise(r => pending.push(r)) };
            if (String(url).endsWith('/b/settings'))
                return { bot_id: 'b', model: { id: 'default', thinking: null },
                         effective: { model: 'm-b', provider: 'p-b', thinking: 'low' } };
            if (url === '/api/bots/a/dm') return { id: 'da', title: 'A', messages: [] };
            if (url === '/api/bots/b/dm') return { id: 'db', title: 'B', messages: [] };
            if (url === '/api/config') return %s;
            if (url === '/api/sessions') return { sessions: [] };
            return {};
        });
        await loadConfig();
        const pA = selectBot('a');                       // settings fetch stays pending
        await tick();
        await selectBot('b');                            // B completes fully
        pending.forEach(r => r({ bot_id: 'a', model: { id: 'default', thinking: null },
                                 effective: { model: 'm-a', provider: 'p-a', thinking: 'high' } }));
        await pA; await tick();
        return { sel: selectedBot, session: currentSession,
                 title: $('#chat-title').textContent,
                 label: document.querySelector('#composer-model').options[0].textContent };
    """ % _CONFIG, via="chat")
    assert out["sel"] == "b" and out["session"] == "db"
    assert out["title"] == "B"
    assert "m-b" in out["label"] and "m-a" not in out["label"]


def test_chat_late_config_preserves_settings_state(node) -> None:
    """Settings resolving before config still drive inherit labels and the
    effective model's effort set once config arrives."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'coder', name: 'Coder' }]));
        renderTeam();
        currentSession = 's1'; selectedBot = 'coder';
        installFetch((url) => {
            if (url === '/api/config') return %s;
            if (url === '/api/bots/coder/settings')
                return { bot_id: 'coder', model: { id: 'm1', thinking: null },
                         effective: { model: 'm1', provider: 'default', thinking: 'low' } };
            if (url === '/api/sessions') return { sessions: [] };
            return {};
        });
        await refreshComposerInherit('coder');   // settings first, config missing
        await loadConfig();                      // config arrives late
        const opts = $$('#composer-model option');
        return { count: opts.length,
                 label: document.querySelector('#composer-model').options[0].textContent,
                 effort: $$('#thinking-effort option').map(o => o.value) };
    """ % _CONFIG, via="chat")
    assert "m1" in out["label"]
    assert out["effort"] == ["", "low", "custom"]


def test_config_saved_refreshes_chat_catalog_and_inheritance_without_losing_drafts(node) -> None:
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'coder', name: 'Coder' }])); renderTeam();
        currentSession = 's1'; selectedBot = 'coder';
        let config = { values: { defaultModel: 'm1', defaultProvider: 'alpha', providers: { alpha: {} },
                                models: { m1: { reasoningEfforts: ['low'] }, m2: {} } },
                       schema: { sections: [{ key: 'provider', fields: [{ key: 'provider.reasoningEffort', choices: ['low', 'high'] }] }] } };
        let configGets = 0, settingsGets = 0;
        installFetch(url => {
            if (url === '/api/bots') return { bots: [{ id: 'coder', name: 'Coder' }] };
            if (url === '/api/sessions') return { sessions: [] };
            if (url === '/api/config') { configGets++; return config; }
            if (url === '/api/bots/coder/settings') {
                settingsGets++;
                return { bot_id: 'coder', model: { id: 'default', thinking: null },
                         effective: { model: config.values.defaultModel, provider: config.values.defaultProvider, thinking: 'high' } };
            }
            return {};
        });
        await tick(10); currentSession = 's1'; selectedBot = 'coder';
        await loadConfig(); await refreshComposerInherit('coder');
        const select = document.querySelector('#composer-model'); select.value = 'm2';
        document.querySelector('#thinking-effort').value = 'high';
        document.querySelector('#message').value = 'unsent composer draft';
        config = { values: { defaultModel: 'm3', defaultProvider: 'beta', providers: { alpha: {}, beta: {} },
                             models: { m1: { reasoningEfforts: ['low'] }, m2: {}, m3: { reasoningEfforts: ['high'] } } },
                   schema: { sections: [{ key: 'provider', fields: [{ key: 'provider.reasoningEffort', choices: ['low', 'high'] }] }] } };
        selectedBot = 'coder'; currentSession = 's1';
        window.dispatchEvent(new Event('thyca:config-saved'));
        await tick(20);
        return { model: select.value, message: document.querySelector('#message').value,
                 label: select.options[0].textContent, options: $$('#composer-model option').map(option => option.value),
                 configGets, settingsGets };
    """, via="chat")
    assert out["model"] == "m2" and out["message"] == "unsent composer draft"
    assert out["settingsGets"] == 2, out
    assert "m3" in out["label"] and "beta" in out["label"] and "m3" in out["options"], out
    assert out["configGets"] == 2 and out["settingsGets"] == 2


def test_chat_config_request_generation_drops_older_catalog_after_saved_event(node) -> None:
    out = _run(node, """
        bots.length = 0; bots.push(...roster([{ id: 'coder', name: 'Coder' }])); renderTeam();
        selectedBot = 'coder'; currentSession = 's1';
        let releaseOld, gets = 0;
        const fresh = { values: { defaultModel: 'new-default', defaultProvider: 'fresh', providers: { fresh: {} }, models: { fresh: {} } }, schema: { sections: [] } };
        installFetch(url => {
            if (url === '/api/config') {
                gets++;
                return gets === 1 ? new Promise(resolve => { releaseOld = () => resolve({ values: { defaultModel: 'stale', models: { stale: {} } }, schema: { sections: [] } }); }) : fresh;
            }
            if (url === '/api/bots/coder/settings') return { bot_id: 'coder', model: { id: 'default' }, effective: { model: 'new-default', provider: 'fresh', thinking: 'high' } };
            return {};
        });
        const old = loadConfig(); await tick();
        window.dispatchEvent(new Event('thyca:config-saved')); await tick(10);
        releaseOld(); await old; await tick(10);
        return { gets, options: $$('#composer-model option').map(option => option.value), label: $('#composer-model').options[0].textContent };
    """, via="chat")
    assert out["gets"] == 2 and "new-default" in out["options"] and "stale" not in out["options"]
    assert "new-default" in out["label"] and "fresh" in out["label"]
