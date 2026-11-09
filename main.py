# main.py — entrypoint
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from admin import (
    admin_callback,
    admin_command,
    ban_command,
    unban_command,
    user_command,
)
from broadcast import broadcast_callback, broadcast_command
from config import ADMIN_IDS, BOT_TOKEN, LOG_LEVEL
from database import close_db, get_user, increment_translations, init_db, log_translation, upsert_user
from force_join import check_force_join, force_join_keyboard
from translation import translate_to_english

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

WELCOME = (
    "👋 Welcome to Auto Translation Bot!\n\n"
    "🌍 I can automatically translate text from almost any language into English.\n\n"
    "📌 How to use:\n"
    "Simply send me any text in any language.\n\n"
    "⚡ I will automatically detect the language and translate it into English.\n\n"
    "🚀 No commands or language selection required!"
)


async def _gate(update: Update, bot) -> bool:
    """Register user, enforce force-join and ban. Returns True if allowed to proceed."""
    user = update.effective_user
    if not user:
        return False

    await upsert_user(user.id, user.username, user.first_name)

    db_user = await get_user(user.id)
    if db_user and db_user.get("banned"):
        if update.message:
            await update.message.reply_text("🚫 You are banned from using this bot.")
        return False

    allowed, reason = await check_force_join(bot, user.id)
    if not allowed:
        if reason == "channel":
            text = "🔒 *ACCESS REQUIRED*\n\nYou still need to join the *channel*."
        elif reason == "group":
            text = "🔒 *ACCESS REQUIRED*\n\nYou still need to join the *group*."
        else:
            text = "🔒 *ACCESS REQUIRED*\n\nTo use this bot, you must join our official channel and group.\n\nPlease join both destinations below and verify your membership."
        if update.message:
            await update.message.reply_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=force_join_keyboard()
            )
        return False
    return True


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update, context.bot):
        return
    await update.message.reply_text(WELCOME)


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if q.data != "fj:verify":
        return
    user = update.effective_user
    allowed, reason = await check_force_join(context.bot, user.id)
    if allowed:
        await q.edit_message_text(WELCOME)
    else:
        if reason == "channel":
            await q.edit_message_text(
                "🔒 Still missing: *channel*. Join and tap Verify again.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=force_join_keyboard(),
            )
        elif reason == "group":
            await q.edit_message_text(
                "🔒 Still missing: *group*. Join and tap Verify again.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=force_join_keyboard(),
            )
        else:
            await q.edit_message_text(
                "🔒 Verification failed. Make sure you joined both destinations, then tap Verify again.",
                reply_markup=force_join_keyboard(),
            )


async def translate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if not await _gate(update, context.bot):
        return

    user = update.effective_user
    text = update.message.text
    result, source = await translate_to_english(text)

    if result is None:
        await log_translation(user.id, source, len(text), False)
        await update.message.reply_text(
            "⚠️ Translation failed. The service may be temporarily unavailable. Please try again."
        )
        return

    await log_translation(user.id, source, len(text), True)
    await increment_translations(user.id)

    # Split translated output to Telegram's 4096-char limit
    max_len = 4096
    header = "🇬🇧 English Translation\n\n"
    full = header + result
    if len(full) <= max_len:
        await update.message.reply_text(full)
        return
    # first chunk carries the header
    first = full[:max_len]
    await update.message.reply_text(first)
    rest = full[max_len:]
    while rest:
        chunk = rest[:max_len]
        rest = rest[max_len:]
        await update.message.reply_text(chunk)


def main() -> None:
    async def post_init(app):
        await init_db()
        logger.info("Bot starting")

    async def post_shutdown(app):
        await close_db()
        logger.info("Bot stopped")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(
