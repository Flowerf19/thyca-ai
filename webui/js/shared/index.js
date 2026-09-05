// Pure-only barrel: every module re-exported here must be importable in
// Node without a DOM (no document/window access at import time).
// DOM modules (dom.js, drawer.js) are imported directly — never via this
// barrel — so Node-based tests keep passing.
export { icons, modes, personaPages } from "./data.js";
export { state } from "./state.js";
export {
  cleanTitle,
  escapeHtml,
  fmtCompact,
  fmtCost,
  fmtInt,
  fmtIso,
  fmtLatency,
  formatUpdated,
  getJson,
  postJson,
  shortModel,
  statusLabel,
} from "./util.js";
export { createNdjsonDecoder } from "./ndjson.js";
export { formatMarkdown } from "./markdown.js";
