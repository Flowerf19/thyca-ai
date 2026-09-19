import { getJson } from "./backend/api.js";
import { formatCost, formatDuration, formatInteger, formatSessionTime, statusLabel } from "./backend/format.js";
import { recentTraces, todaySummary } from "./backend/dashboard-today.js";

(() => {
  const compact = matchMedia("(max-width: 56rem)");
  const buttons = [...document.querySelectorAll("[data-view]")];
  const blocks = {
    today: document.querySelector("#hom-nay"),
    cost: document.querySelector("#chi-phi"),
    request: document.querySelector("#request"),
    usage: document.querySelector("#su-dung"),
  };
  const hashes = { today: "#hom-nay", cost: "#chi-phi", request: "#request", usage: "#su-dung" };
  const status = document.querySelector("#today-status");

  function viewFromHash() {
    if (location.hash === "#hom-nay") return "today";
    if (location.hash === "#su-dung") return "usage";
    if (location.hash === "#request") return "request";
    return "cost";
  }

  const show = (view) => {
    const next = blocks[view] ? view : "today";
    const stack = compact.matches;
    for (const [key, node] of Object.entries(blocks)) {
      if (node) node.hidden = stack ? false : key !== next;
    }
    buttons.forEach((button) => {
      const on = button.dataset.view === next;
      button.classList.toggle("is-active", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const hash = hashes[next];
    if (hash && location.hash !== hash) history.replaceState(null, "", hash);
    const surface = document.querySelector(".dashboard-surface");
    if (surface) {
      surface.scrollTop = 0;
      requestAnimationFrame(() => { surface.scrollTop = 0; });
    }
  };

  buttons.forEach((button) => {
    button.addEventListener("click", () => show(button.dataset.view));
  });
  window.addEventListener("hashchange", () => show(viewFromHash()));
  compact.addEventListener("change", () => show(viewFromHash()));

  function statusText(message = "", kind = "") {
    if (!status) return;
    status.textContent = message;
    status.className = `screen-status${kind ? ` is-${kind}` : ""}`;
  }

  function metricCard(title, value, note) {
    const item = document.createElement("li");
    item.className = "today-metric";
    const heading = document.createElement("h3");
    heading.textContent = title;
    const strong = document.createElement("strong");
    strong.textContent = value;
    const meta = document.createElement("span");
    meta.className = "today-metric-note";
    meta.textContent = note;
    item.append(heading, strong, meta);
    return item;
  }

  function renderMetrics(summary) {
    const list = document.querySelector("#today-metrics");
    if (!list) return;
    list.replaceChildren(
      metricCard(
        "Lượt chạy hôm nay",
        formatInteger(summary.runs),
        `${formatInteger(summary.completed)} thành công · ${formatInteger(summary.failed)} lỗi`,
      ),
      metricCard(
        "Tỷ lệ thành công",
        summary.successRate == null ? "—" : `${Math.round(summary.successRate * 1000) / 10}%`.replace(".", ","),
        "Trên lượt chạy đã kết thúc",
      ),
      metricCard(
        "Thời gian trung bình",
        summary.averageMs == null ? "—" : formatDuration(summary.averageMs),
        "Tính trên lượt chạy đã kết thúc",
      ),
      metricCard("Chi phí hôm nay", summary.costUsd == null ? "—" : formatCost(summary.costUsd, 4), "Tổng các lượt chạy hôm nay"),
    );
  }

  function recentRow(trace) {
    const item = document.createElement("li");
    item.className = "today-run";
    const time = document.createElement("span");
    time.className = "today-run-time";
    time.textContent = formatSessionTime(trace.started_at);
    const body = document.createElement("div");
    body.className = "today-run-body";
    const heading = document.createElement("h3");
    heading.textContent = trace.title || trace.session_id;
    const state = document.createElement("span");
    state.className = `today-run-status${trace.status === "failed" ? " is-error" : ""}`;
    state.textContent = statusLabel(trace.status);
    heading.append(state);
    const description = document.createElement("p");
    description.textContent = `${trace.model || "—"} · ${formatInteger(trace.total_tokens)} token`;
    const meta = document.createElement("div");
    meta.className = "today-run-meta";
    for (const label of [
      `Lượt ${formatInteger((Number(trace.turn_index) || 0) + 1)}`,
      formatDuration(trace.latency_ms),
      trace.cost_usd == null ? null : formatCost(trace.cost_usd),
    ]) {
      if (!label) continue;
      const span = document.createElement("span");
      span.textContent = label;
      meta.append(span);
    }
    body.append(heading, description, meta);
    item.append(time, body);
    return item;
  }

  function renderRecent(traces) {
    const list = document.querySelector("#today-recent");
    if (!list) return;
    if (!traces.length) {
      const note = document.createElement("li");
      note.className = "today-run-empty screen-note";
      note.textContent = "Chưa có lượt chạy nào.";
      list.replaceChildren(note);
      return;
    }
    list.replaceChildren(...traces.map(recentRow));
  }

  async function loadToday() {
    statusText("Đang đọc trace…");
    try {
      const data = await getJson("/api/traces?limit=200");
      const rows = Array.isArray(data?.traces) ? data.traces : [];
      renderMetrics(todaySummary(rows));
      renderRecent(recentTraces(rows, 5));
      statusText();
    } catch (error) {
      renderMetrics({ runs: 0, completed: 0, failed: 0, successRate: null, averageMs: null, costUsd: null });
      renderRecent([]);
      statusText(error instanceof Error && error.message ? error.message : "Không tải được trace.", "error");
    }
  }

  show(viewFromHash());
  void loadToday();
})();
