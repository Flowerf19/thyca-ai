import { el } from "./dom.js";

const MOBILE = "(max-width: 59.99rem)";

export function isMobile() {
  return window.matchMedia(MOBILE).matches;
}

export function openDrawer() {
  el.sidebar.classList.add("is-open");
  el.scrim.hidden = false;
  requestAnimationFrame(() => el.scrim.classList.add("is-visible"));
  el.openSidebar.setAttribute("aria-expanded", "true");
  el.sidebar.removeAttribute("aria-hidden");
  el.sidebar.removeAttribute("inert");
}

export function closeDrawer() {
  const restoreFocus = el.sidebar.contains(document.activeElement);
  el.sidebar.classList.remove("is-open");
  el.scrim.classList.remove("is-visible");
  el.openSidebar.setAttribute("aria-expanded", "false");
  if (isMobile()) {
    el.sidebar.setAttribute("aria-hidden", "true");
    el.sidebar.setAttribute("inert", "");
  }
  window.setTimeout(() => {
    if (!el.sidebar.classList.contains("is-open")) {
      el.scrim.hidden = true;
      if (restoreFocus) el.openSidebar.focus();
    }
  }, 220);
}

export function toggleDrawer() {
  if (el.sidebar.classList.contains("is-open")) closeDrawer();
  else openDrawer();
}

function applyBreakpointState() {
  if (isMobile()) {
    if (!el.sidebar.classList.contains("is-open")) {
      el.sidebar.setAttribute("aria-hidden", "true");
      el.sidebar.setAttribute("inert", "");
    }
  } else {
    el.sidebar.classList.remove("is-open");
    el.sidebar.removeAttribute("aria-hidden");
    el.sidebar.removeAttribute("inert");
    el.openSidebar.setAttribute("aria-expanded", "false");
    el.scrim.classList.remove("is-visible");
    el.scrim.hidden = true;
  }
}

export function hideDrawerIfMobile() {
  applyBreakpointState();
}

window.matchMedia(MOBILE).addEventListener("change", applyBreakpointState);
