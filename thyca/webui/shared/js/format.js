const DATE_TIME = new Intl.DateTimeFormat("vi-VN", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const DATE_ONLY = new Intl.DateTimeFormat("vi-VN", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
const TIME_ONLY = new Intl.DateTimeFormat("vi-VN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function cleanText(value, fallback = "") {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text || fallback;
}

function dateValue(value) {
  const date = new Date(String(value || ""));
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value) {
  const date = dateValue(value);
  return date ? DATE_TIME.format(date) : cleanText(value, "—");
}

export function formatDate(value) {
  const date = dateValue(value);
  return date ? DATE_ONLY.format(date) : cleanText(value, "—");
}

export function formatTime(value) {
  const date = dateValue(value);
  return date ? TIME_ONLY.format(date) : cleanText(value, "—");
}

export function formatSessionTime(value, now = new Date()) {
  const date = dateValue(value);
  if (!date) return "—";
  const sameDay = date.toLocaleDateString("vi-VN") === now.toLocaleDateString("vi-VN");
  if (sameDay) {
    return new Intl.DateTimeFormat("vi-VN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  }
  const days = Math.floor((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days === 1) return "Hôm qua";
  if (days > 1 && days < 7) return `${days} ngày trước`;
  return DATE_ONLY.format(date);
}

function startOfDay(value) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

export function formatInteger(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number).toLocaleString("vi-VN") : "—";
}

export function formatCompact(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const abs = Math.abs(number);
  const short = (divisor, suffix) => `${(number / divisor).toLocaleString("vi-VN", {
    maximumFractionDigits: 2,
  })}${suffix}`;
  if (abs >= 1_000_000_000) return short(1_000_000_000, "B");
  if (abs >= 1_000_000) return short(1_000_000, "M");
  if (abs >= 10_000) return short(1_000, "K");
  return formatInteger(number);
}

export function formatCost(value, digits = 6) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `$${number.toLocaleString("en-US", {
    minimumFractionDigits: Math.min(4, digits),
    maximumFractionDigits: digits,
  })}`;
}

/* Share as a percent string, or "—" when either side is unknown or the
   denominator is not a positive number. Number(null) is 0, so unpriced
   values are rejected before coercion — an unpriced row must never pose as
   0%, and total=0 must not make NaN. One formatter for the Cost, Token and
   Request journals (cost-data.js re-exports it for existing importers). */
export function shareLabel(value, total) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number) || !Number.isFinite(total) || total <= 0) return "—";
  return `${Math.round(number / total * 100)}%`;
}

/* Meter fill as a CSS var value, clamped to 0–100% for rendering safety;
   null when unknown so the meter stays empty. */
export function shareRatio(value, total) {
  if (value == null || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number) || !Number.isFinite(total) || total <= 0) return null;
  const ratio = Math.min(Math.max(number / total * 100, 0), 100);
  return `${ratio}%`;
}

export function formatDuration(value) {
  const ms = Number(value);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${formatInteger(ms)}ms`;
  return `${(ms / 1000).toLocaleString("vi-VN", { maximumFractionDigits: 2 })}s`;
}

export function splitMemoryHeading(leaf) {
  const raw = cleanText(leaf?.heading).replace(/^##\s*/, "");
  const match = raw.match(/^(\d{2}:\d{2})\s*[—-]\s+(.+)$/);
  return {
    time: match?.[1] || "",
    title: match?.[2] || raw || cleanText(leaf?.chunk_id, "Trang không tên"),
  };
}

/* Hash text is whatever the address bar holds: "%" alone makes
   decodeURIComponent throw, so a stray escape degrades to raw text instead
   of breaking the handler that reads it. */
export function decodeHash(hash) {
  const raw = String(hash ?? "").replace(/^#/, "");
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export function statusLabel(status) {
  if (status === "completed") return "Thành công";
  if (status === "failed") return "Lỗi";
  if (status === "loop_limit") return "Chạm giới hạn";
  return cleanText(status, "—");
}
