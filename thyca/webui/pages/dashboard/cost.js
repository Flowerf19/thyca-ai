// Cost journal entry: snapshot state + loading live in cost-view.js, row
// renderers and the two paged lists in cost-rows.js. Booting here (after both
// modules evaluated) keeps the module bodies side-effect free.
import { bootCostJournal } from "./cost-view.js";

bootCostJournal();
