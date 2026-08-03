#!/usr/bin/env python3
"""Dump yesterday's completed TaskNotes into the matching YYYY-MM-DD journal note."""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
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

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
STATUS_RE = re.compile(r"^status:\s*(.+)$", re.MULTILINE)
COMPLETED_DATE_RE = re.compile(r"^completedDate:\s*(.+)$", re.MULTILINE)
LIST_ITEM_RE = re.compile(r"^\s+-\s+(.+)$")
# Habit form: `- [x] [[title]]` or legacy `- [x] title`
CHECKBOX_LINE_RE = re.compile(r"^(-\s*\[x\]\s+)(.+)$", re.IGNORECASE | re.MULTILINE)
# Non-habit form: a line that is only a wikilink
BARE_WIKILINK_LINE_RE = re.compile(r"^(\[\[.+\]\])\s*$", re.MULTILINE)
WIKILINK_RE = re.compile(r"^\[\[(.+)\]\]$")
DAILY_JOURNAL_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


@dataclass(frozen=True)
class CompletedTask:
    title: str
    source_path: Path
    is_habit: bool

    def journal_line(self) -> str:
        if self.is_habit:
            return f"- [x] [[{self.title}]]"
        return f"[[{self.title}]]"


@dataclass
class DumpResult:
    yesterday: str
    journal_path: Path | None
    journal_created: bool
    journal_candidates: int
    appended: list[CompletedTask]
    already_present: list[CompletedTask]
    completed_found: list[CompletedTask]


def yesterday_madrid(now: datetime | None = None) -> str:
    current = now or datetime.now(MADRID_TZ)
    return (current.astimezone(MADRID_TZ).date() - timedelta(days=1)).isoformat()


def extract_frontmatter(text: str) -> str | None:
    match = FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def parse_yaml_string_list(frontmatter: str, key: str) -> list[str]:
    """Parse a simple YAML list field (`key:` + `- item` lines, or inline `[a, b]`)."""
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
                # Scalar form: `contexts: habits`
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


def parse_complete_instances(frontmatter: str) -> list[str]:
    return parse_yaml_string_list(frontmatter, "complete_instances")


def is_habit_task(frontmatter: str) -> bool:
    contexts = {value.casefold() for value in parse_yaml_string_list(frontmatter, "contexts")}
    return HABIT_CONTEXT.casefold() in contexts


def normalize_entry_title(raw: str) -> str:
    """Strip optional [[...]] wrapping so plain and linked forms match."""
    text = raw.strip()
    match = WIKILINK_RE.match(text)
    return match.group(1).strip() if match else text


def task_completed_on(frontmatter: str, day: str) -> bool:
    completed = COMPLETED_DATE_RE.search(frontmatter)
    if completed and completed.group(1).strip() == day:
        status = STATUS_RE.search(frontmatter)
        if status is None or status.group(1).strip() == "done":
            return True

    return day in parse_complete_instances(frontmatter)


