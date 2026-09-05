// Play a staff score (whole passage) via Web Audio.
// Piano samples when decodeAudioData works; triangle fallback otherwise.
// ticksPerQuarter = 4, matching staff/map.js. No DOM.

const FREQ = {
  D3: 146.83, F3: 174.61, G3: 196.0, A3: 220.0,
  C4: 261.63, D4: 293.66, E4: 329.63, F4: 349.23, G4: 392.0, A4: 440.0, B4: 493.88,
  C5: 523.25, D5: 587.33, E5: 659.25, F5: 698.46, G5: 783.99, A5: 880.0, B5: 987.77, C6: 1046.5,
};
const BPM = 65;
const TICKS_PER_QUARTER = 4;
const MEASURE_TICKS = 16;
const SAMPLE_BASE = new URL("../../audio/piano/", import.meta.url);

let shared = null;
let active = [];
let buffers = null;
let loadPromise = null;
let playGen = 0;

function getContext() {
  const Ctor = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!Ctor) return null;
  if (!shared) shared = new Ctor();
  if (shared.state === "suspended" && typeof shared.resume === "function") {
    shared.resume();
  }
  return shared;
}

function tickSec(bpm = BPM) {
  const n = Number(bpm);
  const use = Number.isFinite(n) && n > 0 ? n : BPM;
  return (60 / use) / TICKS_PER_QUARTER;
}

export function stopPlayback() {
  for (const node of active) {
    try { node.stop(); } catch { /* already stopped */ }
  }
  active = [];
}

export function loadPiano(ac) {
  if (buffers) return Promise.resolve(buffers);
  if (loadPromise) return loadPromise;
  if (!ac || typeof ac.decodeAudioData !== "function" || typeof fetch !== "function") {
    return Promise.resolve(null);
  }
  loadPromise = Promise.all(
    Object.keys(FREQ).map(async (name) => {
      const res = await fetch(new URL(`${name}.mp3`, SAMPLE_BASE));
      if (!res.ok) return [name, null];
      const raw = await res.arrayBuffer();
      const buf = await ac.decodeAudioData(raw.slice(0));
      return [name, buf];
    }),
  ).then((pairs) => {
    buffers = new Map(pairs.filter(([, buf]) => buf));
    if (!buffers.size) buffers = null;
    return buffers;
  }).catch(() => {
    buffers = null;
    loadPromise = null;
    return null;
  });
  return loadPromise;
}

function voice(ac, name, hz, when, sec, peak) {
  const gain = ac.createGain();
  const buf = buffers && buffers.get(name);
  gain.gain.setValueAtTime(0, when);
  gain.gain.linearRampToValueAtTime(peak, when + 0.012);
  gain.gain.setValueAtTime(peak, when + Math.max(0.05, sec - 0.05));
  gain.gain.linearRampToValueAtTime(0.001, when + sec);
  gain.connect(ac.destination);
  if (buf && typeof ac.createBufferSource === "function") {
    const src = ac.createBufferSource();
    src.buffer = buf;
    src.connect(gain);
    src.start(when);
    src.stop(when + sec + 0.02);
    active.push(src);
    return;
  }
  const osc = ac.createOscillator();
  osc.type = "triangle";
  osc.frequency.value = hz;
  osc.connect(gain);
  osc.start(when);
  osc.stop(when + sec + 0.02);
  active.push(osc);
}

export function playPitches(pitches, durationTicks, { context, atTicks = 0, bpm } = {}) {
  const names = (Array.isArray(pitches) ? pitches : []).filter((name) => FREQ[name]);
  if (!names.length) return false;
  const ac = context || getContext();
  if (!ac) return false;
  const ticks = Number.isFinite(durationTicks) && durationTicks > 0 ? durationTicks : TICKS_PER_QUARTER;
  const step = tickSec(bpm);
  const sec = Math.max(0.12, ticks * step);
  const when = (ac.currentTime || 0) + Math.max(0, atTicks) * step;
  const peak = (buffers ? 0.28 : 0.1) / Math.sqrt(names.length);
  for (const name of names) voice(ac, name, FREQ[name], when, sec, peak);
  return true;
}

export async function playScore(score, { context } = {}) {
  const ac = context || getContext();
  if (!ac) return { ok: false, timeline: [], startedAt: 0 };
  const mine = ++playGen;
  await loadPiano(ac);
  if (mine !== playGen) return { ok: false, timeline: [], startedAt: 0 };
  stopPlayback();
  const measures = score?.measures;
  if (!Array.isArray(measures) || !measures.length) return { ok: false, timeline: [], startedAt: 0 };
  const bpm = score.bpm;
  const step = tickSec(bpm);
  const startedAt = ac.currentTime || 0;
  const timeline = [];
  let any = false;
  for (let i = 0; i < measures.length; i += 1) {
    for (const event of measures[i].events || []) {
      const atTicks = i * MEASURE_TICKS + (Number(event.offset) || 0);
      const names = event.sound || event.pitches;
      if (playPitches(names, event.duration, { context: ac, atTicks, bpm })) any = true;
      if (Array.isArray(names) && names.length) {
        timeline.push({
          measure: i,
          offset: Number(event.offset) || 0,
          atSec: atTicks * step,
          durSec: Math.max(0.12, (Number(event.duration) || 4) * step),
        });
      }
    }
  }
  return { ok: any, timeline, startedAt };
}
