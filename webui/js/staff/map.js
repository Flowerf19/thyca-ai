// Pure mapper: operational events (same objects as NDJSON) -> normalized score model.
// No timer, no text hashing, no key choice: single voice A minor, 4/4.
// Event types are NOT known here — role lookup lives in staff/catalog.js
// (familyFor: pulse | rest | terminal). Unregistered events are silence.
//
// TO ADD A NEW TRACE: append 4 YAML lines to the catalog (staff/catalog.js),
// a status line in staff/status.js, and a TurnEvent allowlist entry in
// thyca/agent/events.py — plus a test proving it does not double up with
// tool.*. Never add an "unknown event = note" fallback; silence is the
// safe default.
//
// Invariants (per returned measure):
//   - ticks: quarter=4, half=8, whole=16; measure=16
//   - 0 <= offset < 16; duration in {4,8,16}; offset+duration <= 16
//   - events+rests do not overlap; their [offset, offset+duration) union is [0,16)
//   - no duration crosses beat 3 (tick 8) except a whole rest in an empty measure
//   - no dotted values, no 8th/16th, no ties

import { familyFor } from "./catalog.js";

export const TICKS = { quarter: 4, half: 8, whole: 16, measure: 16 };
// Activity voicings [low, middle, high]; vii° is the local error color only.
// VII7 is root+3rd+7th (omit 5th) so it is never the same three pitches as vii°.
const VOICINGS = {
  i: ["C5", "E5", "A5"],
  VI: ["C5", "F5", "A5"],
  III: ["C5", "E5", "G5"],
  VII: ["B4", "D5", "G5"],
  iv: ["D5", "F5", "A5"],
  VII7: ["G4", "B4", "F5"],
  "vii°": ["B4", "D5", "F5"],
};
const HARMONY_ORDER = ["i", "VI", "III", "VII", "iv", "VII7", "i", "i"];
const BEATS = [0, 4, 8, 12];
const BASS = { i: "A3", VI: "F3", III: "C4", VII: "G3", VII7: "G3", iv: "D3" };
const VII7_STAFF = VOICINGS.VII7;
const I_STAFF = VOICINGS.i;

function createMeasure(harmony) {
  return {
    harmony,
    terminal: null,
    events: [],
    rests: [],
    finalBarline: false,
  };
}

function trailingRests(used) {
  switch (used) {
    case 0:
      return [{ offset: 0, duration: 16 }];
    case 1:
      return [
        { offset: 4, duration: 4 },
        { offset: 8, duration: 8 },
      ];
    case 2:
      return [{ offset: 8, duration: 8 }];
    case 3:
      return [{ offset: 12, duration: 4 }];
    default:
      return [];
  }
}

function closeMeasure(measure, used) {
  measure.rests = measure.rests.concat(trailingRests(used));
}

function samePitches(left, right) {
  return (
    Array.isArray(left) &&
    Array.isArray(right) &&
    left.length === right.length &&
    left.every((pitch, index) => pitch === right[index])
  );
}

function staffEvent(offset, duration, pitches, harmony, isVii) {
  const item = { offset, duration, pitches };
  if (isVii) return item;
  const sound = [];
  const bass = BASS[harmony];
  if (bass) sound.push(bass);
  for (const pitch of pitches) {
    if (!sound.includes(pitch)) sound.push(pitch);
  }
  // Thin VII7 densities (anchor/cue) omit the 7th on the staff; still hear F5. Never runs for VII.
  if (harmony === "VII7" && !sound.includes("F5")) sound.push("F5");
  item.sound = sound;
  return item;
}

export function scoreFromEvents(events, familyLookup = familyFor) {
  const score = { key: "a", meter: { beats: 4, beatType: 4, ticksPerQuarter: 4 }, measures: [] };
  const input = Array.isArray(events) ? events : [];
  let measure = null;
  let measureIndex = 0;
  let usedSlots = 0;
  let previousActivityPitches = null;
  const vii = VOICINGS["vii°"];

  function activityFor(event, family) {
    if (family.slot === "rest") return { rest: true };
    const chord = VOICINGS[measure.harmony];
    const low = chord[0];
    const high = chord[chord.length - 1];
    const full = chord;
    if (family.errorWhen?.(event)) {
      if (!samePitches(previousActivityPitches, vii)) return { pitches: [...vii] };
      return { pitches: [...full] };
    }
    switch (family.density) {
      case "anchor":
        return { pitches: [low] };
      case "outer":
        return { pitches: [low, high] };
      case "cue":
        return { pitches: [high] };
      case "full":
        return { pitches: [...full] };
      default:
        return null;
    }
  }

  for (const raw of input) {
    if (!raw || typeof raw !== "object" || typeof raw.type !== "string") continue;
    const family = familyLookup(raw);
    if (!family) continue;
    if (family.slot === "terminal") break;
    if (!measure) {
      measure = createMeasure(HARMONY_ORDER[measureIndex % HARMONY_ORDER.length]);
      usedSlots = 0;
    }
    if (usedSlots >= 4) {
      closeMeasure(measure, usedSlots);
      score.measures.push(measure);
      measureIndex += 1;
      measure = createMeasure(HARMONY_ORDER[measureIndex % HARMONY_ORDER.length]);
      usedSlots = 0;
    }
    const slot = activityFor(raw, family);
    if (!slot) continue;
    const beat = BEATS[usedSlots];
    if (slot.rest) {
      measure.rests.push({ offset: beat, duration: 4 });
      usedSlots += 1;
      previousActivityPitches = null;
      continue;
    }
    const isVii = samePitches(slot.pitches, vii);
    measure.events.push(staffEvent(beat, 4, slot.pitches, measure.harmony, isVii));
    usedSlots += 1;
    previousActivityPitches = slot.pitches;
  }

  if (!measure) measure = createMeasure(HARMONY_ORDER[0]);

  const terminalFamily = input.map(familyLookup).find((f) => f?.slot === "terminal");
  const terminalKind = terminalFamily ? terminalFamily.kind : null;
  if (terminalKind) {
    if (usedSlots > 0) {
      closeMeasure(measure, usedSlots);
      score.measures.push(measure);
    }
    if (terminalKind === "completed") {
      score.measures.push({
        harmony: null,
        terminal: "completed",
        events: [
          staffEvent(0, 8, [...VII7_STAFF], "VII7", false),
          staffEvent(8, 8, [...I_STAFF], "i", false),
        ],
        rests: [],
        finalBarline: true,
      });
    } else {
      score.measures.push({
        harmony: null,
        terminal: "failed",
        events: [staffEvent(0, 16, [...VII7_STAFF], "VII7", false)],
        rests: [],
        finalBarline: false,
      });
    }
  } else {
    closeMeasure(measure, usedSlots);
    score.measures.push(measure);
  }

  if (score.measures.length > 16) {
    const last = score.measures.at(-1);
    score.measures = score.measures.slice(-16);
    if (last.terminal) {
      score.measures[score.measures.length - 1] = last;
    }
  }

  return score;
}
