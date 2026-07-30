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

Shared config lives in `env.sh` (copy from `env.sh.example`). The bot and the daily systemd jobs (`daily_task_summary.py`, `daily_completed_to_journal.py`) source this file.

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | yes | Your Telegram user/chat ID (bot only replies to this chat) |
| `VAULT_PATH` | no | Legacy default for internal function signatures (default: `/home/ubuntu/vaults/test`); not used by chat or daily summary |
| `OBSIDIAN_VAULT_NAME` | no | Legacy default Obsidian name for internal signatures (default: `Testing`); not used by chat or daily summary |
| `VAULTS_ROOT` | no | Root folder scanned for vault subdirectories used by `/vault` (default: `/home/ubuntu/vaults`) |
| `OBSIDIAN_TASKS_PREFIX` | no | Folder inside the vault where task notes live, shared across all vaults (default: `TaskNotes/Tasks`) |
| `OBSID_NET_BASE` | no | Obsidian link redirector base URL (default: `https://obsid.net`) |
| `CURSOR_AGENT_PATH` | no | Path to the `agent` binary (default: `/home/ubuntu/.local/bin/agent`) |
| `AGENT_TIMEOUT` | no | Agent timeout in seconds (default: `120`) |
| `SESSION_IDLE_TIMEOUT` | no | Seconds of inactivity after which a chat's session is no longer resumed and a fresh one starts (default: `2700`, 45 min) |
| `GROQ_API_KEY` | yes | Groq API key used to transcribe voice notes (get one at [console.groq.com/keys](https://console.groq.com/keys)) |

## Run

```bash
source env.sh
python telegram_cursor_bot.py
```

## Vaults (`/vault`)

The bot can work with multiple Obsidian vaults. Every subdirectory of `VAULTS_ROOT` (default `/home/ubuntu/vaults`) is auto-discovered as a vault; the folder name is its alias (e.g. `test`, `personal`). If a vault folder has a `.obsidian/app.json` with a `vaultName` field, that's used as the display/link name — otherwise the alias is capitalized (`test` → `Test`).

**A vault must be chosen explicitly before the bot will talk to the agent.** There is no silent fallback to `env.sh`'s `VAULT_PATH`/`OBSIDIAN_VAULT_NAME`. If you send a text or voice message before picking a vault, the bot replies with a picker instead of calling the agent:

```
Tú:   ¿qué tareas tengo pendientes?
Bot:  Elige un vault para continuar:
      [test]  [personal]  [trabajo]

Tú:   (tap en personal)
Bot:  Vault activo: personal. Ya puedes escribirme.
```

Use `/vault` any time to see the active vault and switch:

```
Tú:   /vault
Bot:  Vault activo: personal
      [test]  [✓ personal]  [trabajo]
```

Switching vaults always resets the conversation (`clear_session`), since the agent's context is tied to a specific workspace. The vault choice is persisted per chat in `chat_state.json` (git-ignored); the file is auto-migrated from the older `sessions.json` format the first time the bot starts after upgrading (existing sessions keep working, but you'll still need to pick a vault once).

## Conversation context (per-chat sessions)

The bot keeps a lightweight session per Telegram chat so the agent can ask a clarifying question and pick up the conversation when you reply, instead of starting from scratch on every message.

```
Message 1  →  agent -p "<text>"                    →  response + session_id S1 (saved to chat_state.json)
Message 2  →  agent -p --resume S1 "<text>"        →  response (full context of turn 1, incl. any question it asked)
```

How it works:

1. Every response from `agent -p --output-format stream-json` carries a `session_id`. The bot captures it and persists a `{chat_id: {vault_alias, session_id, updated_at}}` map in `chat_state.json` (git-ignored) next to the bot.
2. On the next message from the same chat, if the stored session is younger than `SESSION_IDLE_TIMEOUT` (default 45 min), the bot calls the agent with `--resume <session_id>` so it remembers everything from the previous turn — including a question it asked and is waiting on.
3. If the idle timeout has passed, the bot starts a brand-new session automatically (no stale context leaks into an unrelated message).
4. The system prompt (`build_telegram_format_hint()`) explicitly tells the agent to ask a clarifying question instead of guessing when a request is ambiguous or would trigger an irreversible action, and reminds it that the conversation has memory across Telegram messages.
5. Send `/new` at any time to drop the current session and start a fresh, context-free conversation on your next message.

Example:

| Time | User message | What happened |
|------|--------------|---------------|
| 18:50 | *"create a task to buy dog diapers in 3 days"* | Agent creates the task, but asks whether to set it to high priority. Session `S1` is saved. |
| 18:52 | *"sí, ponle prioridad alta"* | Bot resumes `S1`; the agent remembers its own question and updates `priority: high` on the same note. |

### Limitations

- A bot restart does not lose an active session (it's persisted to `chat_state.json`), but if the process is killed mid-run before any event with a `session_id` was read, that turn's context isn't saved.
- Only one session per `chat_id` is tracked; since the bot only replies to `TELEGRAM_CHAT_ID`, there is effectively a single ongoing conversation at a time.
- `daily_task_summary.py` is a scheduled one-shot job (not a conversation) and does not resume chat sessions.

## Transcripción de voz (Groq Whisper)

Las notas de voz de Telegram se transcriben automáticamente a texto usando el modelo `whisper-large-v3` de [Groq](https://console.groq.com/docs/model/whisper-large-v3) y se procesan como si fueran un mensaje de texto normal:

```
Nota de voz  →  descarga (.oga)  →  Groq whisper-large-v3  →  texto  →  agente de Cursor  →  respuesta
```

La transcripción se procesa en silencio (no se muestra al usuario); solo se ve la respuesta final del agente. Si la transcripción falla o el audio no contiene texto detectable, el bot responde con un mensaje de error en vez de invocar al agente.

Requiere la variable `GROQ_API_KEY` (ver tabla de variables de entorno). El límite de Groq es de 25 MB por archivo de audio, muy por encima de lo que ocupa una nota de voz típica de Telegram.

Por ahora solo se procesan mensajes de tipo nota de voz (`voice`); los archivos de audio adjuntos (mp3, m4a, etc.) no se transcriben todavía.

## TaskNotes skill

TaskNotes domain knowledge (frontmatter, fechas Madrid, enlaces obsid.net, estructura del resumen matutino) lives in [`.cursor/skills/obsidian-tasknotes/SKILL.md`](.cursor/skills/obsidian-tasknotes/SKILL.md).

The bot injects this skill into the agent prompt when:

- **Daily summary** — always (`include_tasknotes_skill=True` in `daily_task_summary.py`)
- **Chat messages** — when the message matches task-related keywords (`tarea`, `pendiente`, `priority`, `scheduled`, etc.)

The skill is templated with `{OBSIDIAN_VAULT_NAME}` (the active chat's vault), `{OBSIDIAN_TASKS_PREFIX}` and `{OBSID_NET_BASE}` (both global, from `env.sh`). Edits to the skill file are picked up automatically (cached by file mtime + vault name; no bot restart needed).

Telegram formatting rules remain in `build_telegram_format_hint()` and are always appended to every prompt.

## Daily task summary (systemd timer)

`daily_task_summary.py` asks the Cursor Agent for a morning summary of today's and pending tasks from the **vault you have selected in Telegram** (`vault_alias` in `chat_state.json` for `TELEGRAM_CHAT_ID`), then sends it to your chat.

If no vault is selected yet, the job sends a Telegram reminder with the inline vault picker instead of calling the agent. Changing vault in Telegram affects the next scheduled summary automatically — no service restart needed.

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

## Daily completed → journal (systemd timer)

`daily_completed_to_journal.py` scans TaskNotes in the **active Telegram vault**, finds tasks completed the previous calendar day (Europe/Madrid), and appends them to that day's journal note:

- Habit tasks (`contexts` includes `habits`): `- [x] [[task title]]`
- Other tasks: `[[task title]]`

```markdown
- [x] [[Pon la lavadora 🧼]]
[[emviar paquete con leash]]
```

A task counts as completed yesterday if:

- `status: done` and `completedDate: YYYY-MM-DD` matches yesterday, or
- yesterday appears under `complete_instances` (recurring habits)

The journal file is discovered by filename only (`YYYY-MM-DD.md` anywhere under the vault, excluding `.obsidian`), so vaults need not share the same folder layout. If several matches exist, the shallowest path wins. If none exist and there is something to dump, the note is created at the vault root. Re-runs are idempotent (existing lines in the correct form are left alone; legacy/plain or wrong-format lines for the same task are upgraded in place).

The job always notifies Telegram with what was dumped (or that nothing was new). If no vault is selected, it sends the vault picker instead.

Test manually:

```bash
./run_daily_completed_journal.sh
```

Schedule every day at **08:00** Madrid time (before the 08:30 morning summary):

```bash
sudo cp daily-completed-journal.service daily-completed-journal.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now daily-completed-journal.timer
systemctl list-timers daily-completed-journal.timer
```

Logs are written to `logs/daily_completed_journal.log`.

## systemd example (bot)

```ini
[Service]
EnvironmentFile=/home/ubuntu/Telegram-VS-Cursor-CLI/env.sh
ExecStart=/home/ubuntu/Telegram-VS-Cursor-CLI/.venv/bin/python /home/ubuntu/Telegram-VS-Cursor-CLI/telegram_cursor_bot.py
```
