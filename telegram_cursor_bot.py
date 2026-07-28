import asyncio
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import time

from telegram import Bot, MessageEntity as TelegramMessageEntity, Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegramify_markdown.converter import convert
from telegramify_markdown.entity import split_entities, utf16_len
from telegramify_markdown.stream import DraftStream
from telegramify_markdown.stream.draft import EntityDraftPayload, EntityFinalPayload

logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MY_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
VAULT_PATH = os.environ.get("VAULT_PATH", "/home/ubuntu/vaults/test")
OBSIDIAN_VAULT_NAME = os.environ.get("OBSIDIAN_VAULT_NAME", "Testing")
OBSIDIAN_TASKS_PREFIX = os.environ.get("OBSIDIAN_TASKS_PREFIX", "TaskNotes/Tasks")
OBSID_NET_BASE = os.environ.get("OBSID_NET_BASE", "https://obsid.net").rstrip("/")
CURSOR_AGENT_PATH = os.environ.get("CURSOR_AGENT_PATH", "/home/ubuntu/.local/bin/agent")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "120"))
SESSION_IDLE_TIMEOUT = int(os.environ.get("SESSION_IDLE_TIMEOUT", "2700"))
MAX_MESSAGE_UTF16 = 4096

BOT_REPO_ROOT = Path(__file__).resolve().parent
SESSIONS_PATH = BOT_REPO_ROOT / "sessions.json"
TASKNOTES_SKILL_PATH = (
    BOT_REPO_ROOT / ".cursor/skills/obsidian-tasknotes/SKILL.md"
)
_TASKNOTES_SKILL_CACHE: tuple[float, str] | None = None

_TASKNOTES_KEYWORDS_RE = re.compile(
    r"\b("
    r"tarea|tareas|task|tasks|tasknotes|tasknote|pendiente|pendientes|"
    r"atrasad|overdue|due|scheduled|prioridad|priority|"
    r"inbox|agenda|kanban"
    r")\b",
    re.IGNORECASE,
)

