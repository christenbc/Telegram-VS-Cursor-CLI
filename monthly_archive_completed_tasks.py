#!/usr/bin/env python3
"""Archive completed TaskNotes older than one calendar month."""

from __future__ import annotations

import asyncio
import calendar
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Bot

from telegram_cursor_bot import (
    MY_CHAT_ID,
    OBSIDIAN_TASKS_PREFIX,
    TOKEN,
    get_active_vault,
    migrate_legacy_sessions,
    send_vault_picker,
)

MADRID_TZ = ZoneInfo("Europe/Madrid")
HABIT_CONTEXT = "habits"
ARCHIVED_TAG = "archived"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
STATUS_RE = re.compile(r"^status:\s*(.+)$", re.MULTILINE)
COMPLETED_DATE_RE = re.compile(r"^completedDate:\s*(.+)$", re.MULTILINE)
LIST_ITEM_RE = re.compile(r"^\s+-\s+(.+)$")
TAGS_HEADER_RE = re.compile(r"^tags:\s*(.*)$")


@dataclass(frozen=True)
class TaskCandidate:
    title: str
    source_path: Path
    completed_date: date | None
    reason: str = ""


@dataclass
class ArchiveResult:
    cutoff: date
    archived: list[TaskCandidate] = field(default_factory=list)
    skipped: list[TaskCandidate] = field(default_factory=list)
    conflicts: list[TaskCandidate] = field(default_factory=list)


def archive_prefix_from_tasks_prefix(tasks_prefix: str) -> str:
    if tasks_prefix.endswith("/Tasks"):
        return tasks_prefix[: -len("/Tasks")] + "/Archive"
    return "TaskNotes/Archive"


def subtract_one_month(day: date) -> date:
    if day.month == 1:
        year, month = day.year - 1, 12
    else:
        year, month = day.year, day.month - 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def archive_cutoff_madrid(now: datetime | None = None) -> date:
    current = (now or datetime.now(MADRID_TZ)).astimezone(MADRID_TZ).date()
    return subtract_one_month(current.replace(day=1))


def extract_frontmatter(text: str) -> str | None:
    match = FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def parse_yaml_string_list(frontmatter: str, key: str) -> list[str]:
    header_re = re.compile(rf"^{re.escape(key)}:\s*(.*)$")
    values: list[str] = []
    in_list = False
    for line in frontmatter.splitlines():
        header = header_re.match(line)
        if header:
            inline = header.group(1).strip()
            in_list = True
            if inline in ("", "[]"):
                continue
            if inline.startswith("[") and inline.endswith("]"):
                inner = inline[1:-1].strip()
                if inner:
                    values.extend(part.strip().strip("'\"") for part in inner.split(","))
                in_list = False
            elif inline:
                values.append(inline.strip("'\""))
                in_list = False
            continue
        if in_list:
            item = LIST_ITEM_RE.match(line)
            if item:
                values.append(item.group(1).strip().strip("'\""))
                continue
            if line.strip() == "" or line.startswith(" ") or line.startswith("\t"):
                continue
            in_list = False
    return values


def is_habit_task(frontmatter: str) -> bool:
    contexts = {value.casefold() for value in parse_yaml_string_list(frontmatter, "contexts")}
    return HABIT_CONTEXT.casefold() in contexts


def parse_completed_date(frontmatter: str) -> date | None:
    match = COMPLETED_DATE_RE.search(frontmatter)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def should_archive(frontmatter: str, cutoff: date) -> tuple[bool, str, date | None]:
    if is_habit_task(frontmatter):
        return False, "hábito recurrente", None

    status = STATUS_RE.search(frontmatter)
    if not status or status.group(1).strip() != "done":
        return False, "status no es done", None

    completed = parse_completed_date(frontmatter)
    if completed is None:
        return False, "sin completedDate válido", None

    if completed >= cutoff:
        return False, "dentro del periodo de retención", completed

    return True, "", completed


