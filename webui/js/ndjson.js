export function createNdjsonDecoder() {
  let decoder = null;
  let buffer = "";
  return { push, flush };

  function push(chunkUint8) {
    const out = [];
    decoder ??= new TextDecoder("utf-8", { stream: true });
    buffer += decoder.decode(chunkUint8, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, end).trim();
      buffer = buffer.slice(end + 1);
      if (!line) continue;
      try {
        out.push(JSON.parse(line));
      } catch {
        throw new Error("Phản hồi từ Thyca không hợp lệ.");
      }
    }
    return out;
  }

  function flush() {
    const out = [];
    if (decoder) {
      buffer += decoder.decode();
      decoder = null;
    }
    const line = buffer.trim();
    buffer = "";
    if (!line) return out;
    try {
      out.push(JSON.parse(line));
    } catch {
      throw new Error("Phản hồi từ Thyca không hợp lệ.");
    }
    return out;
  }
}
