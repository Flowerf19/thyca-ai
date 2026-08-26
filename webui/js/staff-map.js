// Pure mapper: operational events (same objects as NDJSON) -> normalized score model.
// No timer, no text hashing, no key choice: single voice C major, 4/4.
//
// Invariants (per returned measure):
//   - ticks: quarter=4, half=8, whole=16; measure=16
//   - 0 <= offset < 16; duration in {4,8,16}; offset+duration <= 16
//   - events+rests do not overlap; their [offset, offset+duration) union is [0,16)
//   - no duration crosses beat 3 (tick 8) except a whole rest in an empty measure
//   - no dotted values, no 8th/16th, no ties

export const TICKS = { quarter: 4, half: 8, whole: 16, measure: 16 };

// Activity voicings [low, middle, high]; vii° is the local error color only.
const VOICINGS = {
  I: ["C5", "E5", "G5"],
  vi: ["C5", "E5", "A5"],
  IV: ["C5", "F5", "A5"],
  V: ["B4", "D5", "G5"],
  "vii°": ["B4", "D5", "F5"],
};
const HARMONY_ORDER = ["I", "vi", "IV", "V"];
const BEATS = [0, 4, 8, 12];

// Pitches for the terminal measures: dominant (G4 B4 D5) and tonic (C5 E5 G5).
const V_TRIAD = ["G4", "B4", "D5"];
const I_TRIAD = ["C5", "E5", "G5"];

const TERMINALS = new Set(["turn.completed", "turn.failed"]);
const ACTIVITY_TYPES = new Set([
  "turn.accepted",
  "llm.started",
  "llm.finished",
  "tool.started",
  "tool.finished",
  "session.naming.started",
  "session.naming.finished",
]);

function createMeasure(harmony) {
  return {
    harmony,
    terminal: null,
    events: [],
    rests: [],
    finalBarline: false,
  };
}

// Generated trailing rests for a measure with `used` activity slots.
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

// Keep operational rests (naming.started) and only fill the unused tail.
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

export function scoreFromEvents(events) {
  const score = { key: "C", meter: { beats: 4, beatType: 4, ticksPerQuarter: 4 }, measures: [] };
  const input = Array.isArray(events) ? events : [];
  let measure = null;
  let measureIndex = 0;
  let usedSlots = 0;
  let previousActivityPitches = null;

  // Sonority of one activity slot (all quarters). Returns {pitches} or {rest: true}.
  function activityFor(event) {
    if (event.type === "session.naming.started") return { rest: true };
    const low = VOICINGS[measure.harmony][0];
    const high = VOICINGS[measure.harmony][2];
    const full = VOICINGS[measure.harmony];
    const vii = VOICINGS["vii°"];
    switch (event.type) {
      case "turn.accepted":
      case "llm.started":
        return { pitches: [low] };
      case "llm.finished":
        return { pitches: [low, high] };
      case "tool.started":
        return { pitches: [high] };
      case "tool.finished":
        if (event.ok !== true) {
          if (!samePitches(previousActivityPitches, vii)) return { pitches: [...vii] };
          return { pitches: full };
        }
        return { pitches: full };
      case "session.naming.finished":
        return { pitches: [low] };
      default:
        return null;
    }
  }

  for (const raw of input) {
    if (!raw || typeof raw !== "object" || typeof raw.type !== "string") continue;
    if (TERMINALS.has(raw.type)) break;
    if (!ACTIVITY_TYPES.has(raw.type)) continue;
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
    const slot = activityFor(raw);
    if (!slot) continue;
    const beat = BEATS[usedSlots];
    if (slot.rest) {
      measure.rests.push({ offset: beat, duration: 4 });
      usedSlots += 1;
      previousActivityPitches = null;
      continue;
    }
    measure.events.push({ offset: beat, duration: 4, pitches: slot.pitches });
    usedSlots += 1;
    previousActivityPitches = slot.pitches;
  }

  if (!measure) measure = createMeasure(HARMONY_ORDER[0]);

  const terminalType = input.find((raw) => raw && typeof raw === "object" && TERMINALS.has(raw.type) && typeof raw.type === "string")?.type;
  if (terminalType) {
    if (usedSlots > 0) {
      closeMeasure(measure, usedSlots);
      score.measures.push(measure);
    } // else: skip an empty whole-rest measure opened solely to close.
    if (terminalType === "turn.completed") {
      score.measures.push({
        harmony: null,
        terminal: "completed",
        events: [
          { offset: 0, duration: 8, pitches: V_TRIAD },
          { offset: 8, duration: 8, pitches: I_TRIAD },
        ],
        rests: [],
        finalBarline: true,
      });
    } else {
      score.measures.push({
        harmony: null,
        terminal: "failed",
        events: [{ offset: 0, duration: 16, pitches: V_TRIAD }],
        rests: [],
        finalBarline: false,
      });
    }
  } else {
    closeMeasure(measure, usedSlots);
    score.measures.push(measure); // in-flight score always shows the current measure
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
