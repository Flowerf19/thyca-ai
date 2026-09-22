// Trace journal entry: session/turn lists + boot live in trace-view.js, turn
// entries/steps/detail in trace-turns.js, deep-link + URL sync in
// trace-deeplink.js. boot() runs here, after all three modules evaluated.
import "./trace-turns.js";
import "./trace-deeplink.js";
import { boot } from "./trace-view.js";

void boot();
