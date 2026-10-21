// Chat data layer — shared backend calls live in ../share/data.js.
// API: GET /api/bots, GET /api/sessions, GET /api/config,
//      GET /api/bots/<id>/dm, GET /api/sessions/<id>,
//      GET /api/bots/<id>/rooms/<session>/operations.
'use strict';
const histories = new Map();
const runs = new Map();          // sessionId -> in-flight turn token (backend allows one per session)
let currentSession = '', attachment = null;

async function loadConfig() {
  try {
    const { values, schema } = await getJson('/api/config');   // API: GET /api/config
    const select = $('#composer-model');
    if (select) {
      const models = values?.models && typeof values.models === 'object' ? Object.keys(values.models) : [];
      const current = values?.provider?.model || '';
      select.replaceChildren(...[...new Set([current, ...models].filter(Boolean))]
        .map(id => Object.assign(document.createElement('option'), { value: id, textContent: id })));
      if (current) select.value = current;   // empty -> first option stays selected
    }
    const effort = $('#thinking-effort');
    const field = (schema?.sections || []).flatMap(s => s.fields || []).find(f => f.key === 'provider.reasoningEffort');
    if (effort && field?.choices?.length) {
      effort.replaceChildren(
        Object.assign(document.createElement('option'), { value: '', textContent: 'Mặc định' }),
        ...field.choices.map(choice => Object.assign(document.createElement('option'), { value: choice, textContent: choice }))
      );
    }
  } catch {}
}
