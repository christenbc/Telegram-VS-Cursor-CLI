import asyncio
import json
import os

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegramify_markdown.stream import DraftStream
from telegramify_markdown.stream.draft import EntityDraftPayload, EntityFinalPayload

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MY_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
VAULT_PATH = os.environ.get("VAULT_PATH", "/home/ubuntu/vaults/test")
CURSOR_AGENT_PATH = os.environ.get("CURSOR_AGENT_PATH", "/home/ubuntu/.local/bin/agent")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "120"))

TELEGRAM_FORMAT_HINT = (
    "Importante: responde en texto plano usando listas con viñetas o líneas separadas "
    "(formato 'Tarea: ... - Estado: ...'), nunca usando tablas Markdown, "
    "ya que se van a mostrar en un chat de Telegram donde las tablas no se ven bien."
)


async def run_agent_streaming(prompt: str, timeout: float = AGENT_TIMEOUT):
    """Stream assistant text deltas from the Cursor agent CLI."""
    full_prompt = f"{prompt}\n\n{TELEGRAM_FORMAT_HINT}"

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
    try:
        if payload.text:
            await bot.send_message_draft(
                chat_id=chat_id,
                draft_id=payload.draft_id,
                text=payload.text,
                entities=payload.entities or None,
            )
        else:
            await bot.send_message_draft(
                chat_id=chat_id,
                draft_id=payload.draft_id,
                text="",
            )
    except BadRequest:
        # Drafts are best-effort previews; a transient failure shouldn't
        # abort the stream, the final send_message still delivers the answer.
        pass


async def _send_final(bot, chat_id: int, payload: EntityFinalPayload):
    text = (payload.text or "Hecho, sin salida del agente.")[:4096]
    entities = payload.entities or None
    try:
        if entities:
            await bot.send_message(chat_id=chat_id, text=text, entities=entities)
        else:
            await bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        await bot.send_message(chat_id=chat_id, text=text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != MY_CHAT_ID:
        return

    user_text = update.message.text
    chat_id = update.message.chat_id
    bot = context.bot

    async def send_draft_cb(payload):
        await _send_draft(bot, chat_id, payload)

    async def send_final_cb(payload):
        await _send_final(bot, chat_id, payload)

    stream = None
    try:
        async with DraftStream(
            send_draft=send_draft_cb,
            send_final=send_final_cb,
            mode="entity",
            interval=0.3,
        ) as stream:
            async for chunk in run_agent_streaming(user_text):
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