def scan_completed_tasks(tasks_dir: Path, day: str) -> list[CompletedTask]:
    if not tasks_dir.is_dir():
        return []

    found: list[CompletedTask] = []
    for path in sorted(tasks_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Skipping unreadable task {path}: {exc}", file=sys.stderr)
            continue
        frontmatter = extract_frontmatter(text)
        if not frontmatter:
            continue
        if task_completed_on(frontmatter, day):
            found.append(
                CompletedTask(
                    title=path.stem,
                    source_path=path,
                    is_habit=is_habit_task(frontmatter),
                )
            )
    return found


def iter_daily_journal_files(vault_root: Path) -> list[Path]:
    """All YYYY-MM-DD.md files in the vault (excluding .obsidian)."""
    hits: list[Path] = []
    for path in vault_root.rglob("*.md"):
        if not path.is_file():
            continue
        if ".obsidian" in path.parts:
            continue
        if not DAILY_JOURNAL_FILE_RE.match(path.name):
            continue
        hits.append(path)
    return hits


def discover_journal_directory(vault_root: Path) -> Path | None:
    """Directory with the most daily journal companions (YYYY-MM-DD.md)."""
    daily_files = iter_daily_journal_files(vault_root)
    if not daily_files:
        return None

    counts: dict[Path, int] = {}
    for path in daily_files:
        parent = path.parent
        counts[parent] = counts.get(parent, 0) + 1

    def sort_key(directory: Path) -> tuple[int, int, str]:
        try:
            rel = directory.relative_to(vault_root)
        except ValueError:
            rel = directory
        return (-counts[directory], len(rel.parts), str(rel).lower())

    return min(counts.keys(), key=sort_key)


def find_journal_candidates(vault_root: Path, day: str) -> list[Path]:
    target_name = f"{day}.md"
    journal_dir = discover_journal_directory(vault_root)
    hits: list[Path] = []
    for path in vault_root.rglob(target_name):
        if not path.is_file():
            continue
        if ".obsidian" in path.parts:
            continue
        hits.append(path)

    def sort_key(p: Path) -> tuple[int, int, str]:
        in_journal_dir = journal_dir is not None and p.parent == journal_dir
        try:
            rel = p.relative_to(vault_root)
        except ValueError:
            rel = p
        return (0 if in_journal_dir else 1, len(rel.parts), str(rel).lower())

    return sorted(hits, key=sort_key)


def resolve_journal(vault_root: Path, day: str) -> tuple[Path, bool, int]:
    """Return (journal_path, created, candidate_count)."""
    candidates = find_journal_candidates(vault_root, day)
    if candidates:
        return candidates[0], False, len(candidates)

    journal_dir = discover_journal_directory(vault_root)
    if journal_dir is not None:
        created_path = journal_dir / f"{day}.md"
    else:
        created_path = vault_root / f"{day}.md"
    created_path.write_text("", encoding="utf-8")
    return created_path, True, 0


def index_existing_entries(text: str) -> dict[str, tuple[int, int, str]]:
    """Map title -> (start, end, current_line_text) for first matching dump entry."""
    by_title: dict[str, tuple[int, int, str]] = {}

    for match in CHECKBOX_LINE_RE.finditer(text):
        title = normalize_entry_title(match.group(2))
        if title not in by_title:
            by_title[title] = (match.start(), match.end(), match.group(0))

    for match in BARE_WIKILINK_LINE_RE.finditer(text):
        title = normalize_entry_title(match.group(1))
        if title not in by_title:
            by_title[title] = (match.start(), match.end(), match.group(1))

    return by_title


def append_completed_lines(
    journal_path: Path, tasks: list[CompletedTask]
) -> tuple[list[CompletedTask], list[CompletedTask]]:
    """Ensure each task has the right journal line; upgrade wrong/legacy forms in place."""
    try:
        text = journal_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""

    by_title = index_existing_entries(text)
    appended: list[CompletedTask] = []
    already: list[CompletedTask] = []
    new_lines: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    for task in tasks:
        desired = task.journal_line()
        existing = by_title.get(task.title)
        if existing is None:
            new_lines.append(desired)
            appended.append(task)
            by_title[task.title] = (-1, -1, desired)
            continue

        start, end, current = existing
        if current == desired:
            already.append(task)
            continue

        replacements.append((start, end, desired))
        appended.append(task)
        by_title[task.title] = (start, end, desired)

    if replacements:
        for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
            text = text[:start] + replacement + text[end:]

    if new_lines:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n".join(new_lines) + "\n"

    if replacements or new_lines:
        journal_path.write_text(text, encoding="utf-8")

    return appended, already


def build_telegram_message(vault_alias: str, result: DumpResult) -> str:
    lines = [
        f"Volcado de tareas completadas ({result.yesterday})",
        f"Vault: {vault_alias}",
    ]

    if result.journal_path is not None:
        lines.append(f"Diario: {result.journal_path}")
        if result.journal_created:
            lines.append("(diario creado junto a los demás diarios del vault)")
        elif result.journal_candidates > 1:
            lines.append(
                f"(había {result.journal_candidates} candidatos; se eligió el del directorio de diarios)"
            )

    if not result.completed_found:
        lines.append("")
        lines.append("Ninguna tarea completada ayer.")
        return "\n".join(lines)

    if result.appended:
        lines.append("")
        lines.append(f"Volcadas ({len(result.appended)}):")
        lines.extend(task.journal_line() for task in result.appended)

    if result.already_present:
        lines.append("")
        lines.append(f"Ya estaban en el diario ({len(result.already_present)}):")
        lines.extend(task.journal_line() for task in result.already_present)

    if not result.appended and result.already_present:
        lines.append("")
        lines.append("No había nada nuevo que volcar.")

    return "\n".join(lines)


def run_dump(vault_path: str, day: str | None = None) -> DumpResult:
    vault_root = Path(vault_path)
    target_day = day or yesterday_madrid()
    tasks_dir = vault_root / OBSIDIAN_TASKS_PREFIX

    completed = scan_completed_tasks(tasks_dir, target_day)

    candidates = find_journal_candidates(vault_root, target_day)
    created = False
    appended: list[CompletedTask] = []
    already: list[CompletedTask] = []
    rel_journal: Path | None = None

    if completed:
        journal_path, created, candidate_count = resolve_journal(vault_root, target_day)
        try:
            rel_journal = Path(str(journal_path.relative_to(vault_root)))
        except ValueError:
            rel_journal = journal_path
        appended, already = append_completed_lines(journal_path, completed)
    elif candidates:
        candidate_count = len(candidates)
        try:
            rel_journal = Path(str(candidates[0].relative_to(vault_root)))
        except ValueError:
            rel_journal = candidates[0]
    else:
        candidate_count = 0

    return DumpResult(
        yesterday=target_day,
        journal_path=rel_journal,
        journal_created=created,
        journal_candidates=candidate_count,
        appended=appended,
        already_present=already,
        completed_found=completed,
    )


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
                    "No hay vault elegido. Elige uno para el volcado diario de tareas:",
                )
        except Exception as exc:
            print(f"Error enviando recordatorio de vault: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        result = run_dump(vault.path)
    except Exception as exc:
        print(f"Error volcando tareas al diario: {exc}", file=sys.stderr)
        try:
            bot = Bot(TOKEN)
            async with bot:
                await bot.send_message(
                    chat_id=MY_CHAT_ID,
                    text=f"Error en el volcado diario de tareas: {exc}",
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
