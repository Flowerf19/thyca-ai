// Ambient copy for live chat. Pure mapping — 40 fixed lines, no extras.
// Builtin allowlist matches thyca.tools registry (files + memory), not MCP.

export const BUILTIN_TOOLS = Object.freeze([
  "bash",
  "read",
  "write",
  "edit",
  "memory_remember",
  "memory_search",
  "memory_recent",
  "memory_get",
  "memory_forget",
  "memory_reinforce",
  "memory_update",
]);

const BUILTIN = new Set(BUILTIN_TOOLS);

export const AMBIENT = Object.freeze({
  llm: Object.freeze([
    "đang ngân nga…",
    "llm đang lấy hơi",
    "nghe nhịp trong đầu",
    "gạch nháp lên lề",
    "nắn lại giai điệu",
    "nghĩ một nhịp đã",
  ]),
  wait: Object.freeze([
    "nghỉ beat…",
    "để mực khô một chút",
    "im trên khuông",
    "giữ nhịp lặng",
  ]),
  retry: Object.freeze([
    "đếm lại từ đầu phách",
    "đánh lại nhịp vừa rồi",
  ]),
  bash: Object.freeze([
    "lật trang bash",
    "gõ nhịp shell",
    "chạy một phách lệnh",
    "lắng tiếng máy gõ",
  ]),
  read: Object.freeze([
    "đọc khuông file",
    "lit lại dòng cũ",
    "soi nốt trên trang",
  ]),
  write: Object.freeze([
    "tẩy và viết lại",
    "sửa nhịp trên giấy",
    "ghi đè một câu",
    "kéo mực qua chỗ lệch",
  ]),
  memory: Object.freeze([
    "dán vào sổ nhớ",
    "ghim mẩu bên lề",
    "cất phách vào ngăn",
  ]),
  addon: Object.freeze([
    "gọi nhịp phụ ngoài sổ",
    "mở ngăn dụng cụ thêm",
    "chạy tool khách",
    "nối một nhịp MCP",
  ]),
  skill: Object.freeze([
    "mở bài skill",
    "giở trang skill",
    "luyện một mẫu skill",
    "gấp skill lại",
  ]),
  completed: Object.freeze([
    "chốt nhịp.",
    "gác bút, xong trang.",
  ]),
  failed: Object.freeze([
    "phách lệch.",
    "mực lem, dừng lại.",
  ]),
  sys: Object.freeze([
    "gõ một nhịp hệ thống",
    "chỉnh lại trang trong sổ",
  ]),
});

export function bucketForEvent(event) {
  if (!event || typeof event !== "object") return "wait";
  const type = event.type;
  if (typeof type !== "string") return "wait";
  if (type === "llm.retry") return "retry";
  if (type === "turn.completed") return "completed";
  if (type === "turn.failed") return "failed";
  if (type.startsWith("skill.")) return "skill";
  if (type.startsWith("tool.")) return bucketForTool(event.name);
  if (type.startsWith("llm.") || type === "turn.accepted") return "llm";
  return "wait";
}

function bucketForTool(name) {
  if (typeof name !== "string" || !name) return "sys";
  if (!BUILTIN.has(name)) return "addon";
  if (name === "bash") return "bash";
  if (name === "read") return "read";
  if (name === "write" || name === "edit") return "write";
  if (name.startsWith("memory_")) return "memory";
  return "sys";
}

export function ambientLineForEvent(event) {
  const bucket = bucketForEvent(event);
  const lines = AMBIENT[bucket] || AMBIENT.wait;
  return lines[indexFor(event, lines.length)];
}

function indexFor(event, length) {
  const key = [
    event && event.type,
    event && event.name,
    event && event.round,
    event && event.call_id,
  ]
    .map((value) => (value == null ? "" : String(value)))
    .join(":");
  let hash = 2166136261;
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) % length;
}