_OBSIDIAN_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((obsidian://[^)\s]+)\)")
_PLAIN_OBSIDIAN_URI_RE = re.compile(r"(?<!\])(obsidian://open\?[^\s)]+)")
# Agents often emit unencoded spaces in file=; markdown links need percent-encoding.
_OBSID_NET_MD_LINK_RE = re.compile(
    r"\[([^\]\n]+)\]\((" + re.escape(OBSID_NET_BASE) + r"/\?[^\)]+)\)",
    re.IGNORECASE,
)
# telegramify-markdown heading marker, or priority-group labels (📚 …).
_SECTION_LINE_RE = re.compile(r"^(?:✏ |\U0001F4DA)")


def build_obsid_net_url(relative_note_path: str, vault: str | None = None) -> str:
    """Build an obsid.net redirect URL for a note relative to the vault root."""
    note_path = relative_note_path.removesuffix(".md").replace("\\", "/").lstrip("/")
    query = urlencode(
        {"vault": vault or OBSIDIAN_VAULT_NAME, "file": note_path},
        quote_via=quote,
    )
    return f"{OBSID_NET_BASE}/?{query}"


def normalize_obsid_net_url(url: str) -> str:
    """Re-encode vault/file query params so Telegram markdown can parse the link."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    vault = query.get("vault", [""])[0]
    file_path = query.get("file", [""])[0]
    if not vault or not file_path:
        return url
    return build_obsid_net_url(file_path, vault=vault)


def obsidian_uri_to_obsid_net_url(obsidian_uri: str) -> str:
    """Convert obsidian://open?... URIs into https://obsid.net/?... links."""
    parsed = urlparse(obsidian_uri)
    if parsed.scheme != "obsidian":
        return obsidian_uri

    query = parse_qs(parsed.query)
    vault = query.get("vault", [""])[0]
    if not vault:
        return obsidian_uri

    file_path = query.get("file", [""])[0]
    return build_obsid_net_url(file_path, vault=vault)


def preprocess_markdown_for_telegram(markdown: str) -> str:
    """Normalize Obsidian / obsid.net links so Telegram can embed them."""
    # ☐ (U+2610) often fails to render in Telegram clients; ⬜ is reliable.
    text = markdown.replace("☐", "⬜")
    text = _OBSIDIAN_MD_LINK_RE.sub(
        lambda match: f"[{match.group(1)}]({obsidian_uri_to_obsid_net_url(match.group(2))})",
        text,
    )
    text = _PLAIN_OBSIDIAN_URI_RE.sub(
        lambda match: obsidian_uri_to_obsid_net_url(match.group(1)),
        text,
    )
    return _OBSID_NET_MD_LINK_RE.sub(
        lambda match: f"[{match.group(1)}]({normalize_obsid_net_url(match.group(2))})",
        text,
    )


def ensure_section_spacing(text: str, entities: list) -> tuple[str, list]:
    """Insert a blank line before section headers when telegramify collapsed it.

    Headings (✏ …) and priority-group labels (📚 …) often sit flush against
    the previous list/paragraph; Telegram needs an extra newline for readability.
    Entity offsets are UTF-16 and are shifted to match insertions.
    """
    if not text:
        return text, entities

    lines = text.split("\n")
    out_lines: list[str] = []
    insert_at_utf16: list[int] = []
    utf16_pos = 0
    prev_blank = True

    for idx, line in enumerate(lines):
        if _SECTION_LINE_RE.match(line) and not prev_blank and out_lines:
            out_lines.append("")
            insert_at_utf16.append(utf16_pos)
            utf16_pos += 1  # inserted "\n"
        out_lines.append(line)
        if idx < len(lines) - 1:
            utf16_pos += utf16_len(line) + 1
        else:
            utf16_pos += utf16_len(line)
        prev_blank = line == ""

    if not insert_at_utf16:
        return text, entities

    new_text = "\n".join(out_lines)
    new_entities = []
    for entity in entities:
        shift = sum(1 for offset in insert_at_utf16 if offset <= entity.offset)
        new_entities.append(
            entity.copy_with(offset=entity.offset + shift) if shift else entity
        )
    return new_text, new_entities


def render_telegram_message(markdown: str) -> tuple[str, list]:
    text, entities = convert(preprocess_markdown_for_telegram(markdown))
    return ensure_section_spacing(text, entities)


def build_telegram_format_hint() -> str:
    return (
        "Responde en el mismo idioma que el mensaje del usuario.\n"
        "Importante: tu respuesta se mostrará en un chat de Telegram y se renderizará "
        "como Markdown. Usa formato enriquecido cuando mejore la legibilidad:\n"
        "- **negrita** y *cursiva* para énfasis\n"
        "- `código inline` y bloques con ```\n"
        "- listas con viñetas o numeradas\n"
        "- encabezados (##), citas (>), ~~tachado~~, ||spoiler||\n"
        "- emojis con moderación; no inventes iconos de metadatos (hora, recurrencia, etc.)\n"
        "- deja una línea en blanco antes de cada encabezado o bloque de sección\n"
        "No uses tablas Markdown (| col | col |): en Telegram se ven mal. "
        "Para datos tabulares usa listas o líneas **Campo:** valor.\n\n"
        "Si el pedido es ambiguo, falta información clave, o vas a realizar una acción "
        "irreversible o difícil de deshacer (borrar, sobrescribir, ejecutar comandos con "
        "efectos secundarios) y no estás seguro de la intención del usuario, pregunta "
        "primero en lugar de asumir. La conversación tiene memoria: el usuario podrá "
        "responder tu pregunta en su siguiente mensaje y retomarás el contexto."
    )


TELEGRAM_FORMAT_HINT = build_telegram_format_hint()


def _template_tasknotes_skill(raw: str) -> str:
    return raw.format(
        OBSIDIAN_VAULT_NAME=OBSIDIAN_VAULT_NAME,
        OBSIDIAN_TASKS_PREFIX=OBSIDIAN_TASKS_PREFIX,
        OBSID_NET_BASE=OBSID_NET_BASE,
    )


def load_tasknotes_skill() -> str:
    global _TASKNOTES_SKILL_CACHE

    mtime = TASKNOTES_SKILL_PATH.stat().st_mtime
    if _TASKNOTES_SKILL_CACHE and _TASKNOTES_SKILL_CACHE[0] == mtime:
        return _TASKNOTES_SKILL_CACHE[1]

    raw = TASKNOTES_SKILL_PATH.read_text(encoding="utf-8")
    templated = _template_tasknotes_skill(raw)
    _TASKNOTES_SKILL_CACHE = (mtime, templated)
    return templated


def should_include_tasknotes_skill(prompt: str) -> bool:
    return bool(_TASKNOTES_KEYWORDS_RE.search(prompt))


def build_agent_prompt(prompt: str, *, include_tasknotes_skill: bool = False) -> str:
    parts = [prompt, TELEGRAM_FORMAT_HINT]
    if include_tasknotes_skill:
        parts.append(load_tasknotes_skill())
    return "\n\n".join(parts)


def load_sessions() -> dict:
    """Load the chat_id -> {session_id, updated_at} map from disk."""
    try:
        with SESSIONS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_sessions(data: dict) -> None:
    tmp_path = SESSIONS_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f)
    tmp_path.replace(SESSIONS_PATH)


def get_active_session(chat_id: int) -> str | None:
    """Return the session_id to resume for this chat, or None to start fresh."""
    sessions = load_sessions()
    entry = sessions.get(str(chat_id))
    if not entry:
        return None
    if time.time() - entry.get("updated_at", 0) > SESSION_IDLE_TIMEOUT:
        return None
    return entry.get("session_id")


def set_session(chat_id: int, session_id: str) -> None:
    sessions = load_sessions()
    sessions[str(chat_id)] = {"session_id": session_id, "updated_at": time.time()}
    save_sessions(sessions)


def clear_session(chat_id: int) -> None:
    sessions = load_sessions()
    if sessions.pop(str(chat_id), None) is not None:
        save_sessions(sessions)


def _to_telegram_entities(entities):
    """Convert telegramify-markdown entities to python-telegram-bot MessageEntity."""
    if not entities:
        return None
    return [TelegramMessageEntity(**entity.to_dict()) for entity in entities]


async def run_agent_streaming(
    prompt: str,
    timeout: float = AGENT_TIMEOUT,
    *,
    include_tasknotes_skill: bool = False,
    resume_session_id: str | None = None,
    session_id_holder: dict | None = None,
):
    """Stream assistant text deltas from the Cursor agent CLI.

    If `resume_session_id` is set, the CLI resumes that chat session instead
    of starting a new one, preserving full conversation context (including
    any clarifying question the agent asked in a previous turn). The
    session_id of the run (new or resumed) is written into
    `session_id_holder["session_id"]` as soon as it is known, so the caller
    can persist it even if the run later fails or times out.
    """
    full_prompt = build_agent_prompt(
        prompt, include_tasknotes_skill=include_tasknotes_skill
    )

    args = [
        CURSOR_AGENT_PATH,
        "-p",
        "--output-format",
        "stream-json",
        "--stream-partial-output",
        "--workspace",
        VAULT_PATH,
        "--force",
        "--model",
        "composer-2.5-fast",
    ]
    if resume_session_id:
        args += ["--resume", resume_session_id]
    args.append(full_prompt)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    yielded_any = False
    fallback_result = None

    async def _iter_chunks():
        nonlocal yielded_any, fallback_result

        while True:
            line = await proc.stdout.readline()
            if not line:
                break

            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if session_id_holder is not None and event.get("session_id"):
                session_id_holder["session_id"] = event["session_id"]

            event_type = event.get("type")
            # Real deltas have timestamp_ms but no model_call_id. Once a
            # narration/answer chunk finishes, the CLI re-emits the full
            # text as a consolidation event carrying model_call_id (and,
            # for the final answer, no timestamp_ms at all) - skip both to
            # avoid duplicating text already streamed via deltas.
            if (
                event_type == "assistant"
                and "timestamp_ms" in event
                and "model_call_id" not in event
            ):
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            yielded_any = True
                            yield text
            elif event_type == "result":
                if event.get("is_error"):
                    raise RuntimeError(event.get("result") or "Error del agente")
                fallback_result = event.get("result") or ""

    try:
        async with asyncio.timeout(timeout):
            async for chunk in _iter_chunks():
                yield chunk

            if not yielded_any and fallback_result:
                yield fallback_result

            await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"El agente superó el límite de {timeout}s") from None

    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"El agente terminó con código {proc.returncode}")


