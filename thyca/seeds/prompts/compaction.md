Write a continuation summary of the conversation below, in the conversation's own language, so the next phase can start from your summary alone. This replaces the older turns at a task boundary: keep what future work needs, drop exploration detail, dead ends, and small talk that no longer matters.

Structure the summary with only the headings that have content, in this order:
- Goal: what the user ultimately wants.
- Done: what was accomplished, with decisions made and why.
- Current state: where the work stands right now, including anything half-finished.
- Files: paths involved and what changed in each (paths only, never file contents or tool output bodies).
- Errors & fixes: failures hit and how they were resolved, so they are not repeated.
- Constraints: explicit user requests, preferences, and limits still in force.
- Next: the immediate next step or focus, most important first.

Rules: if a prior summary is included, merge it into this one coherent summary instead of stacking or repeating it. Be specific and factual; do not invent details, file names, or outcomes. If the user gave a focus for what comes next, put it first under Next. Stay under roughly 3500 characters. Return only the summary: no preamble, no explanation, no code fences.
