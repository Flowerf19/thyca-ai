"""Node tests for the chat composer (ui/chat/js/data.js + chat.js).

Same classic-script bundle harness as test_team_frontend: ``_run``/``node``
are reused from that module so the bundles stay identical.
"""
from test_team_frontend import _run, node  # noqa: F401  (fixture)


def test_chat_composer_has_no_per_turn_model_or_thinking_selects(node) -> None:
    """The chat composer no longer shows model/thinking selects, and
    sendTurn omits both — the backend resolves the recipient card's
    settings."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca', name: 'Thyca' }, { id: 'coder', name: 'Coder' }]));
        renderTeam();
        currentConversation = 'dm:coder'; selectedBot = 'coder';
        const bodies = [];
        installFetch((url, opts) => {
            if (url === '/api/bots/coder/dm/turn/stream' && opts.method === 'POST') {
                bodies.push(JSON.parse(opts.body));
                return { ok: true, status: 200, body: streamOf([
                    { type: 'turn.completed', detail: { messages: [] } },
                ]) };
            }
            if (url === '/api/conversations') return { conversations: [] };
            return {};
        });
        const noModel = document.querySelectorAll('#composer-model').length === 0;
        const noEffort = document.querySelectorAll('#thinking-effort').length === 0;
        await sendTurn('hi', 'coder');
        return { noModel, noEffort, bodies };
    """, via="chat")
    assert out["noModel"] is True and out["noEffort"] is True
    assert out["bodies"] == [{"text": "hi", "retry": False}]


def test_chat_switching_bots_keeps_composer_label_current(node) -> None:
    """updateComposer still rewrites the letter label on an actual
    bot/room change; plain appearance-only repaints touch nothing else."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'thyca', name: 'Thyca' }, { id: 'coder', name: 'Coder' }]));
        renderTeam();
        currentConversation = 'dm:thyca'; selectedBot = 'thyca';
        updateComposer();
        const first = $('#composer-label-text').textContent;
        selectedBot = 'coder';
        updateComposer();
        return { first, second: $('#composer-label-text').textContent,
                 ph: $('#message').placeholder };
    """, via="chat")
    assert out["first"] == "Gửi Thyca,"
    assert out["second"] == "Gửi Coder,"
    assert "Coder" in out["ph"]


def test_chat_stale_navigation_never_repaints_newer_bot(node) -> None:
    """A slow DM fetch for bot A resolving after the user selected B
    is dropped: B's transcript, header and selection stay."""
    out = _run(node, """
        bots.length = 0;
        bots.push(...roster([{ id: 'a', name: 'Alpha' }, { id: 'b', name: 'Beta' }]));
        renderTeam();
        currentConversation = ''; selectedBot = '';
        const pending = [];
        installFetch((url, opts) => {
            if (url === '/api/bots/a/dm')
                return { ok: true, status: 200, json: () => new Promise(r => pending.push(r)) };
            if (url === '/api/bots/b/dm') return { kind: 'dm', bot_id: 'b', messages: [] };
            if (url === '/api/conversations') return { conversations: [] };
            return {};
        });
        const pA = selectBot('a');                       // DM fetch stays pending
        await tick();
        await selectBot('b');                            // B completes fully
        pending.forEach(r => r({ kind: 'dm', bot_id: 'a', messages: [] }));
        await pA; await tick();
        return { sel: selectedBot, conversation: currentConversation,
                 title: $('#chat-title').textContent };
    """, via="chat")
    assert out["sel"] == "b" and out["conversation"] == "dm:b"
    assert out["title"] == "Beta"
