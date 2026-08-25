export const STEP = {
  E4: 0,
  F4: 1,
  G4: 2,
  A4: 3,
  B4: 4,
  C5: 5,
  D5: 6,
  E5: 7,
  F5: 8,
  Fs5: 8,
  G5: 9,
  A5: 10,
};

export const SHARP = new Set(["Fs5"]);

export const KEYS = {
  C: {
    order: ["I", "vi", "IV", "V"],
    chords: {
      I: ["C5", "E5", "G5"],
      vi: ["A4", "C5", "E5"],
      IV: ["F4", "A4", "C5"],
      V: ["G4", "B4", "D5"],
    },
  },
  Am: {
    order: ["i", "VI", "III", "VII"],
    chords: {
      i: ["A4", "C5", "E5"],
      VI: ["F4", "A4", "C5"],
      III: ["C5", "E5", "G5"],
      VII: ["G4", "B4", "D5"],
    },
  },
  G: {
    order: ["I", "vi", "IV", "V"],
    chords: {
      I: ["G4", "B4", "D5"],
      vi: ["E4", "G4", "B4"],
      IV: ["C5", "E5", "G5"],
      V: ["D5", "Fs5", "A5"],
    },
  },
};

export const THINK_PHASES = [
  ["Đang lắng nghe nhịp…", "Nghe nhịp trong đầu…", "Lắng nghe khoảng lặng…"],
  ["Đang tìm tứ thơ…", "Đang đợi cảm hứng…", "Đang tìm hình ảnh…"],
  ["Đang tìm vần…", "Đang chọn từ…", "Đang cân nhắc chữ…"],
  ["Đang sắp xếp nhịp…", "Đang buộc câu thơ…", "Đang chỉnh nhịp điệu…"],
  ["Đang thả chữ xuống trang…", "Đang viết khổ thơ…", "Đang làm thơ…", "Đang để thơ tự đến…"],
];

export const THINK_BREATH = ["Hmm…", "Đang suy nghĩ…", "Tiếp tục suy nghĩ…", "Đang để cảm xúc lắng…"];

export const CLOSE_LINE = "Sắp xong rồi…";

export function keyForMode(mode) {
  if (mode === "memories") return "G";
  if (mode === "poetry") return "Am";
  return "C";
}

export function classifyThink(line) {
  const text = String(line || "");
  if (text.includes("Sắp xong")) return "tonic";
  if (/Hmm|khoảng lặng/.test(text)) return "rest";
  if (/lắng nghe|Nghe nhịp/.test(text)) return "root";
  if (/tứ thơ|cảm hứng|hình ảnh/.test(text)) return "walk";
  if (/vần|chọn từ|cân nhắc/.test(text)) return "dyad";
  if (/sắp xếp nhịp|buộc câu|chỉnh nhịp/.test(text)) return "triad";
  if (/thả chữ|viết khổ|làm thơ|thơ tự đến|viết tiếp/.test(text)) return "whole";
  if (/suy nghĩ|cảm xúc lắng/.test(text)) return "root";
  return "root";
}

export function thinkingEvent(line, index, keyName = "C") {
  const key = KEYS[keyName] || KEYS.C;
  const names = key.order;
  const chord = names[index % names.length];
  const tones = key.chords[chord];
  const steps = tones.map((pitch) => STEP[pitch]);
  const sharps = tones.filter((pitch) => SHARP.has(pitch)).map((pitch) => STEP[pitch]);
  const kind = classifyThink(line);
  if (kind === "rest") return event("rest", "q", [], chord, []);
  if (kind === "root") return event("note", "q", [steps[0]], chord, sharpsFor(sharps, [steps[0]]));
  if (kind === "walk") {
    const step = steps[index % 3];
    return event("note", "q", [step], chord, sharpsFor(sharps, [step]));
  }
  if (kind === "dyad") {
    const pair = [steps[0], steps[2]];
    return event("dyad", "q", pair, chord, sharpsFor(sharps, pair));
  }
  if (kind === "triad") return event("triad", "q", steps, chord, sharps);
  if (kind === "whole") return event("triad", "w", steps, chord, sharps);
  const tonic = key.chords[names[0]];
  return event(
    "triad",
    "w",
    tonic.map((pitch) => STEP[pitch]),
    names[0],
    tonic.filter((pitch) => SHARP.has(pitch)).map((pitch) => STEP[pitch]),
  );
}

export function createThinkCycle(rng = Math.random) {
  let step = 0;
  let lastBreath = false;
  let recent = [];
  function pick(pool) {
    const open = pool.filter((item) => !recent.includes(item));
    const src = open.length ? open : pool;
    return src[Math.floor(rng() * src.length)];
  }
  return {
    nextLine() {
      let line;
      let breath = false;
      if (!lastBreath && step > 0 && step % 3 === 2) {
        line = pick(THINK_BREATH);
        breath = true;
      } else {
        const phase = Math.min(Math.floor(step / 2), THINK_PHASES.length - 1);
        line = pick(THINK_PHASES[phase]);
      }
      lastBreath = breath;
      recent = [...recent, line].slice(-3);
      step += 1;
      return line;
    },
  };
}

function event(kind, duration, steps, chord, sharps) {
  return { kind, duration, steps, chord, sharps };
}

function sharpsFor(sharps, steps) {
  return sharps.filter((step) => steps.includes(step));
}
