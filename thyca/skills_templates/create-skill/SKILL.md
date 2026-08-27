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
2. `write ~/.thyca/skills/<name>/SKILL.md` (folder name = skill name).
3. Verify on the next turn that the index shows the new skill without an error tag.

## Description: weak vs strong

Weak — no trigger context: `About PDF handling.`
Strong — what + when + keywords: `Extract text and tables from PDF files and fill
forms. Use when the user mentions PDFs, forms, or document extraction.`