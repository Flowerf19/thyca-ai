const NS = "http://www.w3.org/2000/svg";
const H = 60;
const GAP = 7;
const TOP = 16;
const BOTTOM = TOP + GAP * 4;
const PAD_LEFT = 32;
const PAD_RIGHT = 12;
const MAX_MEASURES = 8;
const STEM = GAP * 3.5;
const CLEF_SCALE = GAP / 24;
const CLEF_TX = 6 - 75 * CLEF_SCALE;
const CLEF_TY = TOP - 51.638 * CLEF_SCALE;
const CLEF_D =
  "M104.549 111.639c-2.589.845-4.7286 2.271-6.419 4.279-1.6906 2.007-2.7207 4.2-3.0905 6.577-.3698 2.377-.066 4.728.9113 7.052.9773 2.325 2.8131 4.2 5.5072 5.627.634 0 1.03.264 1.189.792.158.528-.08.792-.713.792-2.589-.528-4.8606-1.611-6.8152-3.248-3.6452-3.012-5.6526-6.894-6.0224-11.649-.2113-2.377.0132-4.675.6736-6.894.6603-2.219 1.5716-4.253 2.7338-6.102 1.4263-1.954 3.1168-3.645 5.0715-5.071.1056-.106.4094-.343.9112-.713.5019-.37.9905-.713 1.466-1.03.4755-.317 1.1885-.819 2.1395-1.506L99.1601 86.44c-2.5886 2.166-5.1507 4.556-7.6864 7.171-2.5358 2.615-4.8338 5.376-6.8941 8.281-2.0602 2.906-3.7111 5.983-4.9526 9.232-1.2414 3.249-1.8621 6.669-1.8621 10.262 0 3.328.6999 6.458 2.0999 9.39 1.3999 2.932 3.2621 5.481 5.5865 7.646 2.3244 2.166 5.0054 3.87 8.043 5.112 3.0376 1.241 6.1148 1.862 9.2317 1.862.106 0 .594-.053 1.466-.159.872-.105 1.796-.237 2.773-.396.978-.158 1.876-.33 2.695-.515.818-.185 1.228-.383 1.228-.594l-.476-2.219c-2.06-10.407-4.015-20.365-5.863-29.874zm2.773-.317 6.498 31.459c3.751-1.427 6.286-3.87 7.607-7.33 1.321-3.46 1.624-6.973.911-10.539-.713-3.566-2.39-6.723-5.032-9.47-2.641-2.747-5.969-4.12-9.984-4.12zm-8.3204-42.394c1.6374-.846 3.1564-2.113 4.5564-3.804 1.4-1.69 2.694-3.5 3.883-5.428 1.188-1.928 2.219-3.896 3.09-5.904.872-2.007 1.572-3.829 2.1-5.467.581-1.743.977-3.698 1.189-5.864.211-2.166-.132-3.988-1.03-5.468-.634-1.32-1.466-2.086-2.496-2.298-1.031-.211-2.061-.132-3.091.238-1.03.37-2.007.964-2.932 1.783-.924.819-1.598 1.545-2.02 2.179-1.163 2.06-2.18 4.358-3.0513 6.894-.8717 2.536-1.466 5.164-1.7829 7.885-.317 2.72-.3566 5.335-.1189 7.845.2377 2.509.8056 4.979 1.7037 7.409zm-2.3772 2.456c-.8981-3.486-1.6905-6.907-2.3773-10.262-.6868-3.354-1.0301-6.801-1.0301-10.341 0-2.588.1849-5.428.5547-8.518.3697-3.09 1.0433-6.102 2.0206-9.034.9773-2.932 2.3244-5.56 4.0413-7.884 1.7174-2.325 4.0014-4.015 6.8544-5.072.264-.105.528-.158.792-.158.37 0 .806.211 1.308.634.502.422 1.03 1.043 1.585 1.862.554.819 1.043 1.664 1.466 2.536.422.871.739 1.466.951 1.783 1.426 2.694 2.469 5.56 3.13 8.597.66 3.038 1.043 6.062 1.149 9.073.211 4.544-.04 9.034-.753 13.472-.713 4.437-2.153 8.769-4.319 12.995-.739 1.268-1.492 2.549-2.258 3.843-.766 1.295-1.677 2.549-2.734 3.764-.211.212-.594.595-1.149 1.149l-1.704 1.704c-.581.581-1.096 1.123-1.545 1.624-.449.502-.673.806-.673.912l3.248 15.848c.021.104 1.625 0 1.625 0 3.1.039 6.382.541 9.232 1.664 2.747 1.268 5.111 3.011 7.092 5.23 1.981 2.219 3.565 4.715 4.754 7.488 1.189 2.774 1.783 5.587 1.783 8.44 0 2.852-.423 5.758-1.268 8.716-2.166 5.6-5.626 9.747-10.38 12.441-.529.317-1.282.674-2.259 1.07-.977.396-1.36 1.017-1.149 1.862 1.268 5.756 2.126 9.716 2.576 11.886.449 2.17.779 4.97.99 8.4.211 3.28-.357 6.23-1.704 8.87-1.347 2.65-3.156 4.8-5.428 6.46-2.271 1.67-4.345 2.64-6.22 2.94-1.876.29-3.157.43-3.843.43-2.3776 0-4.702-.45-6.9736-1.35-2.7999-1.05-5.1507-2.66-7.0525-4.83-1.9018-2.17-2.8527-4.81-2.8527-7.92 0-1.96.5679-3.97 1.7037-6.03 1.1358-2.06 2.6282-3.54 4.4771-4.43 2.0603-1.06 3.9225-1.35 5.5866-.88 1.664.48 3.0375 1.38 4.1205 2.7 1.0829 1.32 1.8359 2.92 2.2589 4.79.422 1.88.396 3.63-.08 5.27-.475 1.64-1.439 3.03-2.892 4.16-1.4528 1.14-3.4735 1.65-6.062 1.55 1.0565 1.9 2.5357 3.1 4.4375 3.6 1.9018.51 3.8565.54 5.8635.12 2.008-.42 3.896-1.2 5.666-2.34 1.77-1.13 3.157-2.36 4.16-3.68.634-.95 1.11-2.19 1.427-3.72.317-1.54.502-3.13.554-4.8.053-1.66 0-2.96-.158-3.88-.159-.93-.423-2.37-.793-4.32-1.584-6.39-2.588-10.41-3.011-12.05-.211-.523-.779-.695-1.703-.51-.925.185-1.704.357-2.338.51-4.543.59-8.3468.32-11.4108-.787-4.7545-1.268-8.9411-3.527-12.5598-6.776-3.6187-3.248-6.5242-7.184-8.7166-11.807-2.1923-4.622-3.2885-8.135-3.2885-10.539v-4.556c0-4.279.7396-8.294 2.2188-12.045 2.7998-5.864 6.1148-11.252 9.9448-16.165 3.83-4.913 8.2015-9.483 13.1145-13.709z";

