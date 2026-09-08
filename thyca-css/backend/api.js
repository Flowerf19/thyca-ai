const DEFAULT_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function requestJson(url, options = {}) {
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

export async function postNdjson(url, body, onEvent, { signal } = {}) {
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new ApiError("Không kết nối được với backend Thyca.");
  }

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      payload && typeof payload.error === "string" ? payload.error : "Không gửi được tin nhắn.",
      response.status,
    );
  }
  if (!response.body) throw new ApiError("Backend không mở luồng trả lời.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { stream: true });
  let buffer = "";
  let terminal = null;

  const consume = (line) => {
    if (!line.trim()) return;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      throw new ApiError("Luồng trả lời từ backend không hợp lệ.");
    }
    onEvent(event);
    if (event.type === "turn.completed" || event.type === "turn.failed") terminal = event;
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      consume(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
    }
  }
  buffer += decoder.decode();
  consume(buffer);

  if (!terminal) throw new ApiError("Luồng trả lời kết thúc quá sớm.");
  if (terminal.type === "turn.failed") {
    throw new ApiError(terminal.message || "Lượt trò chuyện đã dừng.");
  }
  return terminal.detail;
}
