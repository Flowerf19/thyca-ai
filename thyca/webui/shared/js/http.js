/* Shared HTTP verbs (W6 TASK-011, layout decision 8): split verbatim from
   shared/js/api.js. Import directly; api.js stays as a barrel. */
const DEFAULT_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function requestJson(url, options = {}) {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(url, {
      ...options,
      timeoutMs: undefined,
      cache: "no-store",
      signal: options.signal ?? controller.signal,
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new ApiError("Hết thời gian chờ backend. Thử lại nhé.");
    }
    throw new ApiError("Không kết nối được với backend Thyca.");
  } finally {
    globalThis.clearTimeout(timer);
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload && typeof payload.error === "string"
      ? payload.error
      : `Backend trả về HTTP ${response.status}.`;
    throw new ApiError(message, response.status);
  }
  if (!payload || typeof payload !== "object") {
    throw new ApiError("Backend trả về dữ liệu không hợp lệ.", response.status);
  }
  return payload;
}

export function getJson(url, options) {
  return requestJson(url, options);
}

export function postJson(url, body, options = {}) {
  return requestJson(url, {
    ...options,
    method: "POST",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: JSON.stringify(body),
  });
}

export function deleteJson(url, body, options = {}) {
  return requestJson(url, {
    ...options,
    method: "DELETE",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json", ...(options.headers || {}) },
          body: JSON.stringify(body),
        }),
  });
}

export function patchJson(url, body, options = {}) {
  return requestJson(url, {
    ...options,
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: JSON.stringify(body),
  });
}
