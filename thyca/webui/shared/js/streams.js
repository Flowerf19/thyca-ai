/* Shared NDJSON streaming (W6 TASK-011, layout decision 8): split verbatim from
   shared/js/api.js. Only chat streams; other pages import http.js. */
import { ApiError } from "./http.js";

export function yieldToRender() {
  // Animation frames can stop in background tabs. Never gate network
  // consumption on a paint; yield once per chunk, not once per event.
  return new Promise((resolve) => globalThis.setTimeout(resolve, 0));
}

export async function readNdjson(response, onEvent, fallbackMessage) {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      payload && typeof payload.error === "string" ? payload.error : fallbackMessage,
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
    if (
      event.type === "turn.completed"
      || event.type === "turn.failed"
      || event.type === "turn.cancelled"
    ) terminal = event;
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
    await yieldToRender();
  }
  buffer += decoder.decode();
  consume(buffer);

  if (!terminal) throw new ApiError("Luồng trả lời kết thúc quá sớm.");
  if (terminal.type === "turn.failed") {
    throw new ApiError(terminal.message || "Lượt trò chuyện đã dừng.");
  }
  return terminal.detail;
}

export async function openNdjson(url, options, fallbackMessage, onEvent) {
  let response;
  try {
    response = await fetch(url, { cache: "no-store", ...options });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new ApiError("Không kết nối được với backend Thyca.");
  }
  return readNdjson(response, onEvent, fallbackMessage);
}

export async function postNdjson(url, body, onEvent, { signal } = {}) {
  return openNdjson(
    url,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    },
    "Không gửi được tin nhắn.",
    onEvent,
  );
}

export async function getNdjson(url, onEvent, { signal } = {}) {
  return openNdjson(
    url,
    { method: "GET", signal },
    "Không theo dõi được lượt.",
    onEvent,
  );
}
