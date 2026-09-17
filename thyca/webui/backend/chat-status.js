// Operational labels for live chat. No ambient copy.

export const SEND_ERROR_STATUS = "Không gửi được — thử lại.";
export const TURN_FAILED_STATUS = "Lượt đã dừng.";

export function brandState(event) {
  if (event?.type === "turn.failed") return "error";
  if (event?.type === "turn.completed") return "idle";
  return "busy";
}

function usageName(rawName) {
  const name = String(rawName || "tool");
  return name.startsWith("memory_") ? "memories" : name;
}

function tallyNames(names) {
  const counts = new Map();
  for (const rawName of names || []) {
    if (!rawName) continue;
    const name = usageName(rawName);
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return counts;
}

// One "what ran" line. Busy: "Đang dùng: bash, edit". Settled: "Đã dùng: bash x2, edit x1".
export function usageLine(completedNames, activeNames = []) {
  const completed = tallyNames(completedNames);
  const active = tallyNames(activeNames);
  const names = [...new Set([...completed.keys(), ...active.keys()])];
  if (!names.length) return null;
  const busy = active.size > 0;
  return {
    label: busy ? "Đang dùng:" : "Đã dùng:",
    body: busy
      ? names.join(", ")
      : names.map((name) => `${name} x${completed.get(name)}`).join(", "),
  };
}
