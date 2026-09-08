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
  second: "2-digit",
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

export function dateValue(value) {
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

export function providerLabel(baseUrl) {
  try {
    const host = new URL(String(baseUrl || "")).hostname.toLowerCase();
    if (host.includes("openai.com")) return "OpenAI";
    if (host.includes("openrouter.ai")) return "OpenRouter";
    if (host.includes("localhost") || host === "127.0.0.1") return "Local";
    return host || "OpenAI-compatible";
  } catch {
    return "OpenAI-compatible";
  }
}

export function statusLabel(status) {
  if (status === "completed") return "Thành công";
  if (status === "failed") return "Lỗi";
  if (status === "loop_limit") return "Chạm giới hạn";
  return cleanText(status, "—");
}
