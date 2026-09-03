// Shared helpers: HTML escaping, fetch wrappers, vi-VN formatters.
// No DOM at import time — safe for Node-based tests.

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export async function getJson(url, { timeoutMs = 15000 } = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { cache: "no-store", signal: controller.signal });
    if (!response.ok) return null;
    return response.json();
  } catch {
    // Timeout / mất mạng: trả null như response !ok để caller rơi vào
    // nhánh lỗi visible sẵn có (LOAD_ERROR_BODY / showPageError).
    return null;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function postJson(url, body, { timeoutMs = 15000 } = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("Hết thời gian chờ — thử lại.");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload && payload.error ? String(payload.error) : "Không gửi được.";
    throw new Error(message);
  }
  return payload;
}

export function formatUpdated(value) {
  if (!value) return "";
  const stamp = new Date(String(value));
  if (Number.isNaN(stamp.getTime())) return String(value);
  return stamp.toLocaleString("vi-VN", { dateStyle: "medium", timeStyle: "short" });
}

export function fmtInt(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("vi-VN");
}

export function fmtCost(value) {
  const n = Number(value);
  if (value == null || value === "" || !Number.isFinite(n)) return "—";
  return "$" + n.toLocaleString("vi-VN", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
}

export function fmtLatency(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n < 1000) return `${fmtInt(Math.round(n))} ms`;
  const s = n / 1000;
  if (s < 10) return `${s.toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`;
  return `${Math.round(s).toLocaleString("vi-VN")} s`;
}

export function fmtIso(value) {
  if (!value) return "";
  const stamp = new Date(String(value));
  if (Number.isNaN(stamp.getTime())) return String(value);
  const time = stamp.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const day = stamp.toLocaleDateString("vi-VN", { day: "numeric", month: "short" });
  return `${time} ${day}`;
}

export function shortModel(model) {
  const raw = String(model || "").trim();
  if (!raw || raw.toLowerCase() === "unknown") return "";
  return raw;
}

// Strip markdown chrome from titles: **, __, wrapping backticks, collapsed whitespace.
export function cleanTitle(value) {
  const text = String(value || "")
    .replace(/\*\*/g, "")
    .replace(/__/g, "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return escapeHtml(text);
}

export function statusLabel(status) {
  if (status === "completed") return "ok";
  if (status === "failed") return "lỗi";
  return String(status || "—");
}