import { formatCompact } from "./format.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const compact = matchMedia("(max-width: 56rem)");

function niceCeiling(max) {
  if (!Number.isFinite(max) || max <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(max));
  const scaled = max / power;
  const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return nice * power;
}

function dayLabel(day) {
  return String(day || "").slice(8, 10).replace(/^0/, "") || "—";
}

function svg(name, attributes = {}, text = "") {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text) node.textContent = text;
  return node;
}

/* Shared day bar chart. `rows` is [{ day, segments: [{ class, value }] }];
   segments stack bottom-up inside each day bar and `class` picks the fill
   from the calling page's stylesheet. `valueLabels` prints the day total
   above each bar. */
export function drawBarChart(node, rows, { ariaLabel = "", valueLabels = false } = {}) {
  if (!rows.length) {
    node.replaceChildren();
    return;
  }
  const box = node.getBoundingClientRect();
  const width = Math.max(Math.round(box.width) || 900, 320);
  const height = Math.max(Math.round(box.height) || 272, 160);
  node.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const left = 58;
  const right = 10;
  const top = 12;
  const bottom = 34;
  const ceiling = niceCeiling(Math.max(...rows.map((row) => row.segments.reduce((sum, seg) => sum + (Number(seg.value) || 0), 0)), 0));
  const x = (index) => left + index * (width - left - right) / rows.length;
  const y = (value) => height - bottom - value / ceiling * (height - bottom - top);
  const barWidth = Math.max(4, (width - left - right) / rows.length * 0.58);
  node.replaceChildren();

  for (let index = 0; index <= 5; index += 1) {
    const value = ceiling * index / 5;
    const yy = y(value);
    node.append(
      svg("line", { class: "usage-grid", x1: left, x2: width - right, y1: yy, y2: yy }),
      svg("text", { class: "usage-axis", x: 3, y: yy + 4 }, formatCompact(value)),
    );
  }

  rows.forEach((row, index) => {
    let base = height - bottom;
    row.segments.forEach((seg, segIndex) => {
      const segHeight = (Number(seg.value) || 0) / ceiling * (height - bottom - top);
      // First and last segments get the rounded corners; middle ones sit
      // flush against their neighbours.
      const rx = segIndex === 0 || segIndex === row.segments.length - 1 ? 3 : 0;
      node.append(svg("rect", { class: seg.class, x: x(index), y: base - segHeight, width: barWidth, height: segHeight, rx }));
      base -= segHeight;
    });
    const show = !compact.matches || index === 0 || index === rows.length - 1 || (index + 1) % 7 === 0;
    if (show) {
      node.append(svg("text", {
        class: "usage-axis",
        "text-anchor": "middle",
        x: x(index) + barWidth / 2,
        y: height - 10,
      }, dayLabel(row.day)));
    }
  });
  if (valueLabels) {
    rows.forEach((row, index) => {
      const total = row.segments.reduce((sum, seg) => sum + (Number(seg.value) || 0), 0);
      node.append(svg("text", {
        class: "usage-axis",
        "text-anchor": "middle",
        x: x(index) + barWidth / 2,
        y: y(total) - 5,
      }, formatCompact(total)));
    });
  }
  if (ariaLabel) node.setAttribute("aria-label", ariaLabel);
}
