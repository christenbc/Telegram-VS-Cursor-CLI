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

When citing a task that exists as a note, link its title with obsid.net.
**Percent-encode the entire `file` value** (`/` → `%2F`, spaces → `%20`). Unencoded spaces break Telegram hyperlinks.

```
[task title]({OBSID_NET_BASE}/?vault={OBSIDIAN_VAULT_NAME}&file=TaskNotes%2FTasks%2FPrepare%20slides%20for%20client%20meeting)
```

- `vault`: `{OBSIDIAN_VAULT_NAME}`
- `file`: path relative to the vault root under `{OBSIDIAN_TASKS_PREFIX}`, without `.md`, fully URL-encoded

Example for `Comprar leche.md`:

```
[Comprar leche]({OBSID_NET_BASE}/?vault={OBSIDIAN_VAULT_NAME}&file=TaskNotes%2FTasks%2FComprar%20leche)
```

(The bot re-encodes obsid.net links before sending, but prefer emitting encoded URLs.)

## Morning summary

Group tasks **by TaskNotes `contexts`** (frontmatter), not in one flat list.

### Which tasks to include

**Only list tasks that have at least one of `scheduled` or `due` set** in frontmatter (non-empty).  
If both are missing or empty, **omit the task entirely** — do not show it in any section.

Among dated tasks, include:

- **Hoy** — `scheduled` or `due` falls on today (Madrid time)
- **Pendientes** — `status` `open` or `in-progress`, with `scheduled` or `due` **before today** (overdue; Madrid time)

Do not list open tasks that only have future dates (after today) unless they also qualify for **Hoy**.

### Discover contexts

1. Read task notes under `{OBSIDIAN_TASKS_PREFIX}/`.
2. Apply the inclusion filter above, then collect every distinct `contexts` value on those tasks.
3. Do not assume a fixed list — use whatever contexts exist in the vault (e.g. `habits`, `hibika`, `office`, `home`).

### Section layout

Create **`## General` first** for tasks with no `contexts` (or an empty list), then **one section per context value**:

| Context value | Section heading (Spanish if the user writes in Spanish) |
|---------------|-----------------------------------------------------------|
| _(empty / missing)_ | `## General` |
| `habits` | `## Hábitos` |
| `hibika` | `## Hibika` |
| any other slug | `##` + humanized name (`deep-work` → `Deep work`, `office` → `Office`) |

**Section order:** `General` first, then `habits` (if present), then other contexts alphabetically by slug.

**Tasks with multiple contexts** — list the task under **each** matching section.

### Within each context section

1. **Hoy** — dated tasks for today (see inclusion filter above)
2. **Pendientes** — overdue dated tasks (`open` / `in-progress`), grouped by priority

Skip a subsection if it would be empty for that context.

Use the priority group labels below inside **Pendientes** (not as global top-level sections).

Be concise but complete. Use headings and lists.
Leave a blank line before each heading or priority-group label.

## Presentation and emojis

**Allowed**

- **Priority group labels** — stable icons by priority, always the same mapping:
  - 🔴 high · 🟡 normal · 🟢 low
  - Example: `📚 🟡 Prioridad normal — atrasadas`
- **One semantic emoji per task** — choose a single emoji that fits the task’s meaning (title/body/tags/contexts), placed before the linked title.
  - Examples: 🦷 dentist, 🛒 groceries, 💻 coding, 📞 phone call, 📊 slides/report
  - Prefer the same emoji for the same task if it appears again in the reply
- **Subtasks** — nest directly under the parent task as indented bullet items (`  - …`). Each subtask line: checkbox emoji, then one semantic emoji, then the text. Keep checked/unchecked from the note: `✅` for done, `⬜` for open (use `⬜`, not `☐` — `☐` often fails to render in Telegram). Do **not** use Markdown task-list syntax (`- [ ]` / `- [x]`): that drops the bullet in Telegram. Example:

```
- 💻 [Draft project proposal]({OBSID_NET_BASE}/?vault={OBSIDIAN_VAULT_NAME}&file=TaskNotes%2FTasks%2FDraft%20project%20proposal)
  - ✅ 📋 Gather requirements from kickoff call
  - ⬜ ✍️ Write scope and deliverables section
  - ⬜ 📅 Add timeline and budget estimate
```

Do not dump all subtasks in a separate `## Subtareas` section.

**Not allowed**

- Do **not** invent metadata icons for frontmatter fields (`🔄` recurrence, `⏰` scheduled/due, status badges, etc.)
- Do **not** invent ad-hoc emoji legends that change between replies
- Do not stack many emojis on a parent-task line; at most one semantic emoji before the linked title. On subtask lines, checkbox + one semantic emoji is fine.
