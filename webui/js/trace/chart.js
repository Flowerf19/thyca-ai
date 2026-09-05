// Daily / hourly bar chart for the Trace overview — hand-built SVG, no library.
// Pure functions over API payloads; filter state stays in trace.js.

import { escapeHtml, fmtCost, fmtInt } from "../shared/util.js";

export function fmtDayShort(day) {
  const d = new Date(`${String(day)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return String(day);
  return d.toLocaleDateString("vi-VN", { day: "numeric", month: "short", timeZone: "UTC" });
}

// by_day only has days with turns — fill calendar gaps so a missing day reads as 0, not as a skipped category.
function fillDays(byDay) {
  const days = byDay.map((d) => String(d.day)).filter(Boolean).sort();
  if (!days.length) return [];
  const first = new Date(`${days[0]}T00:00:00Z`);
  const last = new Date(`${days[days.length - 1]}T00:00:00Z`);
  const span = Math.round((last.getTime() - first.getTime()) / 86400000);
  if (Number.isNaN(span) || span > 92) return byDay;
  const map = new Map(byDay.map((d) => [String(d.day), d]));
  const out = [];
  for (let t = first.getTime(); t <= last.getTime(); t += 86400000) {
    const key = new Date(t).toISOString().slice(0, 10);
    out.push(map.get(key) || { day: key, requests: 0, cost_usd: null });
  }
  return out;
}

function hourEntries(byHour) {
  const map = new Map((byHour || []).map((h) => [String(h.hour).padStart(2, "0"), h]));
  const out = [];
  for (let i = 0; i < 24; i += 1) {
    const key = String(i).padStart(2, "0");
    const row = map.get(key);
    out.push({ label: `${key}h`, requests: Number(row && row.requests) || 0, cost: row ? row.cost_usd : null });
  }
  return out;
}

export function byDayBlock(byDay, byHour, hourMode) {
  const entries = hourMode
    ? hourEntries(byHour)
    : fillDays(byDay)
        .filter((d) => d && d.day)
        .map((d) => ({ label: fmtDayShort(d.day), requests: Number(d.requests) || 0, cost: d.cost_usd }));
  if (!entries.length) return "";
  const W = 720;
  const H = 150;
  const PAD_B = 24;
  const TOP_PAD = 18;
  const max = Math.max(...entries.map((d) => Number(d.requests) || 0), 1);
  const n = entries.length;
  const slot = W / n;
  const barW = Math.max(2, Math.min(28, slot * 0.62));
  const baseline = H - PAD_B;
  const parts = [`<line class="trace-chart-rule" x1="0" y1="${baseline}" x2="${W}" y2="${baseline}" />`];
  const ticks = [];
  const tickEvery = Math.max(1, Math.ceil(n / 8));
  entries.forEach((d, i) => {
    const req = Number(d.requests) || 0;
    const h = req > 0 ? Math.max(2, Math.round((req / max) * (baseline - TOP_PAD))) : 0;
    const x = i * slot + (slot - barW) / 2;
    const y = baseline - h;
    const cost = Number(d.cost) > 0 ? ` · ${fmtCost(d.cost)}` : "";
    if (h > 0) {
      parts.push(
        `<rect class="trace-chart-bar" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h}" rx="1.5"><title>${escapeHtml(d.label)} · ${fmtInt(req)} lượt${cost}</title></rect>`,
      );
    }
    if (i % tickEvery === 0 || i === n - 1) {
      // label sits on top of its bar — same viewBox space, so % maps 1:1
      const topPct = ((baseline - h - 6) / H) * 100;
      ticks.push(`<span style="--x:${(((i * slot + slot / 2) / W) * 100).toFixed(2)}%;--y:${topPct.toFixed(2)}%">${escapeHtml(d.label)}</span>`);
    }
  });
  const unit = hourMode ? "giờ" : "ngày";
  return `<section class="trace-section">
      <h3>Theo ${unit}</h3>
      <p class="trace-section-note">${fmtInt(n)} ${unit} · cao nhất ${fmtInt(max)} lượt/${unit}</p>
      <div class="trace-chart-wrap">
        <svg class="trace-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Lượt theo ${unit}" preserveAspectRatio="xMidYMid meet">${parts.join("")}</svg>
        <div class="trace-chart-ticks" aria-hidden="true">${ticks.join("")}</div>
      </div>
    </section>`;
}