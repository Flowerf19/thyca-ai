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

/* One full-window paging loop for /api/traces, shared by the Cost/Token
   journals (fetchAllTraces: throw on incomplete) and the Trace journal
   (collectTracePages in trace-data.js: return { complete: false }). The
   loop mechanics are one — page until the reported total is covered,
   dedupe on (session_id, turn_index), judge completion on unique rows vs
   total — and the two callers differ only in policy: fetch-arg shape,
   blank-session rows (kept vs skipped-and-counted), turn-index key norm,
   fetch-error and missing-total handling, and whether an incomplete window
   throws or returns. Defaults ARE the fetchAllTraces policy. */
export async function collectTraceWindow(fetchPage, {
  limit = 200,
  objectArg = false,
  skipBlankSession = false,
  indexKey = (value) => cleanText(value),
  catchFetchError = false,
  tolerateMissingTotal = false,
  checkCoverageOnRepeat = true,
  onIncomplete = "throw",
  incompleteError = (_site, ctx) => new TracesIncompleteError(ctx.offset, ctx.total),
} = {}) {
  const rows = [];
  const seen = new Set();
  // Rows dropped for a missing session_id: they count toward the server's
  // total but can never join `rows`, so completeness is judged on
  // rows + skipped, not rows alone.
  let skipped = 0;
  let offset = 0;
  const fail = (site, ctx) => {
    const error = incompleteError(site, { offset, rows: rows.length, ...ctx });
    if (onIncomplete === "throw") throw error;
    return { rows, complete: false, error };
  };
  const done = () => (onIncomplete === "throw" ? rows : { rows, complete: true });
  for (;;) {
    let page;
    try {
      page = await (objectArg ? fetchPage({ limit, offset }) : fetchPage(offset));
    } catch (error) {
      if (!catchFetchError) throw error;
      return { rows, complete: false, error };
    }
    const batch = Array.isArray(page?.traces) ? page.traces : [];
    const parsed = Number(page?.total);
    if (batch.length && (!Number.isFinite(parsed) || parsed <= 0) && !tolerateMissingTotal) {
      return fail("served-without-total", { total: page?.total });
    }
    let added = 0;
    for (const row of batch) {
      const sessionId = cleanText(row?.session_id);
      if (!sessionId && skipBlankSession) {
        skipped += 1;
        continue;
      }
      const key = `${sessionId}\u0000${indexKey(row?.turn_index)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push(row);
      added += 1;
    }
    const total = parsed;
    if (!batch.length) {
      // Window exhausted. Without a usable total there is nothing left to
      // expect; with one, fewer rows than total means pages went missing.
      if (!Number.isFinite(total)) return done();
      if (rows.length >= total) return done();
      return fail("short-read", { total });
    }
    if (!added && (!checkCoverageOnRepeat || rows.length < total)) {
      // A full page of already-seen rows: the offset is not advancing —
      // stop instead of looping forever.
      return fail("repeat-page", { total });
    }
    offset += batch.length;
    if (Number.isFinite(total) && offset >= total) {
      // Completion is unique rows vs total, not the raw offset: overlapping
      // pages advance offset by batch length while adding fewer new turns,
      // so the offset can pass `total` while deduped rows are still missing
      // (rows [0,1] then [1,2] with total 4 never read row 3). Only the
      // deduped count — plus the rows skipped as invalid — proves the window
      // was fully read.
      if (rows.length + skipped >= total) return done();
      return fail("short-read", { total });
    }
    if (!Number.isFinite(total) && batch.length < limit) return done();
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
  return collectTraceWindow(fetchPage);
}
