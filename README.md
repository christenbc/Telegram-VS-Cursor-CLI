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
```

## Environment variables

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
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export VAULT_PATH="/path/to/your/vault"
python telegram_cursor_bot.py
```

## systemd example

```ini
[Service]
Environment=TELEGRAM_BOT_TOKEN=...
Environment=TELEGRAM_CHAT_ID=...
Environment=VAULT_PATH=/path/to/vault
ExecStart=/home/ubuntu/Telegram-VS-Cursor-CLI/.venv/bin/python /home/ubuntu/Telegram-VS-Cursor-CLI/telegram_cursor_bot.py
```
