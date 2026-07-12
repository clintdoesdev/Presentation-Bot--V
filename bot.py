import os
import re
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("You're not authorized to use this bot.")
        return
    await update.message.reply_text(
        "👋 Send me the post you want published to the channel.\n\n"
        "• Plain text, or a photo with a caption — both work.\n"
        "• Add buttons anywhere in the text with this format:\n"
        "  [Button Label - https://example.com]\n"
        "• Add as many as you want, one per line:\n\n"
        "[REGISTER NOW - https://vireonwebsite.com.ng]\n"
        "[JOIN CHANNEL - https://t.me/yourchannel]\n\n"
        "I'll show you a preview with a Confirm / Cancel button before anything "
        "goes live in the channel."
    )


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
    note = f"\n\n— PREVIEW — {len(buttons)} button(s) attached —"

    if photo:
        await msg.reply_photo(
            photo=photo,
            caption=(cleaned_text or "") + note,
            reply_markup=preview_markup,
        )
    else:
        await msg.reply_text((cleaned_text or "") + note, reply_markup=preview_markup)


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


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_message)
    )
    app.add_handler(CallbackQueryHandler(handle_confirmation))
    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
