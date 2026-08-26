import { scoreFromEvents } from "./staff-map.js";

const NS = "http://www.w3.org/2000/svg";
const H = 48;
const GAP = 6;
const TOP = 13;
const BOTTOM = TOP + GAP * 4;
const PAD_LEFT = 52; // clef + 4/4 + gap before beat 1
const PAD_RIGHT = 10;
const MEASURE_W = 128;
const CLEF_X = 16;
const TIME_X = 34;
const STAFF_GAP = 4;
const STEM = GAP * 3.5;
const EM = GAP * 4; // SMuFL em = 4 staff spaces
const SMUFL = {
  gClef: 0xe050,
  timeSig4: 0xe084,
  noteheadWhole: 0xe0a2,
  noteheadHalf: 0xe0a3,
  noteheadBlack: 0xe0a4,
  restWhole: 0xe4e3,
  restHalf: 0xe4e4,
  restQuarter: 0xe4e5,
};

// Treble staff step grid: step 0 = bottom line (E4), step 8 = top line (F5).
const PITCH_STEPS = {
  C4: -2, D4: -1, E4: 0, F4: 1, G4: 2, A4: 3, B4: 4,
  C5: 5, D5: 6, E5: 7, F5: 8, G5: 9, A5: 10, B5: 11, C6: 12,
};
const MIDDLE_STEP = 4; // B4 — pivot for stem direction.

function normalizeScore(score) {
  if (!score || typeof score !== "object") return scoreFromEvents([]);
  if (Array.isArray(score)) return scoreFromEvents(score);
  if (!Array.isArray(score.measures)) return scoreFromEvents([]);
  return score;
}

function layout(widthPx) {
  const avail = Math.max(MEASURE_W, Math.max(240, widthPx) - PAD_LEFT - PAD_RIGHT);
  const perSystem = Math.max(1, Math.floor(avail / MEASURE_W));
  return { perSystem, measureW: avail / perSystem, width: PAD_LEFT + avail + PAD_RIGHT };
}

export function renderStaff(score, { widthPx = 560 } = {}) {
  const normalized = normalizeScore(score);
  const measures = normalized.measures;
  const total = measures.length;
  const { perSystem, measureW, width } = layout(widthPx);
  const systemCount = Math.max(1, Math.ceil(total / perSystem));
  const height = systemCount * H + Math.max(0, systemCount - 1) * STAFF_GAP;

  const svg = node("svg", {
    class: systemCount > 1 ? "thyca-staff is-tall" : "thyca-staff",
    viewBox: `0 0 ${width} ${height}`,
    width: String(width),
    height: String(height),
    fill: "none",
    "aria-hidden": "true",
    preserveAspectRatio: "xMinYMid meet",
  });

  for (let sys = 0; sys < systemCount; sys += 1) {
    const dy = sys * (H + STAFF_GAP);
    const from = sys * perSystem;
    const to = Math.min(from + perSystem, total);
    svg.append(staffSystem(measures, from, to, width, measureW, dy, sys === 0, perSystem));
  }
  return svg;
}

function staffSystem(measures, from, to, width, measureW, dy, showTime, slots) {
  const group = node("g", { class: "staff-system" });
  const endX = PAD_LEFT + slots * measureW;
  const lastHasFinal = !!measures[measures.length - 1]?.finalBarline;
  const lastSystem = from + slots >= measures.length;
  group.append(staffLines(width, endX, dy));
  group.append(clef(dy));
  if (showTime) group.append(timeSignature(dy));
  for (let slot = 0; slot < slots; slot += 1) {
    const i = from + slot;
    const xStart = PAD_LEFT + slot * measureW;
    const finish = lastSystem && slot === slots - 1 && lastHasFinal;
    const m = i < to ? measures[i] : null;
    if (m) group.append(measureContent(m, xStart, measureW, dy, finish));
    else if (finish) group.append(finalBarline(xStart + measureW, dy, true));
    else group.append(singleBarline(xStart + measureW, dy));
  }
  return group;
}

function staffLines(width, endX, dy) {
  const group = node("g", { class: "staff-lines" });
  const x2 = Math.min(width - 4, endX);
  for (let line = 0; line < 5; line += 1) {
    const y = TOP + line * GAP + dy;
    group.append(node("line", { class: "staff-line", x1: "8", x2: String(x2), y1: String(y), y2: String(y) }));
  }
  return group;
}

function glyph(name, code, x, y, cls) {
  const el = node("text", {
    class: `staff-glyph ${cls}`,
    "data-glyph": name,
    x: String(x),
    y: String(y),
    "font-family": "Bravura",
    "font-size": String(EM),
    "text-anchor": "middle",
    "dominant-baseline": "middle",
  });
  el.textContent = String.fromCodePoint(code);
  return el;
}

function clef(dy) {
  // gClef origin sits on the G4 line.
  return glyph("gClef", SMUFL.gClef, CLEF_X, yOf(2) + dy, "staff-clef");
}

function timeSignature(dy) {
  const group = node("g", { class: "staff-time" });
  const x = TIME_X;
  group.append(glyph("timeSig4", SMUFL.timeSig4, x, yOf(6) + dy, "staff-time-glyph"));
  group.append(glyph("timeSig4", SMUFL.timeSig4, x, yOf(2) + dy, "staff-time-glyph"));
  return group;
}

