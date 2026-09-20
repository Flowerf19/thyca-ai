/* View switcher for the four Dashboard views: Sử dụng token, Request,
   Chi phí and Trace. The old "Hôm nay" overview view is gone; the legacy
   #hom-nay hash (and any unknown hash) falls back to Sử dụng token, while a
   ?session=/?turn= deep link always opens Trace (trace.html redirects here
   with the same params, so old links keep working). */
(() => {
  const buttons = [...document.querySelectorAll("[data-view]")];
  const blocks = {
    usage: document.querySelector("#su-dung"),
    request: document.querySelector("#request"),
    cost: document.querySelector("#chi-phi"),
    trace: document.querySelector("#trace"),
  };
  const hashes = { usage: "#su-dung", request: "#request", cost: "#chi-phi", trace: "#trace" };
  const DEFAULT_VIEW = "usage";

  function viewFromHash() {
    const view = Object.keys(hashes).find((key) => location.hash === hashes[key]);
    return view || DEFAULT_VIEW;
  }

  // Actual switching on every viewport: Sử dụng token, Request, Chi phí and
  // Trace are separate destinations on mobile too (the sidebar stays
  // visible there — see dashboard.css).
  const show = (view) => {
    const next = blocks[view] ? view : DEFAULT_VIEW;
    for (const [key, node] of Object.entries(blocks)) {
      if (node) node.hidden = key !== next;
    }
    buttons.forEach((button) => {
      const on = button.dataset.view === next;
      button.classList.toggle("is-active", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const hash = hashes[next];
    // Leaving Trace drops its ?session=/?turn= deep-link params: the URL must
    // match what is on screen, and replaceState keeps Back on the caller
    // page. The combined write already lands the new hash.
    let queryStripped = false;
    if (next !== "trace") {
      const params = new URLSearchParams(location.search);
      if (params.has("session") || params.has("turn")) {
        params.delete("session");
        params.delete("turn");
        queryStripped = true;
        const query = params.toString();
        history.replaceState(
          null,
          "",
          `${location.pathname}${query ? `?${query}` : ""}${hash}`,
        );
      }
    }
    if (hash && location.hash !== hash && !queryStripped) history.replaceState(null, "", hash);
    const surface = document.querySelector(".dashboard-surface");
    if (surface) {
      surface.scrollTop = 0;
      requestAnimationFrame(() => { surface.scrollTop = 0; });
    }
  };

  buttons.forEach((button) => {
    button.addEventListener("click", () => show(button.dataset.view));
  });
  window.addEventListener("hashchange", () => show(viewFromHash()));

  // A ?session=/?turn= deep link always opens the Trace view, never the
  // default one.
  const params = new URLSearchParams(location.search);
  const deepTrace = params.has("session") || params.has("turn");
  show(deepTrace ? "trace" : viewFromHash());
})();