async def _send_draft(bot, chat_id: int, payload: EntityDraftPayload):
    entities = _to_telegram_entities(payload.entities)
    try:
        if payload.text:
            await bot.send_message_draft(
                chat_id=chat_id,
                draft_id=payload.draft_id,
                text=payload.text,
                entities=entities,
            )
        else:
            await bot.send_message_draft(
                chat_id=chat_id,
                draft_id=payload.draft_id,
                text="",
            )
    except BadRequest as exc:
        # Drafts are best-effort previews; a transient failure shouldn't
        # abort the stream, the final send_message still delivers the answer.
        logger.debug("Draft send failed: %s", exc)


async def _send_message_chunks(bot, chat_id: int, text: str, entities: list):
    chunks = split_entities(text, entities, MAX_MESSAGE_UTF16)
    if not chunks:
        await bot.send_message(chat_id=chat_id, text="Hecho, sin salida del agente.")
        return

    for chunk_text, chunk_entities in chunks:
        tg_entities = _to_telegram_entities(chunk_entities)
        try:
            if tg_entities:
                await bot.send_message(
                    chat_id=chat_id, text=chunk_text, entities=tg_entities
                )
            else:
                await bot.send_message(chat_id=chat_id, text=chunk_text)
        except Exception as exc:
            logger.warning(
                "Failed to send formatted chunk (%s); falling back to plain text",
                exc,
            )
            await bot.send_message(chat_id=chat_id, text=chunk_text)


