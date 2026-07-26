#!/usr/bin/env python3
import asyncio
import locale
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram_cursor_bot import AGENT_TIMEOUT, send_agent_prompt_to_telegram

MADRID_TZ = ZoneInfo("Europe/Madrid")


def build_daily_prompt() -> str:
    now = datetime.now(MADRID_TZ)
    try:
        fecha_madrid = now.strftime("%A %d de %B de %Y").capitalize()
    except ValueError:
        fecha_madrid = now.strftime("%Y-%m-%d")

    return f"""Buenos días. Hoy es {fecha_madrid}.

Hazme un resumen matutino de mis tareas del vault:
1. Tareas de hoy (scheduled o due hoy, hora Madrid)
2. Tareas pendientes (status open o in-progress), agrupadas por prioridad y atraso
3. Menciona subtareas si existen

Sé conciso pero completo. Usa encabezados y listas."""


async def main() -> int:
    try:
        locale.setlocale(locale.LC_TIME, "es_ES.UTF-8")
    except locale.Error:
        pass

    try:
        await send_agent_prompt_to_telegram(
            build_daily_prompt(),
            timeout=AGENT_TIMEOUT,
        )
    except Exception as exc:
        print(f"Error enviando resumen diario: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
