(() => {
  const compact = matchMedia("(max-width: 56rem)");
  const buttons = [...document.querySelectorAll("[data-view]")];
  const blocks = {
    cost: document.querySelector("#chi-phi"),
    request: document.querySelector("#request"),
    usage: document.querySelector("#su-dung"),
  };
  const hashes = { cost: "#chi-phi", request: "#request", usage: "#su-dung" };

  function viewFromHash() {
    if (location.hash === "#su-dung") return "usage";
    if (location.hash === "#request") return "request";
    return "cost";
  }

  const show = (view) => {
    const next = blocks[view] ? view : "cost";
    const stack = compact.matches;
    for (const [key, node] of Object.entries(blocks)) {
      if (node) node.hidden = stack ? false : key !== next;
    }
    buttons.forEach((button) => {
      const on = button.dataset.view === next;
      button.classList.toggle("is-active", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const hash = hashes[next];
    if (hash && location.hash !== hash) history.replaceState(null, "", hash);
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
  compact.addEventListener("change", () => show(viewFromHash()));
  show(viewFromHash());
})();
