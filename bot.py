import asyncio
import mimetypes
import os
import re
import logging
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---- Config (set these as environment variables on Railway) ----
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]  # e.g. -1001234567890 or @yourchannelusername
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
}  # comma-separated Telegram user IDs allowed to post. Leave empty to allow anyone (not recommended).

# Matches things like: [REGISTER NOW - https://vireonwebsite.com.ng]
BUTTON_PATTERN = re.compile(r"\[\s*([^\[\]\-]+?)\s*-\s*(https?://[^\s\]]+)\s*\]")

# Telegram's hard limits — going over these causes a BadRequest and, without
# an error handler, a silently "dead" bot.
PHOTO_CAPTION_LIMIT = 1024
TEXT_MESSAGE_LIMIT = 4096

# ---- Audio download config ----
# How long to wait after the last forwarded audio before processing the batch —
# lets several forwards sent back-to-back land as one properly-numbered set.
AUDIO_BATCH_DELAY = float(os.environ.get("AUDIO_BATCH_DELAY", "1.5"))


def parse_buttons(text: str):
    """Pull out (label, url) pairs and return the text with those tags stripped out."""
    text = text or ""
    buttons = BUTTON_PATTERN.findall(text)
    cleaned = BUTTON_PATTERN.sub("", text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()  # collapse leftover blank lines
    return cleaned, buttons


def build_markup(buttons):
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label.strip(), url=url.strip())] for label, url in buttons]
    )


def is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|\r\n]', "", name).strip(" .")
    return name or "audio"


def convert_to_mp3(src: Path, dest: Path):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [ffmpeg_exe, "-y", "-i", str(src), "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", str(dest)],
        check=True,
        capture_output=True,
    )


def audio_source(msg):
    """Return (file_id, suggested_filename) for an audio-bearing message, or None."""
    if msg.audio:
        a = msg.audio
        if a.file_name:
            stem, ext = Path(a.file_name).stem, Path(a.file_name).suffix
        else:
            stem = " - ".join(filter(None, [a.performer, a.title])) or "audio"
            ext = mimetypes.guess_extension(a.mime_type or "") or ".mp3"
        return a.file_id, sanitize_filename(stem) + ext

    if msg.voice:
        v = msg.voice
        ext = mimetypes.guess_extension(v.mime_type or "") or ".ogg"
        return v.file_id, "voice" + ext

    if msg.document and (msg.document.mime_type or "").startswith("audio/"):
        d = msg.document
        base = d.file_name or "audio"
        stem, ext = Path(base).stem, Path(base).suffix
        if not ext:
            ext = mimetypes.guess_extension(d.mime_type or "") or ""
        return d.file_id, sanitize_filename(stem) + ext

    return None


MAIN_MENU_TEXT = "👋 What would you like to do?"

TEXT_HELP = (
    "📝 Text & Photo Posts\n\n"
    "Send me the post you want published to the channel.\n\n"
    "• Plain text, or a photo with a caption — both work.\n"
    "• Add buttons anywhere in the text with this format:\n"
    "  [Button Label - https://example.com]\n"
    "• Add as many as you want, one per line:\n\n"
    "[REGISTER NOW - https://vireonwebsite.com.ng]\n"
    "[JOIN CHANNEL - https://t.me/yourchannel]\n\n"
    "I'll show you a preview with a Confirm / Cancel button before anything "
    "goes live in the channel."
)

AUDIO_HELP = (
    "🎧 Audio Downloads\n\n"
    "Forward me one or more audio files or voice notes. I'll convert each "
    "one to MP3 and send it right back to you here, numbered in the order "
    "you forwarded them (01, 02, ...) — just tap each file in the chat to "
    "download it."
)


def main_menu_markup():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Text & Photo Posts", callback_data="menu:text")],
            [InlineKeyboardButton("🎧 Audio Downloads", callback_data="menu:audio")],
        ]
    )