export function renderStaff(events, { freshFrom = -1, reduceMotion = false, widthPx = 560 } = {}) {
  const width = Math.max(240, widthPx);
  let beats = 0;
  for (const event of events || []) beats += event.duration === "w" ? 4 : 1;
  const measures = Math.min(MAX_MEASURES, Math.max(4, Math.ceil(beats / 4) || 4));
  const beatW = (width - PAD_LEFT - PAD_RIGHT) / (measures * 4);
  const placed = placeEvents(events || [], beatW);
  const svg = node("svg", {
    class: "thyca-staff",
    viewBox: `0 0 ${width} ${H}`,
    width: "100%",
    height: String(H),
    fill: "none",
    "aria-hidden": "true",
    preserveAspectRatio: "xMinYMid meet",
  });
  svg.append(staffLines(width, measures, beatW * 4));
  svg.append(clef());
  placed.forEach((item, index) => {
    svg.append(drawEvent(item, !reduceMotion && index >= freshFrom && freshFrom >= 0));
  });
  return svg;
}

function placeEvents(events, beatW) {
  const measureW = beatW * 4;
  const items = [];
  let beat = 0;
  let measure = 0;
  for (const event of events) {
    const span = event.duration === "w" ? 4 : 1;
    if (beat + span > 4 && beat > 0) {
      beat = 0;
      measure += 1;
    }
    if (measure >= MAX_MEASURES) break;
    items.push({ ...event, x: PAD_LEFT + measure * measureW + beat * beatW + beatW * 0.45 });
    beat += span;
    if (beat >= 4) {
      beat = 0;
      measure += 1;
    }
  }
  return items;
}

