#!/usr/bin/env python3
import asyncio
import locale
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Bot

from telegram_cursor_bot import (
    AGENT_TIMEOUT,
    MY_CHAT_ID,
    TOKEN,
    get_active_vault,
    migrate_legacy_sessions,
    send_agent_prompt_to_telegram,
    send_vault_picker,
)

MADRID_TZ = ZoneInfo("Europe/Madrid")


def build_daily_prompt() -> str:
    now = datetime.now(MADRID_TZ)
    try:
        fecha_madrid = now.strftime("%A %d de %B de %Y").capitalize()
    except ValueError:
        fecha_madrid = now.strftime("%Y-%m-%d")

    return (
        f"Buenos días. Hoy es {fecha_madrid}.\n"
        "Hazme el resumen matutino de mis tareas del vault.\n"
        "Agrupa las tareas en secciones separadas según su campo `contexts` "
        "(hábitos, hibika, etc.): primero General (sin contexto), luego cada contexto.\n"
        "No incluyas tareas sin `due` ni `scheduled`; solo las de hoy y las atrasadas."
    )


async def main() -> int:
    try:
        locale.setlocale(locale.LC_TIME, "es_ES.UTF-8")
    except locale.Error:
        pass

    migrate_legacy_sessions()

    vault = get_active_vault(MY_CHAT_ID)
    if vault is None:
        try:
            bot = Bot(TOKEN)
            async with bot:
                await send_vault_picker(
                    bot,
                    MY_CHAT_ID,
                    "No hay vault elegido. Elige uno para recibir el resumen diario:",
                )
        except Exception as exc:
            print(f"Error enviando recordatorio de vault: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        await send_agent_prompt_to_telegram(
            build_daily_prompt(),
            timeout=AGENT_TIMEOUT,
            include_tasknotes_skill=True,
            workspace_path=vault.path,
            obsidian_vault_name=vault.obsidian_name,
        )
    except Exception as exc:
        print(f"Error enviando resumen diario: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
