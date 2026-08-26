import { renderStaff } from "./staff-draw.js";
import { scoreFromEvents } from "./staff-map.js";

const RECORDS = new WeakMap();
const watched = new WeakSet();
let observer = null;

export function mountStaff(article, score, opts = {}) {
  if (!article || !article.classList.contains("entry-thyca")) return;
  const normalized = Array.isArray(score) || score == null
    ? scoreFromEvents(Array.isArray(score) ? score : [])
    : score;
  RECORDS.set(article, { score: normalized });
  paint(article);
}

export function unmountStaff(host) {
  if (!host) return;
  if (observer && watched.has(host)) {
    observer.unobserve(host);
    watched.delete(host);
  }
}

export function clearStaffs(root) {
  if (!root) return;
  for (const host of root.querySelectorAll(".thyca-staff-host")) {
    unmountStaff(host);
    host.remove();
  }
}

export function syncStaffs(root) {
  if (!root) return;
  for (const article of root.querySelectorAll(".entry-thyca")) {
    const rec = RECORDS.get(article);
    if (!article.classList.contains("entry-status") && !(rec && rec.score?.measures?.length)) {
      const stray = article.querySelector(":scope > .thyca-staff-host");
      if (stray) {
        unmountStaff(stray);
        stray.remove();
      }
      continue;
    }
    paint(article);
  }
}

function paint(article) {
  const host = ensureHost(article);
  watch(host);
  const rec = RECORDS.get(article) || { score: scoreFromEvents([]) };
  const widthPx = Math.round(host.getBoundingClientRect().width) || 480;
  const sig = `${scoreSig(rec.score)}:${widthPx}`;
  if (host.dataset.sig === sig) return;
  host.dataset.sig = sig;
  host.replaceChildren(renderStaff(rec.score, { widthPx }));
}

function scoreSig(score) {
  const measures = score?.measures || [];
  return measures
    .map((measure) => {
      const events = (measure.events || [])
        .map((item) => `${item.offset}/${item.duration}/${(item.pitches || []).join(".")}`)
        .join(",");
      const rests = (measure.rests || [])
        .map((item) => `${item.offset}/${item.duration}`)
        .join(",");
      return `${measure.harmony || ""}:${measure.terminal || ""}:${events}:${rests}`;
    })
    .join(";");
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