def add_archived_tag_to_frontmatter(frontmatter: str) -> str:
    tags = parse_yaml_string_list(frontmatter, "tags")
    if ARCHIVED_TAG in tags:
        return frontmatter

    lines = frontmatter.splitlines()
    result: list[str] = []
    i = 0
    inserted = False

    while i < len(lines):
        line = lines[i]
        header = TAGS_HEADER_RE.match(line)
        if header:
            inline = header.group(1).strip()
            result.append(line)
            if inline and inline not in ("", "[]"):
                if inline.startswith("[") and inline.endswith("]"):
                    inner = inline[1:-1].strip()
                    parts = [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
                    parts.append(ARCHIVED_TAG)
                    result[-1] = f"tags: [{', '.join(parts)}]"
                else:
                    result[-1] = "tags:"
                    result.append(f"  - {inline.strip('\"\'')}")
                    result.append(f"  - {ARCHIVED_TAG}")
                inserted = True
                i += 1
                continue

            i += 1
            while i < len(lines) and LIST_ITEM_RE.match(lines[i]):
                result.append(lines[i])
                i += 1
            result.append(f"  - {ARCHIVED_TAG}")
            inserted = True
            continue

        result.append(line)
        i += 1

    if not inserted:
        if result and result[-1].strip():
            result.append("")
        result.extend(["tags:", f"  - {ARCHIVED_TAG}"])

    return "\n".join(result)


def apply_archived_tag(text: str) -> str:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return text
    frontmatter = match.group(1)
    body = text[match.end() :]
    updated = add_archived_tag_to_frontmatter(frontmatter)
    return f"---\n{updated}\n---{body}"


def archive_task_file(source: Path, archive_dir: Path) -> str | None:
    """Move task to archive with archived tag. Return error message or None on success."""
    dest = archive_dir / source.name
    if dest.exists():
        return "ya existe en Archive"

    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        return f"no se pudo leer: {exc}"

    updated = apply_archived_tag(text)
    archive_dir.mkdir(parents=True, exist_ok=True)

    try:
        dest.write_text(updated, encoding="utf-8")
        source.unlink()
    except OSError as exc:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return f"error al mover: {exc}"

    return None


def scan_and_archive(vault_path: str, cutoff: date | None = None) -> ArchiveResult:
    vault_root = Path(vault_path)
    tasks_dir = vault_root / OBSIDIAN_TASKS_PREFIX
    archive_dir = vault_root / archive_prefix_from_tasks_prefix(OBSIDIAN_TASKS_PREFIX)
    effective_cutoff = cutoff or archive_cutoff_madrid()

    result = ArchiveResult(cutoff=effective_cutoff)
    if not tasks_dir.is_dir():
        return result

    for path in sorted(tasks_dir.glob("*.md")):
        title = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            result.skipped.append(
                TaskCandidate(title=title, source_path=path, completed_date=None, reason=f"no legible: {exc}")
            )
            continue

        frontmatter = extract_frontmatter(text)
        if not frontmatter:
            result.skipped.append(
                TaskCandidate(title=title, source_path=path, completed_date=None, reason="sin frontmatter")
            )
            continue

        eligible, reason, completed = should_archive(frontmatter, effective_cutoff)
        candidate = TaskCandidate(title=title, source_path=path, completed_date=completed, reason=reason)

        if not eligible:
            if reason == "sin completedDate válido":
                result.skipped.append(candidate)
            continue

        error = archive_task_file(path, archive_dir)
        if error:
            result.conflicts.append(
                TaskCandidate(title=title, source_path=path, completed_date=completed, reason=error)
            )
        else:
            result.archived.append(candidate)

    return result


def build_telegram_message(vault_alias: str, result: ArchiveResult) -> str:
    lines = [
        "Archivado mensual de tareas completadas",
        f"Vault: {vault_alias}",
        f"Corte: completedDate anterior a {result.cutoff.isoformat()}",
    ]

    if result.archived:
        lines.append("")
        lines.append(f"Archivadas ({len(result.archived)}):")
        lines.extend(f"- [[{task.title}]]" for task in result.archived)

    if result.skipped:
        lines.append("")
        lines.append(f"Omitidas ({len(result.skipped)}):")
        lines.extend(f"- {task.title} ({task.reason})" for task in result.skipped)

    if result.conflicts:
        lines.append("")
        lines.append(f"Conflictos ({len(result.conflicts)}):")
        lines.extend(f"- {task.title} ({task.reason})" for task in result.conflicts)

    if not result.archived and not result.skipped and not result.conflicts:
        lines.append("")
        lines.append("Ninguna tarea pendiente de archivar.")
    elif not result.archived:
        lines.append("")
        lines.append("No se archivó ninguna tarea nueva.")

    return "\n".join(lines)


async def main() -> int:
    migrate_legacy_sessions()

    vault = get_active_vault(MY_CHAT_ID)
    if vault is None:
        try:
            bot = Bot(TOKEN)
            async with bot:
                await send_vault_picker(
                    bot,
                    MY_CHAT_ID,
                    "No hay vault elegido. Elige uno para el archivado mensual de tareas:",
                )
        except Exception as exc:
            print(f"Error enviando recordatorio de vault: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        result = scan_and_archive(vault.path)
    except Exception as exc:
        print(f"Error archivando tareas: {exc}", file=sys.stderr)
        try:
            bot = Bot(TOKEN)
            async with bot:
                await bot.send_message(
                    chat_id=MY_CHAT_ID,
                    text=f"Error en el archivado mensual de tareas: {exc}",
                )
        except Exception as notify_exc:
            print(f"Error notificando fallo por Telegram: {notify_exc}", file=sys.stderr)
        return 1

    message = build_telegram_message(vault.alias, result)
    print(message)

    try:
        bot = Bot(TOKEN)
        async with bot:
            await bot.send_message(chat_id=MY_CHAT_ID, text=message)
    except Exception as exc:
        print(f"Error enviando notificación Telegram: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
