/* Shared API barrel (W6 TASK-011, layout decision 8): kept permanently so
   existing `.../shared/js/api.js` imports keep working. New code imports
   directly from ./http.js (verbs) or ./streams.js (NDJSON). */
export { ApiError, requestJson, getJson, postJson, deleteJson, patchJson } from "./http.js";
export { yieldToRender, readNdjson, openNdjson, postNdjson, getNdjson } from "./streams.js";
