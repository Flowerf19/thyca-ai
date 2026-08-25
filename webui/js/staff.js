import { renderStaff } from "./staff-draw.js";

const EVENTS = new WeakMap();
const watched = new WeakSet();
let observer = null;

export function mountStaff(article, events, opts = {}) {
  if (!article || !article.classList.contains("entry-thyca")) return;
  EVENTS.set(article, {
    events: events || [],
    freshFrom: opts.freshFrom ?? -1,
    reduceMotion: opts.reduceMotion,
  });
  paint(article);
}

export function syncStaffs(root) {
  if (!root) return;
  for (const article of root.querySelectorAll(".entry-thyca")) {
    if (!EVENTS.has(article)) EVENTS.set(article, { events: [], freshFrom: -1 });
    paint(article);
  }
}

function paint(article) {
  const host = ensureHost(article);
  watch(host);
  const rec = EVENTS.get(article) || { events: [], freshFrom: -1 };
  const widthPx = Math.round(host.getBoundingClientRect().width) || 480;
  const reduceMotion =
    rec.reduceMotion ?? window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sig = `${rec.events.length}:${rec.freshFrom}:${widthPx}:${rec.events.at(-1)?.kind || ""}`;
  if (host.dataset.sig === sig) return;
  host.dataset.sig = sig;
  host.replaceChildren(
    renderStaff(rec.events, { freshFrom: rec.freshFrom ?? -1, reduceMotion, widthPx }),
  );
}

function watch(host) {
  if (typeof ResizeObserver !== "function") return;
  if (!observer) {
    observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const article = entry.target.parentNode;
        if (article?.classList?.contains("entry-thyca")) paint(article);
      }
    });
  }
  if (watched.has(host)) return;
  observer.observe(host);
  watched.add(host);
}

function ensureHost(article) {
  let host = article.querySelector(":scope > .thyca-staff-host");
  if (host) return host;
  host = document.createElement("div");
  host.className = "thyca-staff-host";
  host.setAttribute("aria-hidden", "true");
  const copy = article.querySelector(":scope > .entry-copy");
  if (copy) article.insertBefore(host, copy);
  else article.append(host);
  return host;
}