def back_to_menu_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu:main")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("You're not authorized to use this bot.")
        return
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=main_menu_markup())


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    if query.data == "menu:text":
        await query.edit_message_text(TEXT_HELP, reply_markup=back_to_menu_markup())
    elif query.data == "menu:audio":
        await query.edit_message_text(AUDIO_HELP, reply_markup=back_to_menu_markup())
    else:  # menu:main
        await query.edit_message_text(MAIN_MENU_TEXT, reply_markup=main_menu_markup())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return

    msg = update.message
    photo = msg.photo[-1].file_id if msg.photo else None
    raw_text = msg.caption if photo else msg.text

    if not raw_text and not photo:
        await msg.reply_text("Send text, or a photo with a caption.")
        return

    cleaned_text, buttons = parse_buttons(raw_text)

    # Check against Telegram's real limits BEFORE attempting to send anything.
    # (Tags like [Label - URL] are already stripped out, so this reflects the
    # actual visible length.)
    limit = PHOTO_CAPTION_LIMIT if photo else TEXT_MESSAGE_LIMIT
    if len(cleaned_text) > limit:
        over = len(cleaned_text) - limit
        kind = "photo caption" if photo else "message"
        tip = (
            "Trim it, or send the photo on its own and the full copy as a separate text post."
            if photo
            else "Trim it and resend."
        )
        await msg.reply_text(
            f"⚠️ That {kind} is {len(cleaned_text)} characters — {over} over Telegram's "
            f"{limit}-character limit. {tip}"
        )
        return

    # Stash the pending post for this admin until they confirm.
    context.user_data["pending_post"] = {
        "photo": photo,
        "text": cleaned_text,
        "buttons": buttons,
    }

    preview_rows = [
        [InlineKeyboardButton(label.strip(), url=url.strip())] for label, url in buttons
    ]
    preview_rows.append(
        [
            InlineKeyboardButton("✅ Post to channel", callback_data="confirm_post"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_post"),
        ]
    )
    preview_markup = InlineKeyboardMarkup(preview_rows)

    try:
        if photo:
            await msg.reply_photo(
                photo=photo,
                caption=cleaned_text or None,
                reply_markup=preview_markup,
            )
        else:
            await msg.reply_text(cleaned_text, reply_markup=preview_markup)
        await msg.reply_text(f"👆 Preview — {len(buttons)} button(s) attached. Confirm to post.")
    except BadRequest as e:
        logger.exception("Failed to build preview")
        context.user_data.pop("pending_post", None)
        await msg.reply_text(f"⚠️ Telegram rejected that message: {e}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Queue up forwarded audio/voice/document-audio for download.

    Telegram delivers a batch of forwarded files as separate updates in the
    order they were forwarded, so each one is appended to a per-chat queue.
    We wait a short beat after the last arrival before saving the whole
    batch, numbering the files 1..N in that same order.
    """
    user = update.effective_user
    if not is_admin(user.id):
        return

    msg = update.message
    source = audio_source(msg)
    if not source:
        return
    file_id, suggested_name = source

    chat_id = update.effective_chat.id
    queue = context.chat_data.setdefault("audio_queue", [])
    queue.append((file_id, suggested_name))

    existing_task = context.chat_data.get("audio_flush_task")
    if existing_task and not existing_task.done():
        existing_task.cancel()
    context.chat_data["audio_flush_task"] = asyncio.create_task(
        _flush_audio_queue(context, chat_id)
    )


async def _flush_audio_queue(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        await asyncio.sleep(AUDIO_BATCH_DELAY)
    except asyncio.CancelledError:
        return  # a newer audio arrived and superseded this flush

    queue = context.chat_data.pop("audio_queue", [])
    context.chat_data.pop("audio_flush_task", None)
    if not queue:
        return

    width = len(str(len(queue)))
    failed = []

    with tempfile.TemporaryDirectory(prefix="audio_dl_") as tmp:
        tmp_dir = Path(tmp)
        for i, (file_id, suggested_name) in enumerate(queue, start=1):
            stem, ext = Path(suggested_name).stem, Path(suggested_name).suffix
            mp3_name = f"{i:0{width}d} - {stem}.mp3" if len(queue) > 1 else f"{stem}.mp3"
            try:
                tg_file = await context.bot.get_file(file_id)
                src_path = tmp_dir / f"src_{i}{ext}"
                await tg_file.download_to_drive(custom_path=src_path)

                mp3_path = tmp_dir / mp3_name
                if ext.lower() == ".mp3":
                    src_path.replace(mp3_path)
                else:
                    await asyncio.to_thread(convert_to_mp3, src_path, mp3_path)

                with open(mp3_path, "rb") as f:
                    await context.bot.send_audio(
                        chat_id=chat_id, audio=f, filename=mp3_name, title=stem
                    )
            except Exception:
                logger.exception("Failed to convert/send forwarded audio (%s)", suggested_name)
                failed.append(suggested_name)

    if failed:
        lines = [f"⚠️ Failed to convert/send {len(failed)} file(s):"]
        lines += [f"• {name}" for name in failed]
        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))


async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    pending = context.user_data.get("pending_post")
    if not pending:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if query.data == "cancel_post":
        context.user_data.pop("pending_post", None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Cancelled — nothing was posted.")
        return

    if query.data == "confirm_post":
        markup = build_markup(pending["buttons"])
        try:
            if pending["photo"]:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=pending["photo"],
                    caption=pending["text"] or None,
                    reply_markup=markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=pending["text"],
                    reply_markup=markup,
                )
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("✅ Posted to the channel.")
        except Exception as e:
            logger.exception("Failed to post to channel")
            await query.message.reply_text(f"❌ Failed to post: {e}")
        finally:
            context.user_data.pop("pending_post", None)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all so a crash gets reported to you instead of just logged and forgotten."""
    logger.error("Unhandled exception while processing an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ Something went wrong: {context.error}",
            )
        except Exception:
            pass  # don't let a failed error-report crash the error handler itself


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            (filters.AUDIO | filters.VOICE | filters.Document.AUDIO) & ~filters.COMMAND,
            handle_audio,
        )
    )
    app.add_handler(
        MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_message)
    )
    app.add_handler(CallbackQueryHandler(handle_menu, pattern=r"^menu:"))
    app.add_handler(
        CallbackQueryHandler(handle_confirmation, pattern=r"^(confirm_post|cancel_post)$")
    )
    app.add_error_handler(error_handler)
    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
