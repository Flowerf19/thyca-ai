(() => {
  const buttons = [...document.querySelectorAll("[data-view]")];
  const cost = document.querySelector("#chi-phi");
  const usage = document.querySelector("#su-dung");
  const surface = document.querySelector(".dashboard-surface");

  const show = (view) => {
    const isUsage = view === "usage";
    cost.hidden = isUsage;
    usage.hidden = !isUsage;
    buttons.forEach((button) => {
      const on = button.dataset.view === view;
      button.classList.toggle("is-active", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const hash = isUsage ? "#su-dung" : "#chi-phi";
    if (location.hash !== hash) history.replaceState(null, "", hash);
    surface.scrollTop = 0;
    requestAnimationFrame(() => { surface.scrollTop = 0; });
  };

  buttons.forEach((button) => {
    button.addEventListener("click", () => show(button.dataset.view));
  });
  window.addEventListener("hashchange", () => {
    show(location.hash === "#su-dung" ? "usage" : "cost");
  });

  show(location.hash === "#su-dung" ? "usage" : "cost");
})();