function measureContent(measure, xStart, measureW, dy, isLastOverall) {
  const group = node("g", { class: "staff-measure" });
  const ticks = 16;
  for (const rest of measure.rests || []) {
    group.append(restGlyph(rest, xStart, measureW, ticks, dy));
  }
  for (const event of measure.events || []) {
    group.append(eventGlyph(event, xStart, measureW, ticks, dy));
  }
  if (isLastOverall) {
    group.append(finalBarline(xStart + measureW, dy, !!measure.finalBarline));
  } else {
    group.append(singleBarline(xStart + measureW, dy));
  }
  return group;
}

function eventX(offset, xStart, measureW, ticks) {
  return xStart + (offset / ticks) * measureW;
}

function eventGlyph(event, xStart, measureW, ticks, dy) {
  const x = eventX(event.offset, xStart, measureW, ticks) + measureW * 0.06;
  const pitches = (event.pitches || []).map((p) => PITCH_STEPS[p]).filter((n) => Number.isFinite(n));
  pitches.sort((a, b) => a - b);
  const duration = event.duration;
  const group = node("g", { class: "staff-event" });
  if (!pitches.length) {
    // Treat chordless event as a rest for layout safety.
    group.append(restPath(duration, x, dy));
    return group;
  }
  const isWhole = duration === 16;
  const lowest = pitches[0];
  const highest = pitches[pitches.length - 1];
  const farthest = Math.max(Math.abs(lowest - MIDDLE_STEP), Math.abs(highest - MIDDLE_STEP));
  const mid = (lowest + highest) / 2;
  let stemUp;
  if (pitches.length === 1) {
    stemUp = pitches[0] < MIDDLE_STEP;
  } else {
    stemUp = farthest > Math.abs(mid - MIDDLE_STEP) ? mid < MIDDLE_STEP : false;
  }
  for (const step of pitches) {
    for (const ledger of ledgerLines(step, x, dy)) group.append(ledger);
    group.append(notehead(x, yOf(step) + dy, duration));
  }
  if (!isWhole) group.append(stem(x, pitches, stemUp, dy));
  return group;
}

function restGlyph(rest, xStart, measureW, ticks, dy) {
  const x = eventX(rest.offset, xStart, measureW, ticks) + measureW * 0.06;
  const group = node("g", { class: "staff-event" });
  group.append(restPath(rest.duration, x, dy));
  return group;
}

function restPath(duration, x, dy) {
  if (duration === 16) {
    return glyph("restWhole", SMUFL.restWhole, x, yOf(6) + dy, "staff-rest is-whole");
  }
  if (duration === 8) {
    return glyph("restHalf", SMUFL.restHalf, x, yOf(4) + dy, "staff-rest is-half");
  }
  return glyph("restQuarter", SMUFL.restQuarter, x, yOf(4) + dy, "staff-rest is-quarter");
}

function singleBarline(x, dy) {
  return node("line", {
    class: "staff-bar",
    x1: String(x),
    x2: String(x),
    y1: String(TOP + dy),
    y2: String(BOTTOM + dy),
  });
}

function finalBarline(x, dy, isFinal) {
  if (!isFinal) return singleBarline(x, dy);
  // Final double barline = thin rule + thick rule, with an is-final marker.
  const group = node("g", { class: "staff-bar-group" });
  group.append(node("line", {
    class: "staff-bar",
    x1: String(x),
    x2: String(x),
    y1: String(TOP + dy),
    y2: String(BOTTOM + dy),
  }));
  group.append(node("line", {
    class: "staff-bar is-final",
    x1: String(x + 3),
    x2: String(x + 3),
    y1: String(TOP + dy),
    y2: String(BOTTOM + dy),
  }));
  return group;
}

function notehead(x, y, duration) {
  if (duration === 16) {
    return glyph("noteheadWhole", SMUFL.noteheadWhole, x, y, "staff-head is-open");
  }
  if (duration === 8) {
    return glyph("noteheadHalf", SMUFL.noteheadHalf, x, y, "staff-head is-open");
  }
  return glyph("noteheadBlack", SMUFL.noteheadBlack, x, y, "staff-head");
}

function stem(x, steps, up, dy) {
  const low = yOf(steps[0]) + dy;
  const high = yOf(steps[steps.length - 1]) + dy;
  const x1 = up ? x + GAP * 0.58 : x - GAP * 0.58;
  const y1 = up ? low : high;
  const y2 = up ? high - STEM : low + STEM;
  return node("line", { class: "staff-stem", x1: String(x1), x2: String(x1), y1: String(y1), y2: String(y2) });
}

function yOf(step) {
  return BOTTOM - step * (GAP / 2);
}

function ledgerLines(step, x, dy) {
  const lines = [];
  if (step < 0) {
    for (let line = -2; line >= step; line -= 2) {
      const y = yOf(line) + dy;
      lines.push(node("line", { class: "staff-ledger", x1: String(x - GAP * 0.93), x2: String(x + GAP * 0.93), y1: String(y), y2: String(y) }));
    }
  }
  if (step > 8) {
    for (let line = 10; line <= step; line += 2) {
      const y = yOf(line) + dy;
      lines.push(node("line", { class: "staff-ledger", x1: String(x - GAP * 0.93), x2: String(x + GAP * 0.93), y1: String(y), y2: String(y) }));
    }
  }
  return lines;
}

function node(name, attrs) {
  const el = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null) continue;
    el.setAttribute(key, String(value));
  }
  return el;
}
