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

export function effortDefault(schema) {
  return effortField(schema)?.default;
}

// Fill a <select> with one option per choice and pick `selected`,
// falling back to the schema default, then the last choice.
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
  const fallback = effortDefault(schema) || levels[levels.length - 1];
  select.value = levels.includes(selected) ? selected : fallback;
}
