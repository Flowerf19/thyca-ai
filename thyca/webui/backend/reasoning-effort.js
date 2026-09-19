// Shared reasoning-effort helpers for the composer and provider screens.
// The option list comes from the config schema served by /api/config
// (provider.reasoningEffort.choices, sourced from REASONING_EFFORTS).

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

export function effortDefault(schema) {
  return effortField(schema)?.default;
}

// Fill a <select> with one option per choice and pick `selected`,
// falling back to the schema default, then the last choice.
export function fillEffortSelect(select, schema, selected) {
  const choices = effortChoices(schema);
  select.replaceChildren(
    ...choices.map((choice) => {
      const option = document.createElement("option");
      option.value = choice;
      option.textContent = choice;
      return option;
    }),
  );
  const fallback = effortDefault(schema) || choices[choices.length - 1];
  select.value = choices.includes(selected) ? selected : fallback;
}
