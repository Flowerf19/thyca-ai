(() => {
try {
  const prefs = JSON.parse(localStorage.getItem("thyca.ui.preferences") || "null");
  if (prefs && typeof prefs === "object") {
    const root = document.documentElement;
    if (prefs.theme) root.dataset.theme = prefs.theme;
    if (prefs.typeScale) root.dataset.typeScale = prefs.typeScale;
    root.dataset.reduceMotion = prefs.reduceMotion ? "on" : "off";
    root.dataset.sprigs = prefs.showSprig === false ? "off" : "on";
    if (prefs.language) root.lang = prefs.language;
  }
} catch {
  // Local preferences are optional; navigation must still open.
}

const routes = [
  ["index.html", "Trò chuyện", "M4 5h16v11H9l-5 4V5Z"],
  ["memories.html", "Nhật ký", "M12 6C9 4 6 4 3 5v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"],
  ["trace.html", "Trace", "M5 4v16M5 6h14M5 12h10M5 18h14"],
  ["dashboard.html", "Tổng quan", "M4 20V10h4v9M10 20V5h4v14M16 19v-7h4v7"],
  ["provider.html", "Provider", "M5 15.5a5.5 5.5 0 0 1 1.8-10.7A6.5 6.5 0 0 1 19 8.5a4.5 4.5 0 0 1-.5 9H6"],
  ["settings.html", "Giao diện", "M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Zm3 5a4 4 0 1 0 0 8 4 4 0 0 0 0-8"],
];

const menuButtons = document.querySelectorAll(".menu-button");
if (menuButtons.length) {
  const dialog = document.createElement("dialog");
  dialog.id = "screen-navigation";
  dialog.className = "screen-dialog";
  dialog.setAttribute("aria-labelledby", "screen-navigation-title");
  dialog.innerHTML = `
    <div class="screen-heading">
      <h2 id="screen-navigation-title">Mục lục</h2>
      <button class="icon-button" type="button" aria-label="Đóng mục lục" autofocus>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>
      </button>
    </div>
    <nav class="screen-nav" aria-label="Các màn Thyca"></nav>
    <p class="screen-note">Thyca local · dữ liệu từ backend</p>`;
  const nav = dialog.querySelector("nav");
  const currentPage = location.pathname.split("/").pop() || "index.html";
  for (const [path, label, icon] of routes) {
    const link = document.createElement("a");
    link.href = `./${path}`;
    link.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icon}"/></svg><span></span>`;
    link.querySelector("span").textContent = label;
    if (path === currentPage) link.setAttribute("aria-current", "page");
    nav.append(link);
  }
  document.body.append(dialog);

  let opener;
  for (const button of menuButtons) {
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-controls", dialog.id);
    button.addEventListener("click", () => {
      opener = button;
      dialog.showModal();
    });
  }
  dialog.querySelector("button").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
      dialog.close();
    }
  });
  dialog.addEventListener("close", () => opener?.focus());
}
})();
