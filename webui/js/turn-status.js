// Operational event -> status text. Pure mapping, no DOM, no timers.
// Unknown event types return null so the caller keeps the current text.
export function statusTextForEvent(event) {
  if (!event || typeof event !== "object") return null;
  const type = event.type;
  if (typeof type !== "string") return null;
  switch (type) {
    case "turn.accepted":
      return "Đã nhận lượt…";
    case "llm.started": {
      const round = Number.isInteger(event.round) ? event.round : null;
      return round === null ? null : `Đang xử lý vòng ${round}…`;
    }
    case "llm.finished": {
      const count = Number.isInteger(event.tool_count) ? event.tool_count : 0;
      return count > 0 ? `Đã chọn ${count} công cụ…` : "Đang hoàn tất câu trả lời…";
    }
    case "tool.started": {
      const name = publicName(event.name);
      return `Đang dùng ${name}…`;
    }
    case "tool.finished": {
      const name = publicName(event.name);
      return event.ok === true
        ? `${name} đã xong…`
        : `${name} gặp lỗi, đang xử lý tiếp…`;
    }
    case "session.naming.started":
      return "Đang đặt tên phiên…";
    case "session.naming.finished":
      return "Đang hoàn tất…";
    case "turn.completed":
      return "Đã xong.";
    case "turn.failed":
      return "Lượt đã dừng.";
    default:
      return null;
  }
}

function publicName(name) {
  if (typeof name !== "string" || !name) return "tool";
  return name;
}
