---
name: obsidian-tasknotes
description: Query and summarize TaskNotes tasks in an Obsidian vault. Use when the user asks about tasks, pending items, morning summaries, or TaskNotes.
---

# Obsidian TaskNotes

## Language

Respond in the **same language as the user's message** (e.g. Spanish if they write in Spanish, English if they write in English). These instructions are in English regardless.

## When to apply

Use these instructions when the user asks for:

- Morning summary or task listing
- Today's tasks, pending, overdue, or by priority
- Creating, editing, or querying TaskNotes

## Task location

Task notes live in `{OBSIDIAN_TASKS_PREFIX}/` inside the vault.

To list tasks, read the `*.md` files in that folder. Do not assume a fixed list — explore the directory on every query.

## TaskNotes frontmatter

Each task is a Markdown note with YAML at the top. Common fields:

| Field | Purpose |
|-------|---------|
| `status` | `open`, `in-progress`, `done`, etc. |
| `priority` | `low`, `normal`, `high`, etc. |
| `due` | Deadline (ISO 8601) |
| `scheduled` | Planned date/time (ISO 8601) |
| `dateCreated` / `dateModified` | Audit timestamps |
| `tags` | Labels (e.g. `task`) |
| `contexts` | Contexts (e.g. `office`) |

The note body contains the description and subtasks in Markdown.

## Dates and timezone

Interpret "today", "tomorrow", and date comparisons in **Europe/Madrid**.

- A task is "for today" if `scheduled` or `due` falls on the current day (Madrid time).
- A task is overdue if `due` or `scheduled` is before now (Madrid time) and `status` is not `done`.

## Links in Telegram responses

When citing a task that exists as a note, link its title with obsid.net:

```
[task title]({OBSID_NET_BASE}/?vault={OBSIDIAN_VAULT_NAME}&file=relative/path/without/md)
```

- `vault`: `{OBSIDIAN_VAULT_NAME}`
- `file`: path relative to the vault root, without `.md` extension, forward slashes `/`

Example for a task at `{OBSIDIAN_TASKS_PREFIX}/Comprar leche.md`:

```
[Comprar leche]({OBSID_NET_BASE}/?vault={OBSIDIAN_VAULT_NAME}&file={OBSIDIAN_TASKS_PREFIX}/Comprar leche)
```

## Morning summary

Recommended structure:

1. **Today's tasks** — `scheduled` or `due` today (Madrid time)
2. **Pending** — `status` `open` or `in-progress`, grouped by priority and overdue status
3. **Subtasks** — mention any found in each note's body

Be concise but complete. Use headings and lists.
