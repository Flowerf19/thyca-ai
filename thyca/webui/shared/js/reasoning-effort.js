// Shared reasoning-effort helpers for the composer and provider screens.
// The option list for a model comes from its config thinking map
// (models[<name>].reasoningEfforts); unknown models fall back to the
// schema choices served by /api/config (REASONING_EFFORTS).

const FALLBACK_CHOICES = ["low", "high", "max"];

export function effortField(schema) {
  return (schema?.sections || [])
    .find((section) => section.key === "provider")?.fields
    ?.find((field) => field.key === "provider.reasoningEffort");
}

export function effortChoices(schema) {
  const field = effortField(schema);
  return field?.choices?.length ? field.choices : FALLBACK_CHOICES;
}

// Thinking map for a model: user-declared levels win, then schema choices.
export function effortChoicesFor(schema, values, modelName) {
  const declared = values?.models?.[modelName]?.reasoningEfforts;
  if (Array.isArray(declared) && declared.length) return declared;
  return effortChoices(schema);
}

// Default effort for a model: its own setting wins, then its provider's,
// then the default provider's.
export function effortDefaultFor(values, modelName) {
  const spec = values?.models?.[modelName];
  if (spec?.reasoningEffort) return spec.reasoningEffort;
  const providers = values?.providers;
  const pid = spec?.provider || values?.defaultProvider;
  const entry = providers && typeof providers === "object" ? providers[pid] : null;
  if (entry?.reasoningEffort) return entry.reasoningEffort;
  return providers?.[values?.defaultProvider]?.reasoningEffort;
}

export function effortDefault(schema) {
  return effortField(schema)?.default;
}

// Fill a <select> with one option per choice and pick `selected`.
// Config first: a configured level that no longer matches the available
// choices lands on the first available choice — never a silent default.
// The schema default only applies when nothing was configured at all.
export function fillEffortSelect(select, choices, selected, schema) {
  const levels = choices?.length ? choices : effortChoices(schema);
  select.replaceChildren(
    ...levels.map((choice) => {
      const option = document.createElement("option");
      option.value = choice;
      option.textContent = choice;
      return option;
    }),
  );
  if (levels.includes(selected)) {
    select.value = selected;
    return;
  }
  const configured = selected !== undefined && selected !== null && selected !== "";
  select.value = configured ? levels[0] : (effortDefault(schema) || levels[levels.length - 1]);
}
