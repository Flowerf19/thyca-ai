import { cleanText, splitMemoryHeading } from "./format.js";

export function memoryFromLeaf(leaf) {
  const heading = splitMemoryHeading(leaf);
  return {
    id: cleanText(leaf?.chunk_id),
    sessionId: cleanText(leaf?.session_id),
    title: heading.title,
    description: cleanText(leaf?.snippet, "(trống)").replace(/^-\s+/, ""),
    date: cleanText(leaf?.timeline_day, "Không rõ ngày"),
    time: heading.time,
    uses: Math.max(0, Number(leaf?.get_count) || 0),
    searches: Math.max(0, Number(leaf?.search_count) || 0),
    expiresAt: cleanText(leaf?.expires_at),
    raw: leaf,
  };
}

function score(memory) {
  return memory.uses + memory.searches;
}

export function selectMemories(leaves, { view = "day", query = "" } = {}) {
  const needle = cleanText(query).toLocaleLowerCase("vi");
  const rows = (Array.isArray(leaves) ? leaves : [])
    .map(memoryFromLeaf)
    .filter((memory) => {
      if (!needle) return true;
      return `${memory.title} ${memory.description} ${memory.date}`
        .toLocaleLowerCase("vi")
        .includes(needle);
    });

  if (view === "used-more") {
    return rows.sort((a, b) => b.uses - a.uses || b.searches - a.searches || a.title.localeCompare(b.title, "vi"));
  }
  if (view === "searched-more") {
    return rows.sort((a, b) => b.searches - a.searches || b.uses - a.uses || a.title.localeCompare(b.title, "vi"));
  }
  if (view === "used-less") {
    return rows.sort((a, b) => score(a) - score(b) || a.title.localeCompare(b.title, "vi"));
  }
  return rows.sort((a, b) => b.date.localeCompare(a.date) || b.time.localeCompare(a.time) || a.title.localeCompare(b.title, "vi"));
}

export function selectCanonical(files, query = "") {
  const needle = cleanText(query).toLocaleLowerCase("vi");
  const order = { "USER.md": 0, "SOUL.md": 1, "IDENTITY.md": 2 };
  return (Array.isArray(files) ? files : [])
    .filter((file) => file && typeof file.name === "string")
    .map((file) => ({
      name: file.name,
      content: String(file.content || ""),
      title: canonicalTitle(file.name),
      description: canonicalDescription(file.name),
    }))
    .filter((file) => !needle || `${file.name} ${file.title} ${file.content}`.toLocaleLowerCase("vi").includes(needle))
    .sort((a, b) => (order[a.name] ?? 9) - (order[b.name] ?? 9) || a.name.localeCompare(b.name));
}

function canonicalTitle(name) {
  if (name === "USER.md") return "Hồ sơ của bạn";
  if (name === "SOUL.md") return "Giọng nói của Thyca";
  if (name === "IDENTITY.md") return "Danh tính Thyca";
  return name;
}

function canonicalDescription(name) {
  if (name === "USER.md") return "Tên, sở thích và bối cảnh Thyca dùng khi trả lời.";
  if (name === "SOUL.md") return "Cách Thyca trò chuyện và hỗ trợ bạn.";
  if (name === "IDENTITY.md") return "Danh tính và giới hạn của trợ lý.";
  return "File hồ sơ được đưa vào ngữ cảnh của Thyca.";
}
