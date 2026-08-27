// Trace overview filter state + dropdown menus (split from trace.js).
// "all" = param omitted. traceFilter is exported and mutated in place by
// trace.js's pill handler so both modules see one state.

import { escapeHtml, fmtInt, shortModel } from "./util.js";


// Filter state for the overview pills. "all" = param omitted.
export const traceFilter = { model: "all", status: "all", range: "30d" };

export function activeParams() {
  const params = {};
  if (traceFilter.model !== "all") params.model = traceFilter.model;
  if (traceFilter.status !== "all") params.status = traceFilter.status;
  const range = dateRange(traceFilter.range);
  if (range.from) params.from = range.from;
  if (range.to) params.to = range.to;
  return params;
}

// 1d = today only; 7d/30d = rolling from today, UTC YYYY-MM-DD
function dateRange(range) {
  if (range === "1d") {
    const today = new Date().toISOString().slice(0, 10);
    return { from: today, to: today };
  }
  if (range !== "7d" && range !== "30d") return { from: "", to: "" };
  const days = range === "7d" ? 7 : 30;
  const from = new Date(Date.now() - (days - 1) * 86400000);
  return { from: from.toISOString().slice(0, 10), to: "" };
}


function optionButton(group, value, label, count = null) {
  const active = traceFilter[group] === value;
  const n = count == null ? "" : ` <span class="pill-count">${fmtInt(count)}</span>`;
  return `<button type="button" class="trace-filter-option" role="option" data-trace-pill="${escapeHtml(group)}" data-value="${escapeHtml(value)}" aria-pressed="${active}" aria-selected="${active}"><span class="trace-option-label">${escapeHtml(label)}</span>${n}</button>`;
}

function countFor(rows, key, value) {
  const row = rows.find((r) => r[key] === value);
  return row ? Number(row.requests) || 0 : null;
}

// One dropdown per group — inline pills don't scale once several models exist.
// highlight the toggle only when the filter differs from its default
const FILTER_DEFAULTS = { model: "all", status: "all", range: "30d" };

function filterMenu(group, label, options, activeLabel) {
  const engaged = traceFilter[group] !== FILTER_DEFAULTS[group];
  const items = options
    .map((opt) => optionButton(group, opt.value, opt.label, opt.count))
    .join("");
  return `<div class="trace-filter">
      <span class="pill-name" id="trace-filter-${escapeHtml(group)}">${escapeHtml(label)}</span>
      <div class="trace-filter-menu">
        <button type="button" class="suggestion-chip trace-filter-toggle${engaged ? " is-engaged" : ""}" data-filter-toggle="${escapeHtml(group)}" aria-haspopup="listbox" aria-expanded="false" aria-labelledby="trace-filter-${escapeHtml(group)}">
          <span class="trace-filter-value">${escapeHtml(activeLabel)}</span>
          <span class="trace-filter-caret" aria-hidden="true">▾</span>
        </button>
        <div class="trace-filter-pop" role="listbox" aria-labelledby="trace-filter-${escapeHtml(group)}" hidden>${items}</div>
      </div>
    </div>`;
}

export function pillBlock(models, byModel, byStatus) {
  const known = models.filter((model) => shortModel(model));
  const unknownCount = countFor(byModel, "model", "unknown");
  const total = byModel.reduce((sum, entry) => sum + (Number(entry.requests) || 0), 0);
  const modelOptions = [{ value: "all", label: "Tất cả", count: total }].concat(
    known.map((model) => ({
      value: String(model),
      label: shortModel(model),
      count: countFor(byModel, "model", String(model)),
    })),
  );
  if (unknownCount != null || traceFilter.model === "unknown") {
    modelOptions.push({
      value: "unknown",
      label: "không rõ",
      count: unknownCount,
    });
  }
  const modelLabel =
    traceFilter.model === "all"
      ? "Tất cả"
      : shortModel(traceFilter.model) || (traceFilter.model === "unknown" ? "không rõ" : "Tất cả");
  const statusOptions = [
    { value: "all", label: "Tất cả", count: null },
    { value: "completed", label: "ok", count: countFor(byStatus, "status", "completed") },
    { value: "failed", label: "lỗi", count: countFor(byStatus, "status", "failed") },
    { value: "loop_limit", label: "loop_limit", count: countFor(byStatus, "status", "loop_limit") },
  ];
  const statusEntry = statusOptions.find((opt) => opt.value === traceFilter.status) || statusOptions[0];
  const rangeOptions = [
    { value: "1d", label: "1 ngày" },
    { value: "7d", label: "7 ngày" },
    { value: "30d", label: "30 ngày" },
  ];
  const rangeEntry = rangeOptions.find((opt) => opt.value === traceFilter.range) || rangeOptions[2];
  return `<div class="trace-pills" role="group" aria-label="Bộ lọc trace">
      ${filterMenu("model", "model", modelOptions, modelLabel)}
      ${filterMenu("status", "trạng thái", statusOptions, statusEntry.label)}
      ${filterMenu("range", "ngày", rangeOptions, rangeEntry.label)}
    </div>`;
}

export function closeFilterPops(scope) {
  scope.querySelectorAll(".trace-filter-pop").forEach((pop) => {
    pop.hidden = true;
  });
  scope.querySelectorAll("[data-filter-toggle]").forEach((btn) => {
    btn.setAttribute("aria-expanded", "false");
  });
}

let filterOutsideBound = false;

export function bindFilterOutside() {
  if (filterOutsideBound) return;
  filterOutsideBound = true;
  const open = () => document.querySelector(".trace-filter-pop:not([hidden])");
  document.addEventListener("click", (event) => {
    if (!open() || event.target.closest(".trace-filter-menu")) return;
    closeFilterPops(document);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open()) closeFilterPops(document);
  });
}
