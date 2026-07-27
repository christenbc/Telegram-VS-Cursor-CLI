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
