// History-replay skill classification for trace scores.
//
// The server classifies skill loads when building the trace payload
// (thyca/trace_api.py:trace_tool_call, reusing classify_skill_read): a call
// that was a skill load arrives as {id, name, skill: "<name>"} — never with
// the path. Here we only read that marker, so the browser cannot and does
// not re-derive anything from filesystem paths.
//
// Accepted gaps (inherent to replay): a path that only resolves (symlink)
// into the skills dir replays as tool.* while live emitted skill.*, and a
// lexical .. after skills/<name>/ can slip past server-side containment was
// rejected live. Staff notes are identical either way (same densities), so
// the mismatch is metadata-only and never breaks the 4/4 grammar.

// Skill name from a wire tool call, or null when it was not a skill load.
export function skillNameForRead(call) {
  if (!call || typeof call.skill !== "string") return null;
  return call.skill;
}
