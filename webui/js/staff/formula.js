// One Formula class; many instances. Add a chart = registerFormula(new Formula({...})).
// Mapper stays event→density; it does not own key/bars/voicings.

export class Formula {
  constructor({
    id,
    key,
    mode,
    bars,
    voicings,
    bass,
    bpm = 65,
    seventh = "V7",
    tonic = "I",
    error = "vii°",
  } = {}) {
    if (!id || !key || !mode) throw new Error("Formula needs id, key, mode");
    if (!Array.isArray(bars) || !bars.length) throw new Error("Formula needs bars");
    if (!voicings || typeof voicings !== "object") throw new Error("Formula needs voicings");
    this.id = String(id);
    this.key = String(key);
    this.mode = String(mode);
    this.bars = Object.freeze([...bars]);
    this.voicings = Object.freeze({ ...voicings });
    this.bass = Object.freeze({ ...(bass || {}) });
    this.bpm = Number.isFinite(bpm) && bpm > 0 ? bpm : 65;
    this.seventh = String(seventh);
    this.tonic = String(tonic);
    this.error = String(error);
    Object.freeze(this);
  }

  degreeAt(index) {
    const n = this.bars.length;
    const i = Number.isInteger(index) ? index : 0;
    return this.bars[((i % n) + n) % n];
  }

  staffPitches(degree) {
    const chord = this.voicings[degree];
    return Array.isArray(chord) ? [...chord] : [];
  }

  bassNote(degree) {
    const note = this.bass[degree];
    return typeof note === "string" && note ? note : null;
  }

  isSeventh(degree) {
    return degree === this.seventh;
  }

  errorPitches() {
    return this.staffPitches(this.error);
  }

  seventhPitches() {
    return this.staffPitches(this.seventh);
  }

  tonicPitches() {
    return this.staffPitches(this.tonic);
  }
}

const REGISTRY = new Map();

export function registerFormula(formula) {
  if (!(formula instanceof Formula)) throw new Error("registerFormula expects Formula");
  REGISTRY.set(formula.id, formula);
  return formula;
}

export function getFormula(id) {
  if (id && REGISTRY.has(id)) return REGISTRY.get(id);
  return REGISTRY.get("am-8");
}

export function defaultFormula() {
  return getFormula("am-8");
}

export function listFormulas() {
  return [...REGISTRY.values()];
}

export function pickFormula(rng = Math.random) {
  const all = listFormulas();
  if (!all.length) return defaultFormula();
  const i = Math.min(all.length - 1, Math.max(0, Math.floor(Number(rng()) * all.length)));
  return all[i];
}

export function pickBpm(formula, rng = Math.random) {
  const bpm = formula && formula.bpm;
  if (Array.isArray(bpm) && bpm.length >= 2) {
    const lo = Math.ceil(Math.min(Number(bpm[0]), Number(bpm[1])));
    const hi = Math.floor(Math.max(Number(bpm[0]), Number(bpm[1])));
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return 65;
    const span = Math.max(0, hi - lo);
    return lo + Math.floor(Number(rng()) * (span + 1));
  }
  const n = Number(bpm);
  return Number.isFinite(n) && n > 0 ? n : 65;
}

registerFormula(new Formula({
  id: "am-8",
  key: "a",
  mode: "minor",
  bpm: [58, 72],
  tonic: "i",
  seventh: "VII7",
  error: "vii°",
  bars: ["i", "VI", "III", "VII", "iv", "VII7", "i", "i"],
  voicings: {
    i: ["C5", "E5", "A5"],
    VI: ["C5", "F5", "A5"],
    III: ["C5", "E5", "G5"],
    VII: ["B4", "D5", "G5"],
    iv: ["D5", "F5", "A5"],
    VII7: ["G4", "B4", "F5"],
    "vii°": ["B4", "D5", "F5"],
  },
  bass: { i: "A3", VI: "F3", III: "C4", VII: "G3", VII7: "G3", iv: "D3" },
}));

registerFormula(new Formula({
  id: "c-doo-wop",
  key: "C",
  mode: "major",
  bpm: [62, 76],
  tonic: "I",
  seventh: "V7",
  error: "vii°",
  bars: ["I", "vi", "IV", "V", "ii", "V7", "I", "I"],
  voicings: {
    I: ["C5", "E5", "G5"],
    vi: ["C5", "E5", "A5"],
    IV: ["C5", "F5", "A5"],
    V: ["B4", "D5", "G5"],
    ii: ["D5", "F5", "A5"],
    V7: ["G4", "B4", "F5"],
    "vii°": ["B4", "D5", "F5"],
  },
  bass: { I: "C4", vi: "A3", IV: "F3", V: "G3", V7: "G3", ii: "D3" },
}));
