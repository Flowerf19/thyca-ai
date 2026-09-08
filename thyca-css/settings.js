(() => {
  const STORAGE_KEY = "thyca.ui.preferences";
  const root = document.documentElement;
  const controls = {
    theme: document.getElementById("theme"),
    typeScale: document.getElementById("type-scale"),
    language: document.getElementById("language"),
    reduceMotion: document.getElementById("reduce-motion"),
    showSprig: document.getElementById("show-sprig"),
  };
  const defaults = {
    theme: "paper",
    typeScale: "medium",
    language: "vi",
    reduceMotion: false,
    showSprig: true,
  };

  const read = () => {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      return parsed && typeof parsed === "object" ? { ...defaults, ...parsed } : defaults;
    } catch {
      return defaults;
    }
  };

  const values = () => ({
    theme: controls.theme.value,
    typeScale: controls.typeScale.value,
    language: controls.language.value,
    reduceMotion: controls.reduceMotion.checked,
    showSprig: controls.showSprig.checked,
  });

  const apply = (next = values(), { save = true } = {}) => {
    root.dataset.theme = next.theme;
    root.dataset.typeScale = next.typeScale;
    root.dataset.reduceMotion = next.reduceMotion ? "on" : "off";
    root.dataset.sprigs = next.showSprig ? "on" : "off";
    root.lang = next.language;
    if (save) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        // The visual controls still apply when storage is unavailable.
      }
    }
  };

  const hydrate = (next) => {
    controls.theme.value = next.theme;
    controls.typeScale.value = next.typeScale;
    controls.language.value = next.language;
    controls.reduceMotion.checked = Boolean(next.reduceMotion);
    controls.showSprig.checked = next.showSprig !== false;
    apply(next, { save: false });
  };

  Object.values(controls).forEach((control) => {
    control.addEventListener("change", () => apply());
  });
  document.querySelector(".settings-reset")?.addEventListener("click", () => {
    hydrate(defaults);
    apply(defaults);
  });

  hydrate(read());
})();
