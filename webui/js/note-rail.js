const RAIL = "note-rail";
const SVG = "http://www.w3.org/2000/svg";
export const KINDS = "bsbsbrbsbdbsbrbsbrbd";
const NOTE = {
  b: '<ellipse cx="4" cy="9" rx="3" ry="2.1" transform="rotate(-22 4 9)" /><path d="M6.8 8.6V1.8" />',
  s: '<ellipse cx="4" cy="9" rx="3" ry="2.1" transform="rotate(-22 4 9)" /><path d="M6.8 8.6V1.8q2.4 1 2.6 3.4" />',
  d: '<ellipse cx="4" cy="9" rx="3" ry="2.1" transform="rotate(-22 4 9)" /><path d="M6.8 8.6V1.6q2.4.9 2.6 3.1M6.8 4.4q2.4.9 2.6 3" />',
  r: '<path d="M3 2.2c2.6 1.4 2.8 2.8.4 4.2 2.6 1.2 2.4 3.4-.2 4.8" />',
};

let observer = null;
let observed = null;

export function syncNoteRail(list) {
  if (!list) {
    observer?.disconnect();
    observer = null;
    observed = null;
    return;
  }
  layout(list);
  if (observed !== list) {
    observer?.disconnect();
    observer = new ResizeObserver(() => {
      if (observed) layout(observed);
    });
    observer.observe(list);
    observed = list;
  }
}

function layout(list) {
  let rail = list.querySelector(`:scope > .${RAIL}`);
  if (!rail) {
    rail = document.createElement("div");
    rail.className = RAIL;
    rail.setAttribute("aria-hidden", "true");
    list.prepend(rail);
  }
  const height = list.clientHeight;
  const spans = freeSpans(list, height);
  renderRail(rail, height, spans, placeNotes(spans, height));
}

function freeSpans(list, height) {
  const box = list.getBoundingClientRect();
  const blocked = [];
  for (const node of list.querySelectorAll(".entry-thyca .entry-copy, .entry-thyca > time")) {
    const rect = node.getBoundingClientRect();
    const top = Math.max(0, rect.top - box.top - 4);
    const bottom = Math.min(height, rect.bottom - box.top + 4);
    if (bottom > top) blocked.push([top, bottom]);
  }
  return gapsFromBlocked(blocked, height);
}

export function gapsFromBlocked(blocked, height) {
  const merged = [];
  for (const span of [...blocked].sort((a, b) => a[0] - b[0])) {
    const last = merged.at(-1);
    if (!last || span[0] > last[1]) merged.push([span[0], span[1]]);
    else last[1] = Math.max(last[1], span[1]);
  }
  const open = [];
  let cursor = 0;
  for (const [top, bottom] of merged) {
    if (top > cursor) open.push([cursor, top]);
    cursor = Math.max(cursor, bottom);
  }
  if (cursor < height) open.push([cursor, height]);
  return open.filter(([top, bottom]) => bottom - top >= 20);
}

export function placeNotes(spans, height) {
  const pad = 16;
  const inner = spans
    .map(([top, bottom]) => [Math.max(top, pad), Math.min(bottom, height - pad)])
    .filter(([top, bottom]) => bottom - top >= 16);
  const usable = inner.reduce((sum, [top, bottom]) => sum + (bottom - top), 0);
  if (usable < 24) return [];
  const count = Math.min(18, Math.max(3, Math.round(usable / 32)));
  const spots = [];
  for (let index = 0; index < count; index += 1) {
    const target = ((index + 0.5) / count) * usable;
    const wobble = ((index * 7) % 5) + 4;
    let seen = 0;
    for (const [top, bottom] of inner) {
      const len = bottom - top;
      if (seen + len < target) {
        seen += len;
        continue;
      }
      const y = Math.min(bottom - 12, Math.max(top, top + (target - seen) + wobble));
      spots.push({ y, kind: KINDS[index % KINDS.length] });
      break;
    }
  }
  return spots;
}

function renderRail(rail, height, spans, notes) {
  const svg = ensureSvg(rail, height);
  const lines = svg.querySelector(".note-lines");
  const marks = svg.querySelector(".note-marks");
  while (lines.firstChild) lines.firstChild.remove();
  for (const [top, bottom] of spans) {
    const line = document.createElementNS(SVG, "line");
    line.setAttribute("class", "note-line");
    line.setAttribute("x1", "18");
    line.setAttribute("x2", "18");
    line.setAttribute("y1", top.toFixed(1));
    line.setAttribute("y2", bottom.toFixed(1));
    lines.append(line);
  }
  const keep = notes.length;
  while (marks.childElementCount > keep) marks.lastElementChild.remove();
  notes.forEach((note, index) => {
    let node = marks.children[index];
    if (!node) {
      node = document.createElementNS(SVG, "g");
      const inner = document.createElementNS(SVG, "g");
      inner.setAttribute("class", "note is-in");
      inner.innerHTML = NOTE[note.kind];
      node.append(inner);
      marks.append(node);
    }
    node.setAttribute("transform", `translate(10 ${note.y.toFixed(1)})`);
  });
}

function ensureSvg(rail, height) {
  let svg = rail.querySelector("svg");
  if (!svg) {
    svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("fill", "none");
    const lines = document.createElementNS(SVG, "g");
    lines.setAttribute("class", "note-lines");
    const marks = document.createElementNS(SVG, "g");
    marks.setAttribute("class", "note-marks");
    svg.append(lines, marks);
    rail.append(svg);
  }
  const size = String(Math.max(height, 1));
  svg.setAttribute("viewBox", `0 0 28 ${size}`);
  svg.setAttribute("width", "28");
  svg.setAttribute("height", size);
  return svg;
}
