/* View switcher for the unified Dashboard: a single overview screen
   (Chung / bot / group, rendered by overview.js) plus the Trace detail
   screen. A ?session=/?turn= deep link or #trace opens Trace (trace.html
   redirects here with the same params, so old links keep working); every
   other URL — including the legacy #su-dung/#request/#chi-phi hashes —
   falls back to the overview. */
(() => {
  const blocks = {
    overview: document.querySelector("#overview"),
    trace: document.querySelector("#trace"),
  };
  const DEFAULT_VIEW = "overview";

  // Mobile pick pattern (same as Hồ sơ/Cài đặt): the entity list shows
  // first; choosing reveals the overview, ‹ Dashboard returns to the list.
  // A deep link or #trace opens Trace directly instead of the list.
  const shell = document.querySelector(".dashboard-shell");
  // matchMedia is absent in the Node test harness — fall back to a desktop
  // stub so module evaluation never throws there.
  const compact = typeof matchMedia === "function"
    ? matchMedia("(max-width: 56rem)")
    : { matches: false, addEventListener: () => {} };
  const params = new URLSearchParams(location.search);
  let chosen = params.has("session") || params.has("turn") || location.hash === "#trace";
  const syncPick = () => shell?.classList.toggle("is-picking", compact.matches && !chosen);

  const isTraceRequested = () =>
    new URLSearchParams(location.search).has("session")
    || new URLSearchParams(location.search).has("turn")
    || location.hash === "#trace";

  // Actual switching on every viewport: the overview (with its entity
  // sidebar) and the Trace detail are separate destinations on mobile too.
  const show = (view) => {
    const next = blocks[view] ? view : DEFAULT_VIEW;
    for (const [key, node] of Object.entries(blocks)) {
      if (node) node.hidden = key !== next;
    }
    // Leaving Trace drops its ?session=/?turn= deep-link params, and any
    // other view shows the bare overview URL: the address must match what
    // is on screen, and replaceState keeps Back on the caller page.
    if (next !== "trace") {
      const leaving = new URLSearchParams(location.search);
      let dirty = false;
      if (leaving.has("session") || leaving.has("turn")) {
        leaving.delete("session");
        leaving.delete("turn");
        dirty = true;
      }
      if (location.hash) dirty = true;
      if (dirty) {
        const query = leaving.toString();
        history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}`);
      }
    } else if (location.hash !== "#trace") {
      history.replaceState(null, "", "#trace");
    }
    const surface = document.querySelector(".dashboard-surface");
    if (surface) {
      surface.scrollTop = 0;
      requestAnimationFrame(() => { surface.scrollTop = 0; });
    }
  };

  // The Trace detail back button returns to the overview.
  document.querySelectorAll(".dashboard-shell .screen-back, #trace-back").forEach((button) => {
    button.addEventListener("click", () => {
      chosen = false;
      syncPick();
      show(DEFAULT_VIEW);
    });
  });
  window.addEventListener("hashchange", () => {
    chosen = true;
    syncPick();
    show(isTraceRequested() ? "trace" : DEFAULT_VIEW);
  });
  compact.addEventListener("change", syncPick);

  // A ?session=/?turn= deep link or #trace always opens Trace, never the
  // overview.
  show(isTraceRequested() ? "trace" : DEFAULT_VIEW);
  syncPick();
})();
