---
name: create-skill
description: Author or update an Agent Skills SKILL.md that validates against the agentskills.io spec. Use when persisting a reusable procedure, workflow, or domain guide so it can be dispatched in later sessions.
---

# Author a skill

A skill is a folder `~/.thyca/skills/<name>/` containing `SKILL.md` (required) plus
optional `scripts/`, `references/`, `assets/`. Create or update it with `write`/`edit`.
It appears in the `<skills>` index on the next turn.

## Frontmatter (required)

```yaml
---
name: my-skill-name
description: What the skill does and when to use it, with trigger keywords. Max 1024 chars.
---
```

Rules (validated on every scan — a broken skill shows up as
`- <name> (SKILL.md invalid: <reason>)` in the index):

- `name`: 1–64 chars, lowercase letters/digits/hyphens only, no leading, trailing, or
  consecutive hyphens. **Must equal the folder name.**
- `description`: 1–1024 chars, non-empty. Say **what it does AND when to use it**, and
  include the keywords a matching task would contain — this line is all the model sees
  when deciding to load the skill.
- Unknown frontmatter fields are ignored.
- Body: markdown instructions, under ~500 tokens when possible. Move detail into
  `references/*.md` and reference them by relative path; put runnable code in
  `scripts/` (the agent runs it with bash) and templates/data in `assets/`.

## Steps

1. Check `<skills>` — if an existing skill already covers the task, edit it instead.
2. Write your private copy at `~/.thyca/bots/<your-bot-id>/skills/<name>/SKILL.md` (folder name = skill name).
3. Verify on the next turn that the index shows the new skill without an error tag.

## Sharing (team skills)

- Team skills live in `~/.thyca/skills-team/<name>/`. New and existing team files stay hidden from other bots until Thyca shares them with `bot_skill_manage` (list/share/unshare). Sharing is name-based and persists across restarts; edits preserve the share flag.
- Child bots may read their own private skills and shared team skills, and edit their own private skills. They may not edit shared skills or open other bots' private skills. Thyca may edit shared skills, and the share flag stays on.
- Limits (by design): sharing controls the skill index and recognized built-in file-tool paths. Bash and MCP remain unchanged and can bypass these permissions. Outside hardlink aliases can also bypass path-based checks.

## Description: weak vs strong

Weak — no trigger context: `About PDF handling.`
Strong — what + when + keywords: `Extract text and tables from PDF files and fill
forms. Use when the user mentions PDFs, forms, or document extraction.`