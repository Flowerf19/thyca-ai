/* Shared status helpers for dashboard view panels (usage, request). Lives
   here — not in api.js — so W2 never touches the file W6 is splitting. */

export function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

/* Binds a panel's status node to the `setStatus(message, kind)` shape every
   view already calls; a missing node is a no-op. `baseClass` is the full
   static class list (default `screen-status`): memories prefixes it
   (`screen-note memory-status screen-status`), profile suffixes it
   (`screen-status profile-status`), so one additive position cannot
   reproduce both — the whole base is a parameter instead. */
export function makeSetStatus(node, baseClass = "screen-status") {
  return (message = "", kind = "") => {
    if (!node) return;
    node.textContent = message;
    node.className = `${baseClass}${kind ? ` is-${kind}` : ""}`;
  };
}