async def _send_final(bot, chat_id: int, payload: EntityFinalPayload):
    text = payload.text or "Hecho, sin salida del agente."
    entities = payload.entities or []
    await _send_message_chunks(bot, chat_id, text, entities)


async def send_agent_prompt_to_telegram(
    prompt: str,
    timeout: float = AGENT_TIMEOUT,
    chat_id: int = MY_CHAT_ID,
    *,
    include_tasknotes_skill: bool = False,
):
    """Run the agent with a prompt and send the full response to Telegram."""
    chunks: list[str] = []
    async for chunk in run_agent_streaming(
        prompt,
        timeout=timeout,
        include_tasknotes_skill=include_tasknotes_skill,
    ):
        chunks.append(chunk)

    markdown = "".join(chunks).strip() or "Hecho, sin salida del agente."
    text, entities = render_telegram_message(markdown)

    bot = Bot(TOKEN)
    async with bot:
        await _send_message_chunks(bot, chat_id, text, entities)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != MY_CHAT_ID:
        return

    user_text = update.message.text
    chat_id = update.message.chat_id
    bot = context.bot
    resume_id = get_active_session(chat_id)
    session_id_holder: dict = {}

    async def send_draft_cb(payload):
        text, entities = render_telegram_message(stream.buffer)
        await _send_draft(
            bot,
            chat_id,
            EntityDraftPayload(
                text=text,
                entities=entities,
                draft_id=payload.draft_id,
            ),
        )

    async def send_final_cb(payload):
        text, entities = render_telegram_message(stream.buffer)
        await _send_final(
            bot,
            chat_id,
            EntityFinalPayload(text=text, entities=entities),
        )

    stream = None
    try:
        async with DraftStream(
            send_draft=send_draft_cb,
            send_final=send_final_cb,
            mode="entity",
            interval=0.3,
        ) as stream:
            async for chunk in run_agent_streaming(
                user_text,
                include_tasknotes_skill=should_include_tasknotes_skill(user_text),
                resume_session_id=resume_id,
                session_id_holder=session_id_holder,
            ):
                stream.feed(chunk)

            if not stream.buffer.strip():
                stream.feed("Hecho, sin salida del agente.")

            await stream.finish()
    except TimeoutError as e:
        if stream is not None:
            await stream.cancel()
        await bot.send_message(chat_id=chat_id, text=f"Error: {e}")
    except Exception as e:
        if stream is not None:
            await stream.cancel()
        await bot.send_message(chat_id=chat_id, text=f"Error: {e}")
    finally:
        if session_id_holder.get("session_id"):
            set_session(chat_id, session_id_holder["session_id"])


async def handle_new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != MY_CHAT_ID:
        return

    clear_session(update.message.chat_id)
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="Listo, empezamos una conversación nueva (sin contexto previo).",
    )


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("new", handle_new_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()
