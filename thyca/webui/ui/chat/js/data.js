// Chat data layer — shared backend calls live in ../share/data.js.
// API: GET /api/bots, GET /api/sessions, GET /api/config,
//      GET /api/config/status, GET /api/bots/<id>/settings,
//      GET /api/bots/<id>/dm, GET /api/sessions/<id>,
//      GET /api/bots/<id>/rooms/<session>/operations.
'use strict';
const histories = new Map();
const runs = new Map();          // sessionId -> in-flight turn token (backend allows one per session)
let currentSession = '', attachment = null;

/* Composer overrides: an empty value means "inherit the responding bot's
   configured settings". The client never injects a concrete model here —
   when the payload omits model/effort the backend resolves the recipient
   card's settings (mentions included). */
let composerModels = {};         // id -> registered model entry (reasoningEfforts)
let composerDefaultModel = '';
let composerEffortChoices = [];
/* Effective connection the composer currently inherits from (GET settings):
   drives the inherit labels AND the effort set shown while the model select
   stays on inheritance. Reset with the overrides so stale labels/options
   can never leak onto another bot. */
let composerEffModel = '';
let composerEffProvider = '';
let composerEffThinking = '';
let composerInheritSeq = 0;      // guards stale settings fetches
let composerConfigSeq = 0;       // guards stale config catalog reads
let composerNavSeq = 0;          // view generation shared by selectBot/loadSession
let composerScopeKey = '';       // last bot+session the composer reset for

function nextChatNav() { return ++composerNavSeq; }
function chatNavCurrent() { return composerNavSeq; }

function composerModelInheritLabel() {
  const detail = [composerEffModel, composerEffProvider].filter(Boolean).join(' · ');
  return detail ? `Theo cấu hình bot (${detail})` : 'Theo cấu hình bot';
}

function composerEffortInheritLabel() {
  return composerEffThinking ? `Theo cấu hình bot (${composerEffThinking})` : 'Theo cấu hình bot';
}

/* Model-aware effort options: the explicitly chosen model's own set wins;
   while inheriting, the inherited effective model's own set governs, else
   the global provider set. The inheritance entry stays first; the current
   choice survives when still allowed. */
function rebuildEffortOptions(keep) {
  const effort = $('#thinking-effort');
  if (!effort) return;
  const explicit = $('#composer-model')?.value || '';
  const base = explicit || composerEffModel;
  const registered = composerModels[base];
  const own = registered && Array.isArray(registered.reasoningEfforts)
    ? registered.reasoningEfforts.filter(Boolean) : [];
  const choices = own.length ? own : composerEffortChoices;
  effort.replaceChildren(
    Object.assign(document.createElement('option'), { value: '', textContent: composerEffortInheritLabel() }),
    ...choices.map(choice => Object.assign(document.createElement('option'), { value: choice, textContent: choice }))
  );
  effort.value = choices.includes(keep) ? keep : '';
}

function rebuildComposerOptions() {
  const select = $('#composer-model');
  if (select) {
    const keep = select.value;
    const ids = [...new Set([composerDefaultModel, ...Object.keys(composerModels)].filter(Boolean))];
    select.replaceChildren(
      Object.assign(document.createElement('option'), { value: '', textContent: composerModelInheritLabel() }),
      ...ids.map(id => Object.assign(document.createElement('option'), { value: id, textContent: id }))
    );
    select.value = ids.includes(keep) ? keep : '';
    rebuildEffortOptions($('#thinking-effort')?.value);
  } else rebuildEffortOptions($('#thinking-effort')?.value);
}

function bindComposerModel() {
  const select = $('#composer-model');
  if (select && !select.dataset.bound) {
    select.dataset.bound = '1';
    select.addEventListener('change', () => rebuildEffortOptions($('#thinking-effort')?.value));
  }
}

/* Switching bots resets per-turn choices to inheritance — a stale explicit
   model must never leak into another bot's turn. The reset also invalidates
   old in-flight inherit labels and clears the stored effective connection. */
function resetComposerOverrides() {
  composerInheritSeq++;                                  // stale fetches drop
  composerEffModel = composerEffProvider = composerEffThinking = '';
  const select = $('#composer-model'), effort = $('#thinking-effort');
  if (select) select.value = '';
  if (effort) { rebuildEffortOptions(''); }
  paintComposerInherit();
}

function paintComposerInherit() {
  const select = $('#composer-model'), effort = $('#thinking-effort');
  if (select?.options?.[0]) select.options[0].textContent = composerModelInheritLabel();
  if (effort?.options?.[0]) effort.options[0].textContent = composerEffortInheritLabel();
}

/* Show the selected bot's configured connection on the inheritance entries
   and offer its effective model's effort set while inheriting. Paints only
   for the newest fetch of the still-selected bot; resets and navigation
   invalidate older answers. */
async function refreshComposerInherit(botId, nav) {
  const seq = ++composerInheritSeq;
  let data = null;
  try {
    data = await getJson(`/api/bots/${encodeURIComponent(botId)}/settings`);   // API: GET /api/bots/<id>/settings
  } catch { data = null; }
  if (seq !== composerInheritSeq) return;
  if (typeof nav === 'number' && nav !== chatNavCurrent()) return;
  if (selectedBot !== botId) return;
  if (data && data.effective) {
    const eff = data.effective;
    composerEffModel = typeof eff.model === 'string' ? eff.model : '';
    composerEffProvider = typeof eff.provider === 'string' ? eff.provider : '';
    composerEffThinking = typeof eff.thinking === 'string' ? eff.thinking : '';
  } else {
    composerEffModel = composerEffProvider = composerEffThinking = '';
  }
  rebuildEffortOptions($('#thinking-effort')?.value);
  paintComposerInherit();
}

async function loadConfig() {
  const seq = ++composerConfigSeq;
  try {
    const { values, schema } = await getJson('/api/config');   // API: GET /api/config
    if (seq !== composerConfigSeq) return false;
    composerModels = values?.models && typeof values.models === 'object' ? values.models : {};
    composerDefaultModel = typeof values?.defaultModel === 'string' ? values.defaultModel : '';
    const field = (schema?.sections || []).flatMap(s => s.fields || []).find(f => f.key === 'provider.reasoningEffort');
    composerEffortChoices = Array.isArray(field?.choices) ? field.choices : [];
    // Rebuilding options never resets the composer, stream, or message draft.
    rebuildComposerOptions();
    bindComposerModel();
    return true;
  } catch { return false; }
}

window.addEventListener('thyca:config-saved', () => {
  const botId = selectedBot, nav = chatNavCurrent();
  void loadConfig().then(ok => ok && botId ? refreshComposerInherit(botId, nav) : null);
});
