# Telegram VS Cursor CLI

Telegram bot that forwards messages to the Cursor Agent CLI and streams responses back in real time using Telegram's draft API.

## Requirements

- Python 3.12+
- [Cursor Agent CLI](https://cursor.com) (`agent` on PATH)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.sh.example env.sh
# Edit env.sh with your token, chat ID, and vault path
```

## Environment variables

Shared config lives in `env.sh` (copy from `env.sh.example`). Both the bot and the daily summary cron source this file.

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | yes | Your Telegram user/chat ID (bot only replies to this chat) |
| `VAULT_PATH` | no | Workspace path passed to `agent --workspace` (default: `/home/ubuntu/vaults/test`) |
| `OBSIDIAN_VAULT_NAME` | no | Obsidian vault name for task links (default: `Testing`) |
| `OBSIDIAN_TASKS_PREFIX` | no | Folder inside the vault where task notes live (default: `TaskNotes/Tasks`) |
| `OBSID_NET_BASE` | no | Obsidian link redirector base URL (default: `https://obsid.net`) |
| `CURSOR_AGENT_PATH` | no | Path to the `agent` binary (default: `/home/ubuntu/.local/bin/agent`) |
| `AGENT_TIMEOUT` | no | Agent timeout in seconds (default: `120`) |

## Run

```bash
source env.sh
python telegram_cursor_bot.py
```

## Conversation context (stateless bot)

The bot does **not** store Telegram chat history. Each incoming message starts a **new** Cursor Agent process with only that message as the prompt (plus a static Telegram formatting hint). The bot does not pass `--continue` or `--resume` to the CLI.

```
Telegram message  →  telegram_cursor_bot.py  →  agent -p "<your text>"  →  response
                         (no history)              (new process every time)
```

This means short follow-ups like *"yes, set it to high priority"* are **not** resolved by the bot remembering the previous turn.

### Why follow-ups can still work

The agent has full tool access (read files, shell, grep, etc.) over `VAULT_PATH`. When a follow-up is ambiguous, it may reconstruct context by inspecting the environment:

1. **Vault artifacts** — e.g. a task note created seconds ago with a recent `dateCreated` / `dateModified`.
2. **Agent session transcripts** — Cursor stores CLI session logs under `~/.cursor/projects/<workspace>/agent-transcripts/`. The agent can list and read recent sessions to connect a reply like *"sí, ponle prioridad alta"* to a prior turn that asked *"¿Quieres que le ponga prioridad…?"*.
3. **Prompt wording** — replies such as *"sí"* only make sense when tied to a specific yes/no question in the previous session.

Example (two separate agent runs, two minutes apart):

| Time | User message | What happened |
|------|--------------|---------------|
| 18:50 | *"create a task to buy dog diapers in 3 days"* | Agent creates `TaskNotes/Tasks/Comprar pañales para la perra.md` and asks whether to change priority. |
| 18:52 | *"sí, ponle prioridad alta"* | New agent run; bot sends only this text. Agent finds the new task file and reads the previous session transcript, then updates `priority: high`. |

This is **inference**, not guaranteed conversation memory.

### Limitations

Follow-up resolution is **best-effort and fragile**. It may fail or pick the wrong target when:

- Several tasks or notes were changed recently.
- The follow-up does not reference something unambiguous (no clear *"sí"* / *"that one"* / task name).
- Transcripts or vault files are unavailable.

For reliable multi-turn behavior, the bot would need explicit session handling (e.g. prepend chat history to the prompt, or use `agent --continue` / `--resume <chatId>` with a stable ID per Telegram chat). That is not implemented today.

## TaskNotes skill

TaskNotes domain knowledge (frontmatter, fechas Madrid, enlaces obsid.net, estructura del resumen matutino) lives in [`.cursor/skills/obsidian-tasknotes/SKILL.md`](.cursor/skills/obsidian-tasknotes/SKILL.md).

The bot injects this skill into the agent prompt when:

- **Daily summary** — always (`include_tasknotes_skill=True` in `daily_task_summary.py`)
- **Chat messages** — when the message matches task-related keywords (`tarea`, `pendiente`, `priority`, `scheduled`, etc.)

The skill uses placeholders templated from `env.sh` at load time: `{OBSIDIAN_VAULT_NAME}`, `{OBSIDIAN_TASKS_PREFIX}`, `{OBSID_NET_BASE}`. Edits to the skill file are picked up automatically (cached by file mtime; no bot restart needed).

Telegram formatting rules remain in `build_telegram_format_hint()` and are always appended to every prompt.

## Daily task summary (systemd timer)

`daily_task_summary.py` asks the Cursor Agent for a morning summary of today's and pending tasks from the Obsidian vault, then sends it to your Telegram chat.

Test manually:

```bash
./run_daily_summary.sh
```

Schedule every day at 8:30 Madrid time. Use a systemd timer (not cron): Debian/Ubuntu cron ignores `CRON_TZ` and runs jobs in the system timezone (UTC on most VPS hosts), which would fire at 10:30 in summer if you schedule `30 8 * * *`.

```bash
sudo cp daily-summary.service daily-summary.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now daily-summary.timer
systemctl list-timers daily-summary.timer
```

Logs are written to `logs/daily_summary.log`.

## systemd example (bot)

```ini
[Service]
EnvironmentFile=/home/ubuntu/Telegram-VS-Cursor-CLI/env.sh
ExecStart=/home/ubuntu/Telegram-VS-Cursor-CLI/.venv/bin/python /home/ubuntu/Telegram-VS-Cursor-CLI/telegram_cursor_bot.py
```
