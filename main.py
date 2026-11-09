import os
import re
import asyncio
import logging
import sqlite3

from datetime import datetime, timezone
from functools import partial

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError, Forbidden, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from deep_translator import GoogleTranslator


# =====================================================
# CONFIGURATION
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

FORCE_CHANNEL = os.getenv("FORCE_CHANNEL_ID")
FORCE_GROUP = os.getenv("FORCE_GROUP_ID")

CHANNEL_LINK = os.getenv("CHANNEL_LINK")
GROUP_LINK = os.getenv("GROUP_LINK")

DB_NAME = os.getenv("DB_NAME", "bot.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

# =====================================================
# DATABASE
# =====================================================

db = sqlite3.connect(DB_NAME, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TEXT,
    last_seen TEXT,
    translations INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0
)
""")

db.commit()


def execute(query, params=()):
    cursor = db.execute(query, params)
    db.commit()
    return cursor


def register_user(user):
    now = datetime.now(timezone.utc).isoformat()

    execute("""
        INSERT INTO users (
            user_id, username, first_name,
            joined_at, last_seen
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_seen=excluded.last_seen
    """, (
        user.id,
        user.username,
        user.first_name,
        now,
        now
    ))


def is_banned(user_id):
    row = execute(
        "SELECT banned FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    return bool(row and row["banned"])


def set_ban(user_id, value):
    execute("""
        UPDATE users SET banned=?
        WHERE user_id=?
    """, (value, user_id))


def add_translation(user_id):
    execute("""
        UPDATE users
        SET translations=translations+1
        WHERE user_id=?
    """, (user_id,))


def get_stats():
    return execute("""
        SELECT
            COUNT(*) AS total,
            SUM(banned) AS banned,
            SUM(translations) AS translations
        FROM users
    """).fetchone()


# =====================================================
# ADMIN CHECK
# =====================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


# =====================================================
# FORCE JOIN
# =====================================================

def force_join_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 Join Channel",
                url=CHANNEL_LINK
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Join Group",
                url=GROUP_LINK
            )
        ],
        [
            InlineKeyboardButton(
                "✅ I've Joined – Verify",
                callback_data="verify_join"
            )
        ]
    ])


async def check_membership(bot, user_id):
    if not FORCE_CHANNEL or not FORCE_GROUP:
        logger.error("Force join IDs are not configured")
        return False

    try:
        for chat_id in (FORCE_CHANNEL, FORCE_GROUP):
            member = await bot.get_chat_member(
                chat_id=chat_id,
                user_id=user_id
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED
            ):
                return False

            if (
                member.status == ChatMemberStatus.RESTRICTED
                and not member.is_member
            ):
                return False

        return True

    except TelegramError as error:
        logger.warning(
            "Membership verification failed for %s: %s",
            user_id,
            error
        )
        return False


async def require_join(update, context):
    user_id = update.effective_user.id

    if is_admin(user_id):
        return True

    if await check_membership(context.bot, user_id):
        return True

    message = (
        "🔒 **Access Restricted**\n\n"
        "To use this translation bot, you must join "
        "both our channel and group.\n\n"
        "👇 Join both using the buttons below, "
        "then press Verify."
    )

    if update.callback_query:
        await update.callback_query.answer()

        await update.callback_query.message.reply_text(
            message,
            reply_markup=force_join_keyboard(),
            parse_mode="Markdown"
        )

    elif update.message:
        await update.message.reply_text(
            message,
            reply_markup=force_join_keyboard(),
            parse_mode="Markdown"
        )

    return False


# =====================================================
# START
# =====================================================

async def start(update, context):
    user = update.effective_user

    register_user(user)

    if is_banned(user.id):
        await update.message.reply_text(
            "🚫 You are banned from using this bot."
        )
        return

    if not await require_join(update, context):
        return

    await update.message.reply_text(
        "👋 **Welcome to Auto Translate Bot!**\n\n"
        "🌍 Send me text in ANY supported language.\n\n"
        "🇬🇧 I will automatically detect its language "
        "and translate it into English.\n\n"
        "⚡ No language selection required!\n\n"
        "📝 Just send your text to begin.\n\n"
        "❓ Use /help for instructions.",
        parse_mode="Markdown"
    )


# =====================================================
# HELP
# =====================================================

async def help_command(update, context):
    if not await require_join(update, context):
        return

    await update.message.reply_text(
        "📖 **How to use this bot**\n\n"
        "1️⃣ Send text in any language.\n"
        "2️⃣ The bot detects the language.\n"
        "3️⃣ The bot translates it into English.\n\n"
        "🌍 Automatic detection\n"
        "⚡ Fast translation\n"
        "🇬🇧 English output\n\n"
        "Simply send a message!",
        parse_mode="Markdown"
    )


# =====================================================
# TRANSLATION
# =====================================================

def translate_text(text):
    return GoogleTranslator(
        source="auto",
        target="en"
    ).translate(text)


async def translate_message(update, context):
    user = update.effective_user
    message = update.message

    if not user or not message:
        return

    register_user(user)

    if is_banned(user.id):
        await message.reply_text(
            "🚫 You are banned from using this bot."
        )
        return

    if not await require_join(update, context):
        return

    original = (message.text or "").strip()

    if not original:
        return

    if len(original) > 4500:
        await message.reply_text(
            "⚠️ Please send text containing no more "
            "than 4500 characters."
        )
        return

    status = await message.reply_text(
        "🌍 Translating into English..."
    )

    try:
        translated = await asyncio.to_thread(
            translate_text,
            original
        )

        if not translated:
            raise ValueError("Empty translation")

        add_translation(user.id)

        await status.edit_text(
            "🇬🇧 **English Translation**\n\n"
            + translated,
            parse_mode="Markdown"
        )

    except Exception:
        logger.exception("Translation error")

        await status.edit_text(
            "❌ Translation failed.\n"
            "Please try again later."
        )


# =====================================================
# VERIFY JOIN
# =====================================================

async def verify_join(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if is_banned(user_id):
        await query.answer(
            "You are banned.",
            show_alert=True
        )
        return

    if is_admin(user_id):
        await query.answer("Admin access granted!")
    elif await check_membership(context.bot, user_id):
        await query.answer(
            "Membership verified!",
            show_alert=True
        )

        await query.message.edit_text(
            "✅ **Verification successful!**\n\n"
            "You can now use the translation bot.\n\n"
            "🌍 Send me text in any language."
        )
        return
    else:
        await query.answer(
            "You must join both the channel and group!",
            show_alert=True
        )
        return

    await query.message.edit_text(
        "✅ Admin access granted."
    )


# =====================================================
# ADMIN PANEL
# =====================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            ),
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast"
            )
        ],
        [
            InlineKeyboardButton(
                "🚫 Ban User",
                callback_data="admin_ban"
            ),
            InlineKeyboardButton(
                "✅ Unban User",
                callback_data="admin_unban"
            )
        ]
    ])


async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    await update.message.reply_text(
        "🛠 **Translation Bot Admin Panel**\n\n"
        "Choose an operation below:",
        reply_markup=admin_keyboard(),
        parse_mode="Markdown"
    )


async def admin_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.answer("Unauthorized!", show_alert=True)
        return

    await query.answer()

    action = query.data

    if action == "admin_stats":
        stats = get_stats()

        await query.message.reply_text(
            "📊 **BOT STATISTICS**\n\n"
            f"👥 Total users: {stats['total'] or 0}\n"
            f"🚫 Banned users: {stats['banned'] or 0}\n"
            f"🌍 Successful translations: "
            f"{stats['translations'] or 0}",
            parse_mode="Markdown"
        )

    elif action == "admin_users":
        rows = execute("""
            SELECT user_id, username, first_name, banned
            FROM users
            ORDER BY joined_at DESC
            LIMIT 25
        """).fetchall()

        if not rows:
            await query.message.reply_text("No registered users.")
            return

        result = ["👥 **LATEST 25 USERS**\n"]

        for row in rows:
            name = row["first_name"] or "Unknown"
            status = "🚫 Banned" if row["banned"] else "✅ Active"

            result.append(
                f"• {name} | `{row['user_id']}` | {status}"
            )

        await query.message.reply_text(
            "\n".join(result),
            parse_mode="Markdown"
        )

    elif action == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"

        await query.message.reply_text(
            "📢 Send the message you want to broadcast.\n\n"
            "You can send text, photos, videos or documents.\n"
            "Send /cancel to cancel."
        )

    elif action == "admin_ban":
        context.user_data["admin_action"] = "ban"

        await query.message.reply_text(
            "🚫 Send the Telegram user ID to ban."
        )

    elif action == "admin_unban":
        context.user_data["admin_action"] = "unban"

        await query.message.reply_text(
            "✅ Send the Telegram user ID to unban."
        )


# =====================================================
# ADMIN USER INPUT
# =====================================================

async def admin_input(update, context):
    user = update.effective_user

    if not user:
        return

    # Only admin messages with an active panel action
    # are handled here.
    if not is_admin(user.id):
        await translate_message(update, context)
        return

    action = context.user_data.get("admin_action")

    if not action:
        if update.message and update.message.text:
            await translate_message(update, context)
        return

    if update.message.text and update.message.text.startswith("/cancel"):
        context.user_data.pop("admin_action", None)
        await update.message.reply_text("❌ Action cancelled.")
        return

    context.user_data.pop("admin_action", None)

    if action == "broadcast":
        users = execute("""
            SELECT user_id FROM users WHERE banned=0
        """).fetchall()

        success = 0
        failed = 0

        status = await update.message.reply_text(
            "📢 Broadcast started..."
        )

        for row in users:
            try:
                await update.message.copy(
                    chat_id=row["user_id"]
                )
                success += 1
                await asyncio.sleep(0.05)

            except RetryAfter as error:
                await asyncio.sleep(error.retry_after + 1)

                try:
                    await update.message.copy(
                        chat_id=row["user_id"]
                    )
                    success += 1
                except TelegramError:
                    failed += 1

            except TelegramError:
                failed += 1

        await status.edit_text(
            "📢 **Broadcast Completed**\n\n"
            f"✅ Successful: {success}\n"
            f"❌ Failed: {failed}\n"
            f"👥 Total targeted: {len(users)}",
            parse_mode="Markdown"
        )

    elif action in ("ban", "unban"):
        value = (update.message.text or "").strip()

        if not value.isdigit():
            await update.message.reply_text(
                "⚠️ Please send a valid numeric Telegram ID."
            )
            return

        target = int(value)

        if target in ADMIN_IDS:
            await update.message.reply_text(
                "⛔ You cannot ban an administrator."
            )
            return

        row = execute(
            "SELECT user_id FROM users WHERE user_id=?",
            (target,)
        ).fetchone()

        if not row:
            await update.message.reply_text(
                "❌ User not found in the database."
            )
            return

        if action == "ban":
            set_ban(target, 1)
            await update.message.reply_text(
                f"🚫 User `{target}` has been banned.",
                parse_mode="Markdown"
            )
        else:
            set_ban(target, 0)
            await update.message.reply_text(
                f"✅ User `{target}` has been unbanned.",
                parse_mode="Markdown"
            )


# =====================================================
# ADDITIONAL ADMIN COMMANDS
# =====================================================

async def ban_command(update, context):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /ban USER_ID"
        )
        return

    target = int(context.args[0])

    if target in ADMIN_IDS:
        await update.message.reply_text(
            "You cannot ban an administrator."
        )
        return

    row = execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (target,)
    ).fetchone()

    if not row:
        await update.message.reply_text("User not found.")
        return

    set_ban(target, 1)
    await update.message.reply_text(
        f"🚫 Banned user {target}"
    )


async def unban_command(update, context):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /unban USER_ID"
        )
        return

    target = int(context.args[0])

    if target in ADMIN_IDS:
        await update.message.reply_text(
            "Administrator access is protected."
        )
        return

    row = execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (target,)
    ).fetchone()

    if not row:
        await update.message.reply_text("User not found.")
        return

    set_ban(target, 0)
    await update.message.reply_text(
        f"✅ Unbanned user {target}"
    )


async def user_command(update, context):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /user USER_ID"
        )
        return

    target = int(context.args[0])

    row = execute(
        "SELECT * FROM users WHERE user_id=?",
        (target,)
    ).fetchone()

    if not row:
        await update.message.reply_text("User not found.")
        return

    await update.message.reply_text(
        "👤 **USER INFORMATION**\n\n"
        f"ID: `{row['user_id']}`\n"
        f"Name: {row['first_name'] or 'Unknown'}\n"
        f"Username: @{row['username'] or 'None'}\n"
        f"Translations: {row['translations']}\n"
        f"Banned: {'Yes' if row['banned'] else 'No'}\n"
        f"Joined: {row['joined_at']}",
        parse_mode="Markdown"
    )


async def stats_command(update, context):
    if not is_admin(update.effective_user.id):
        return

    stats = get_stats()

    await update.message.reply_text(
        "📊 **BOT STATISTICS**\n\n"
        f"👥 Total users: {stats['total'] or 0}\n"
        f"🚫 Banned: {stats['banned'] or 0}\n"
        f"🌍 Translations: {stats['translations'] or 0}",
        parse_mode="Markdown"
    )


# =====================================================
# ERROR HANDLER
# =====================================================

async def error_handler(update, context):
    logger.error(
        "Update error",
        exc_info=context.error
    )


# =====================================================
# MAIN
# =====================================================

def main():
    required = {
        "BOT_TOKEN": BOT_TOKEN,
        "FORCE_CHANNEL_ID": FORCE_CHANNEL,
        "FORCE_GROUP_ID": FORCE_GROUP,
        "CHANNEL_LINK": CHANNEL_LINK,
        "GROUP_LINK": GROUP_LINK,
    }

    missing = [
        name for name, value in required.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )

    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_IDS is missing.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(16)
        .build()
    )

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    # Admin commands
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("user", user_command))

    # Inline callbacks
    app.add_handler(
        CallbackQueryHandler(
            verify_join,
            pattern="^verify_join$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern="^admin_"
        )
    )

    # Admin action input and normal user translation
    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            admin_input
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Auto Translation Bot is starting...")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
