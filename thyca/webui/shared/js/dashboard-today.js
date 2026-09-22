import { cleanText } from "./format.js";

/* Shared /api/traces paging helper. The "Hôm nay" overview view it originally
   served was removed (GOAL-010 TASK-029); the day-filter/stamp/summary
   helpers went with it. The paging stays because the Cost journal loads the
   whole API window through it (cost.js → cost-data.js). */

/* Raised when /api/traces stops making progress while `total` claims more
   (empty page at the 200-session-file cap, a page that only repeats rows
   already seen, or overlapping pages that never cover every turn): the
   caller must show an incomplete state, never present a partial window as
   the whole day. */
export class TracesIncompleteError extends Error {
  constructor(offset, total) {
    super(`Chưa tải đủ trace: dừng ở ${offset} trên ${total} lượt.`);
    this.name = "TracesIncompleteError";
    this.offset = offset;
    this.total = total;
  }
}

/* Pages through the whole window /api/traces serves, deduplicating on
   (session_id, turn_index) — the pair that identifies a turn; there is no
   independent trace ID. Overlapping pages therefore stay correct, but a page
   that stops making progress (empty, or only turns already seen) while
   `total` claims more is an error, not a shorter day. The API always answers
   { traces, total }: a served page without a readable positive total is
   malformed, never an empty remainder. */
export async function fetchAllTraces(fetchPage) {
  const rows = [];
  const seen = new Set();
  let offset = 0;
  let total = Infinity;
  while (offset < total) {
    const page = await fetchPage(offset);
    const batch = Array.isArray(page?.traces) ? page.traces : [];
    const parsed = Number(page?.total);
    if (batch.length && (!Number.isFinite(parsed) || parsed <= 0)) {
      throw new TracesIncompleteError(offset, page?.total);
    }
    let added = 0;
    for (const row of batch) {
      const key = `${cleanText(row?.session_id)}\u0000${cleanText(row?.turn_index)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push(row);
      added += 1;
    }
    total = parsed;
    if (!batch.length) {
      if (rows.length < total) throw new TracesIncompleteError(offset, total);
      break;
    }
    if (!added && rows.length < total) {
      throw new TracesIncompleteError(offset, total);
    }
    offset += batch.length;
  }
  // Completion is unique rows vs total, not the raw offset: overlapping
  // pages advance offset by batch length while adding fewer new turns, so
  // the loop can end short of `total` without any page failing.
  if (rows.length < total) throw new TracesIncompleteError(offset, total);
  return rows;
}
