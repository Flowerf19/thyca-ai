// Shared journal pager (cost + trace views). Mechanical copy of the identical
// blocks in pages/dashboard/cost.js and pages/dashboard/trace.js; originals
// kept in place (W3 team removes the duplicates on its branch).
// >>> journal-pager (pure math; extracted by unit tests, no DOM here)
export const JOURNAL_PAGE_SIZE = 12;
export function journalPageCount(totalItems, perPage = JOURNAL_PAGE_SIZE) {
  return Math.max(1, Math.ceil(totalItems / perPage));
}
export function journalClampPage(page, pages) {
  return Math.min(Math.max(page, 1), pages);
}
// <<< journal-pager

export function buildPager(onStep) {
  const nav = document.createElement("nav");
  nav.className = "journal-pager";
  nav.hidden = true;
  const prev = document.createElement("button");
  prev.type = "button";
  prev.className = "screen-button journal-pager-step";
  prev.textContent = "‹ Trước";
  prev.setAttribute("aria-label", "Trang trước");
  const label = document.createElement("span");
  label.className = "journal-pager-label";
  label.setAttribute("aria-live", "polite");
  const next = document.createElement("button");
  next.type = "button";
  next.className = "screen-button journal-pager-step";
  next.textContent = "Sau ›";
  next.setAttribute("aria-label", "Trang sau");
  prev.addEventListener("click", () => onStep(-1));
  next.addEventListener("click", () => onStep(1));
  nav.append(prev, label, next);
  return { nav, prev, next, label };
}

export function syncPager(pager, page, pages) {
  pager.nav.hidden = pages <= 1;
  pager.prev.disabled = page <= 1;
  pager.next.disabled = page >= pages;
  pager.label.textContent = `${page} / ${pages}`;
}
