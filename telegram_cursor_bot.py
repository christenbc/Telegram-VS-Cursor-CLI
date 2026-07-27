import asyncio
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from telegram import Bot, MessageEntity as TelegramMessageEntity, Update
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegramify_markdown.converter import convert
from telegramify_markdown.entity import split_entities
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
MAX_MESSAGE_UTF16 = 4096

BOT_REPO_ROOT = Path(__file__).resolve().parent
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
    text = _OBSIDIAN_MD_LINK_RE.sub(
        lambda match: f"[{match.group(1)}]({obsidian_uri_to_obsid_net_url(match.group(2))})",
        markdown,
    )
    text = _PLAIN_OBSIDIAN_URI_RE.sub(
        lambda match: obsidian_uri_to_obsid_net_url(match.group(1)),
        text,
    )
    return _OBSID_NET_MD_LINK_RE.sub(
        lambda match: f"[{match.group(1)}]({normalize_obsid_net_url(match.group(2))})",
        text,
    )


def render_telegram_message(markdown: str) -> tuple[str, list]:
    return convert(preprocess_markdown_for_telegram(markdown))


def build_telegram_format_hint() -> str:
    return (
        "Responde en el mismo idioma que el mensaje del usuario.\n"
        "Importante: tu respuesta se mostrará en un chat de Telegram y se renderizará "
        "como Markdown. Usa formato enriquecido cuando mejore la legibilidad:\n"
        "- **negrita** y *cursiva* para énfasis\n"
        "- `código inline` y bloques con ```\n"
        "- listas con viñetas o numeradas\n"
        "- encabezados (##), citas (>), ~~tachado~~, ||spoiler||\n"
        "- emojis Unicode con moderación\n"
        "No uses tablas Markdown (| col | col |): en Telegram se ven mal. "
        "Para datos tabulares usa listas o líneas **Campo:** valor."
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
):
    """Stream assistant text deltas from the Cursor agent CLI."""
    full_prompt = build_agent_prompt(
        prompt, include_tasknotes_skill=include_tasknotes_skill
    )

    proc = await asyncio.create_subprocess_exec(
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
        full_prompt,
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


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()
