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
    case "llm.retry": {
      const attempt = Number.isInteger(event.attempt) ? event.attempt : null;
      const max = Number.isInteger(event.max_attempts) ? event.max_attempts : null;
      if (attempt === null || max === null) return null;
      return `Đang thử lại (${attempt}/${max})…`;
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
    case "skill.started":
      return `Đang mở skill ${skillName(event)}…`;
    case "skill.finished":
      return event.ok === true
        ? `Đã mở skill ${skillName(event)}…`
        : `Skill ${skillName(event)} không đọc được, đang xử lý tiếp…`;
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

// First-seen order, identical names collapse: bash, bash, memory_recent → bash ×2 · memory_recent
export function collapseNames(names) {
  const order = [];
  const count = new Map();
  for (const raw of Array.isArray(names) ? names : []) {
    const key = String(raw || "").trim() || "tool";
    if (!count.has(key)) order.push(key);
    count.set(key, (count.get(key) || 0) + 1);
  }
  return order.map((key) => {
    const n = count.get(key);
    return n > 1 ? `${key} ×${n}` : key;
  }).join(" · ");
}

export function batchDoneText(names) {
  const summary = collapseNames(names);
  return summary ? `Đã chạy ${summary}…` : "Đã chạy công cụ…";
}

// Skill events keep the backend's own fallback word so status never says
// "tool" for a skill load.
function skillName(event) {
  const name = event && typeof event.name === "string" ? event.name.trim() : "";
  return name || "skill";
}
