import { state } from "../state.js";
import { hydrateSettings } from "./schema.js";
import { bindProviderForm } from "./provider.js";
import { bindAddModel, bindModelCards } from "./models.js";

export { hydrateSettings } from "./schema.js";

export function initSettings() {
  return { open: () => renderModeSettings(), isOpen: () => state.activeMode === "settings" };
}

// Open the settings page (used by the boot gate when provider isn't ready).
export async function renderModeSettings() {
  const { renderMode } = await import("../render.js");
  await renderMode("settings");
}

export function bindSettings(root) {
  bindProviderForm(root);
  bindAddModel(root);
  bindModelCards(root);
}
