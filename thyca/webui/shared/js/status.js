/* Shared status helpers for dashboard view panels (usage, request). Lives
   here — not in api.js — so W2 never touches the file W6 is splitting. */

export function messageOf(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback;
}

/* Binds a panel's `.screen-status` node to the `setStatus(message, kind)`
   shape both views already call; a missing node is a no-op. */
export function makeSetStatus(node) {
  return (message = "", kind = "") => {
    if (!node) return;
    node.textContent = message;
    node.className = `screen-status${kind ? ` is-${kind}` : ""}`;
  };
}
