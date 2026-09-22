import {
  brandState,
  TURN_FAILED_STATUS,
  usageLine,
} from "./chat-status.js";
import { bindThinkingToggle, createThinkingNote, elapsedLabel } from "./chat-thinking.js";
import { chatBrandHeader, fillUsageRow, setChatBrand, usageRowSkeleton } from "./chat-view.js";

export function createLiveStatus(root, startedAt) {
  const article = document.createElement("article");
  article.className = "live-card live-status";
  article.setAttribute("aria-label", "Thyca đang trả lời");
  article.setAttribute("aria-live", "polite");
  const live = {
    article,
    startedAt: startedAt || null,
    brand: null,
    thinking: null,
    notes: [],
    notesWrap: null,
    reply: null,
    // Tool record is per round (reset on each new segment), like the
    // settled transcript where every round keeps its own usage line.
    // The row renders after the reply text: tools execute after think.
    round: { active: new Map(), completed: [], usage: null },
  };
  const notesWrap = document.createElement("div");
  notesWrap.className = "thinking-notes";
  notesWrap.id = `thinking-notes-${++liveSerial}`;
  const thinking = createThinkingNote({
    live: true,
    startedAt,
    onElapsed: thinkingElapsed(live),
  });
  notesWrap.append(thinking.note);
  const header = chatBrandHeader({
    state: "busy",
    status: `đang suy nghĩ · ${elapsedLabel(0)}`,
    expandable: true,
  });
  bindThinkingToggle(header.querySelector(".chat-brand-line"), { bodies: [notesWrap] });
  article.append(header, notesWrap);
  root.append(article);
  live.brand = header;
  live.thinking = thinking;
  live.notesWrap = notesWrap;
  live.notes.push(thinking);
  return live;
}

let liveSerial = 0;

function thinkingElapsed(live) {
  return (sec) => setChatBrand(live, { status: `đang suy nghĩ · ${elapsedLabel(sec)}` });
}

// One segment per LLM round: settle the previous note WITH its tool line
// (each round keeps its own "Đã dùng", like the settled transcript) and
// stream the new round into a fresh note with a fresh tool record below
// it. Empty previous notes hide themselves via settle().
function startThinkingSegment(live) {
  live.thinking?.settle();
  const next = createThinkingNote({
    live: true,
    startedAt: live.startedAt ?? undefined,
    onElapsed: thinkingElapsed(live),
  });
  live.notesWrap.append(next.note);
  live.notes.push(next);
  live.thinking = next;
  live.reply = null;
  live.round = { active: new Map(), completed: [], usage: null };
}

// The round's usage row lives after its reply text (tools execute after
// think), mirroring the settled transcript. Created on the first tool
// event of the round, updated in place, left in place on segment switch.
function syncRoundUsage(live) {
  const active = [...live.round.active.values()];
  if (!usageLine(live.round.completed, active)) {
    live.round.usage?.remove();
    live.round.usage = null;
    return;
  }
  if (!live.round.usage) {
    live.round.usage = usageRowSkeleton();
    live.notesWrap.append(live.round.usage);
  }
  fillUsageRow(live.round.usage, live.round.completed, active);
}

// The visible reply streams into its own paragraph below the round's
// thinking note; plain text here — markdown renders in the transcript.
function replySegment(live) {
  if (!live.reply) {
    const p = document.createElement("p");
    p.className = "reply-live";
    live.notesWrap.append(p);
    live.reply = p;
  }
  return live.reply;
}

// A re-follow replays the whole turn; rebuild segments from scratch so the
// replay does not duplicate the notes an earlier attempt already added.
export function resetLiveStatus(live, startedAt) {
  if (!live?.notesWrap) return;
  live.notesWrap.replaceChildren();
  const fresh = createThinkingNote({
    live: true,
    startedAt: startedAt ?? live.startedAt ?? undefined,
    onElapsed: thinkingElapsed(live),
  });
  live.notesWrap.append(fresh.note);
  live.notes = [fresh];
  live.thinking = fresh;
  live.reply = null;
  live.round = { active: new Map(), completed: [], usage: null };
}

export function updateLiveStatus(live, event) {
  if (event?.type === "llm.thinking") {
    live.thinking?.append(event.delta);
    return;
  }
  if (event?.type === "llm.content") {
    const p = replySegment(live);
    p.append(document.createTextNode(event.delta));
    return;
  }
  if (event?.type === "llm.started" && event.round > 1) {
    startThinkingSegment(live);
  }
  setChatBrand(live, {
    state: brandState(event),
    status: event?.type === "turn.failed" ? TURN_FAILED_STATUS : undefined,
  });
  const starts = event?.type === "tool.started" || event?.type === "skill.started";
  const finishes = event?.type === "tool.finished" || event?.type === "skill.finished";
  const callKey = event?.call_id || `${event?.type}:${event?.name || "tool"}`;
  if (starts) {
    live.round.active.set(callKey, event.name || "tool");
  } else if (finishes) {
    const name = live.round.active.get(callKey) || event.name || "tool";
    live.round.active.delete(callKey);
    live.round.completed.push(name);
  }
  if (event?.type === "turn.failed" || event?.type === "turn.cancelled") {
    // The turn is over, so nothing is still running: settle the row instead
    // of leaving it claiming a call is in flight.
    live.round.completed.push(...live.round.active.values());
    live.round.active.clear();
  }
  if (
    event?.type === "turn.completed"
    || event?.type === "turn.failed"
    || event?.type === "turn.cancelled"
  ) {
    for (const note of live.notes) note.settle();
  }
  syncRoundUsage(live);
}