function yOf(step) {
  return BOTTOM - step * (GAP / 2);
}

function staffLines(width, measures, measureW) {
  const group = node("g", { class: "staff-lines" });
  const end = Math.min(width - 4, PAD_LEFT + measures * measureW);
  for (let line = 0; line < 5; line += 1) {
    const y = TOP + line * GAP;
    group.append(node("line", { class: "staff-line", x1: "8", x2: String(end), y1: String(y), y2: String(y) }));
  }
  for (let bar = 0; bar <= measures; bar += 1) {
    const x = PAD_LEFT + bar * measureW;
    group.append(node("line", { class: "staff-bar", x1: String(x), x2: String(x), y1: String(TOP), y2: String(BOTTOM) }));
  }
  return group;
}

function clef() {
  return node("path", {
    class: "staff-clef",
    d: CLEF_D,
    transform: `translate(${CLEF_TX} ${CLEF_TY}) scale(${CLEF_SCALE})`,
  });
}

function drawEvent(item, fresh) {
  const group = node("g", { class: fresh ? "staff-event is-fresh" : "staff-event" });
  if (item.kind === "rest") {
    group.append(rest(item.x));
    return group;
  }
  const steps = [...item.steps].sort((a, b) => a - b);
  const whole = item.duration === "w";
  const mid = (steps[0] + steps[steps.length - 1]) / 2;
  const stemUp = mid < 4;
  for (const step of steps) {
    for (const ledger of ledgers(item.x, step)) group.append(ledger);
    if ((item.sharps || []).includes(step)) group.append(sharp(item.x, yOf(step)));
    group.append(head(item.x, yOf(step), whole));
  }
  if (!whole) group.append(stem(item.x, steps, stemUp));
  return group;
}

function head(x, y, whole) {
  return node("ellipse", {
    class: whole ? "staff-head is-whole" : "staff-head",
    cx: String(x),
    cy: String(y),
    rx: "4.4",
    ry: "3.1",
    transform: `rotate(-22 ${x} ${y})`,
  });
}

function stem(x, steps, up) {
  const low = yOf(steps[0]);
  const high = yOf(steps[steps.length - 1]);
  const x1 = up ? x + 4.1 : x - 4.1;
  const y1 = up ? low : high;
  const y2 = up ? high - STEM : low + STEM;
  return node("line", { class: "staff-stem", x1: String(x1), x2: String(x1), y1: String(y1), y2: String(y2) });
}

function rest(x) {
  return node("path", {
    class: "staff-rest",
    d: "M3 2.2c2.6 1.4 2.8 2.8.4 4.2 2.6 1.2 2.4 3.4-.2 4.8",
    transform: `translate(${x - 3} ${TOP + GAP})`,
  });
}

function sharp(x, y) {
  return node("path", {
    class: "staff-accidental",
    d: "M2 1v11M7 0v11M0 4.2h9M0 7.8h9",
    transform: `translate(${x - 12} ${y - 6})`,
  });
}

function ledgers(x, step) {
  const lines = [];
  if (step < 0) {
    for (let line = -2; line >= step; line -= 2) {
      const y = yOf(line);
      lines.push(node("line", { class: "staff-ledger", x1: String(x - 6.5), x2: String(x + 6.5), y1: String(y), y2: String(y) }));
    }
  }
  if (step > 8) {
    for (let line = 10; line <= step; line += 2) {
      const y = yOf(line);
      lines.push(node("line", { class: "staff-ledger", x1: String(x - 6.5), x2: String(x + 6.5), y1: String(y), y2: String(y) }));
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
