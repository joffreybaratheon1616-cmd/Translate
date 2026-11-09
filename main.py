#!/usr/bin/env python3
"""
Telegram Auto Translation Bot (No Database)
Production-ready async bot with force-join, admin panel, broadcast, ban system.
All state is kept in memory (resets on restart).
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import (
    TelegramError, Forbidden, BadRequest, RetryAfter,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

from deep_translator import GoogleTranslator
from deep_translator.exceptions import (
    LanguageNotSupportedException, NotValidPayload, TranslationNotFound,
)

import config

# -------------------- Logging --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# -------------------- In-Memory State --------------------
# These reset when the process restarts.

# user_id -> {"username", "first_name", "last_name", "joined_at", "last_activity", "translations"}
users: Dict[int, Dict[str, Any]] = {}

# Set of banned user_ids
banned_users: Set[int] = set()

# Runtime counters
stats = {
    "total_translations": 0,
    "failed_translations": 0,
    "total_broadcasts": 0,
    "started_at": datetime.now(timezone.utc),
}

# Pending broadcast data: admin_id -> message payload
pending_broadcasts: Dict[int, Dict[str, Any]] = {}


# -------------------- Helpers --------------------

def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def register_user(user) -> Dict[str, Any]:
    """Register or update a user in memory."""
    uid = user.id
    now = datetime.now(timezone.utc)
    if uid not in users:
        users[uid] = {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "joined_at": now,
            "last_activity": now,
            "translations": 0,
        }
        logger.info(f"New user registered: {uid} (@{user.username})")
    else:
        users[uid]["username"] = user.username
        users[uid]["first_name"] = user.first_name
        users[uid]["last_name"] = user.last_name
        users[uid]["last_activity"] = now
    return users[uid]


def is_banned(user_id: int) -> bool:
    return user_id in banned_users


def ban_user(user_id: int) -> bool:
    if user_id in config.ADMIN_IDS:
        return False
    banned_users.add(user_id)
    return True


def unban_user(user_id: int) -> bool:
    if user_id in banned_users:
        banned_users.discard(user_id)
        return True
    return False


def get_non_banned_user_ids() -> List[int]:
    return [uid for uid in users if uid not in banned_users]


async def check_membership(bot, user_id: int) -> Dict[str, bool]:
    """Check if user is member of required channel and group."""
    result = {"channel": True, "group": True}

    if config.FORCE_CHANNEL_ID:
        try:
            member = await bot.get_chat_member(config.FORCE_CHANNEL_ID, user_id)
            result["channel"] = member.status in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
                ChatMemberStatus.RESTRICTED,
            )
        except (TelegramError, Forbidden, BadRequest) as e:
            logger.warning(f"Channel membership check failed for {user_id}: {e}")
            result["channel"] = False

    if config.FORCE_GROUP_ID:
        try:
            member = await bot.get_chat_member(config.FORCE_GROUP_ID, user_id)
            result["group"] = member.status in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
                ChatMemberStatus.RESTRICTED,
            )
        except (TelegramError, Forbidden, BadRequest) as e:
            logger.warning(f"Group membership check failed for {user_id}: {e}")
            result["group"] = False

    return result


def force_join_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    if config.CHANNEL_LINK and config.FORCE_CHANNEL_ID:
        buttons.append([InlineKeyboardButton("📢 Join Channel", url=config.CHANNEL_LINK)])
    if config.GROUP_LINK and config.FORCE_GROUP_ID:
        buttons.append([InlineKeyboardButton("👥 Join Group", url=config.GROUP_LINK)])
    buttons.append([InlineKeyboardButton("✅ I’ve Joined – Verify", callback_data="verify_join")])
    return InlineKeyboardMarkup(buttons)


def admin_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📊 Statistics", callback_data="admin:stats"),
            InlineKeyboardButton("👥 Total Users", callback_data="admin:users"),
        ],
        [
            InlineKeyboardButton("🌍 Translation Stats", callback_data="admin:trans_stats"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcast"),
        ],
        [
            InlineKeyboardButton("🚫 Ban User", callback_data="admin:ban"),
            InlineKeyboardButton("✅ Unban User", callback_data="admin:unban"),
        ],
        [
            InlineKeyboardButton("🔎 Search User", callback_data="admin:search"),
            InlineKeyboardButton("👤 User Management", callback_data="admin:usermgmt"),
        ],
        [
            InlineKeyboardButton("⚙️ Force Join Settings", callback_data="admin:forcejoin"),
            InlineKeyboardButton("🔄 Refresh Statistics", callback_data="admin:refresh"),
        ],
        [InlineKeyboardButton("❌ Close", callback_data="admin:close")],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_to_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin:panel")]
    ])


async def require_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if user is allowed (joined or admin). Otherwise send force-join message."""
    user = update.effective_user
    if not user:
        return False

    if is_admin(user.id):
        return True

    membership = await check_membership(context.bot, user.id)
    if membership["channel"] and membership["group"]:
        return True

    missing = []
    if not membership["channel"] and config.FORCE_CHANNEL_ID:
        missing.append("channel")
    if not membership["group"] and config.FORCE_GROUP_ID:
        missing.append("group")

    text = (
        "🔒 <b>ACCESS REQUIRED</b>\n\n"
        "To use this bot, you must join our official channel and group.\n\n"
        "Please join both destinations below and verify your membership."
    )
    if missing:
        text += f"\n\n⚠️ Still missing: <b>{' and '.join(missing)}</b>"

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=force_join_keyboard(), parse_mode=ParseMode.HTML
            )
        except BadRequest:
            await update.callback_query.message.reply_text(
                text, reply_markup=force_join_keyboard(), parse_mode=ParseMode.HTML
            )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=force_join_keyboard(), parse_mode=ParseMode.HTML
        )
    return False


def split_text(text: str, max_len: int = config.TRANSLATION_CHUNK_SIZE) -> List[str]:
    """Split long text into chunks without breaking mid-word if possible."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = max_len
        for sep in ("\n\n", "\n", ". ", " "):
            pos = text.rfind(sep, 0, max_len)
            if pos > max_len // 2:
                split_at = pos + len(sep)
                break
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return [c for c in chunks if c]


async def translate_text(text: str) -> Optional[str]:
    """Translate text to English. Handles long messages by chunking."""
    if not text or not text.strip():
        return None

    text = text.strip()
    try:
        chunks = split_text(text)
        translated_parts = []
        loop = asyncio.get_running_loop()
        for chunk in chunks:
            part = await loop.run_in_executor(
                None,
                lambda c=chunk: GoogleTranslator(source="auto", target="en").translate(c),
            )
            if part:
                translated_parts.append(part)
        return "\n".join(translated_parts) if translated_parts else None
    except (LanguageNotSupportedException, NotValidPayload, TranslationNotFound) as e:
        logger.warning(f"Translation language issue: {e}")
        return None
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return None


# -------------------- Command Handlers --------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    register_user(user)

    if is_banned(user.id) and not is_admin(user.id):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    if not await require_force_join(update, context):
        return

    welcome = (
        "👋 <b>Welcome to Auto Translation Bot!</b>\n\n"
        "🌍 I can automatically translate text from almost any language into English.\n\n"
        "📌 <b>How to use:</b>\n"
        "Simply send me any text in any language.\n\n"
        "⚡ I will automatically detect the language and translate it into English.\n\n"
        "🚀 <b>No commands or language selection required!</b>"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.HTML)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("⛔ Access denied. Admins only.")
        return

    register_user(user)
    text = "🛠 <b>ADMIN PANEL</b>\n\nSelect an option below:"
    await update.message.reply_text(text, reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /ban USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        return

    if target_id in config.ADMIN_IDS:
        await update.message.reply_text("⚠️ Cannot ban an administrator.")
        return

    ban_user(target_id)
    await update.message.reply_text(
        f"✅ User <code>{target_id}</code> has been banned.",
        parse_mode=ParseMode.HTML,
    )
    logger.info(f"Admin {user.id} banned {target_id}")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /unban USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    if unban_user(target_id):
        await update.message.reply_text(
            f"✅ User <code>{target_id}</code> has been unbanned.",
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"Admin {user.id} unbanned {target_id}")
    else:
        await update.message.reply_text(f"ℹ️ User {target_id} was not banned.")


async def user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /user USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    u = users.get(target_id)
    if not u:
        status = "🚫 Banned" if is_banned(target_id) else "❓ Not seen yet"
        await update.message.reply_text(
            f"User <code>{target_id}</code>\nStatus: {status}\n"
            "(User has not interacted with the bot since last restart)",
            parse_mode=ParseMode.HTML,
        )
        return

    status = "🚫 Banned" if is_banned(target_id) else "✅ Active"
    text = (
        f"👤 <b>User Information</b>\n\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"👤 Username: @{u.get('username') or 'N/A'}\n"
        f"📛 Name: {u.get('first_name') or ''} {u.get('last_name') or ''}\n"
        f"📅 First seen: {u['joined_at'].strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"🕒 Last activity: {u['last_activity'].strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"🌍 Translations: {u.get('translations', 0)}\n"
        f"📌 Status: {status}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        context.user_data.clear()
        pending_broadcasts.pop(user.id, None)
    await update.message.reply_text("Cancelled.")


# -------------------- Message Handler (Auto Translate) --------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return

    if message.text.startswith("/"):
        return

    register_user(user)

    if is_banned(user.id) and not is_admin(user.id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return

    if not await require_force_join(update, context):
        return

    text = message.text.strip()
    if not text:
        await message.reply_text("⚠️ Please send some text to translate.")
        return

    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    except Exception:
        pass

    translated = await translate_text(text)

    if translated is None:
        stats["failed_translations"] += 1
        await message.reply_text(
            "❌ Translation failed. The text may be in an unsupported language "
            "or the translation service is temporarily unavailable. Please try again."
        )
        return

    response = f"🇬🇧 <b>English Translation</b>\n\n{translated}"

    if len(response) > config.MAX_MESSAGE_LENGTH:
        header = "🇬🇧 <b>English Translation</b>\n\n"
        remaining = translated
        first = True
        while remaining:
            if first:
                chunk = remaining[: config.MAX_MESSAGE_LENGTH - len(header) - 50]
                await message.reply_text(header + chunk, parse_mode=ParseMode.HTML)
                remaining = remaining[len(chunk):]
                first = False
            else:
                chunk = remaining[: config.MAX_MESSAGE_LENGTH - 50]
                await message.reply_text(chunk, parse_mode=ParseMode.HTML)
                remaining = remaining[len(chunk):]
    else:
        await message.reply_text(response, parse_mode=ParseMode.HTML)

    stats["total_translations"] += 1
    if user.id in users:
        users[user.id]["translations"] = users[user.id].get("translations", 0) + 1


# -------------------- Callback Query Handler --------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    user = query.from_user
    data = query.data

    try:
        await query.answer()
    except Exception:
        pass

    # ---- Verify Join ----
    if data == "verify_join":
        membership = await check_membership(context.bot, user.id)
        if membership["channel"] and membership["group"]:
            await query.edit_message_text(
                "✅ <b>Access Granted!</b>\n\n"
                "You have successfully joined both destinations.\n"
                "You can now send any text for automatic English translation.",
                parse_mode=ParseMode.HTML,
            )
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    "👋 <b>Welcome to Auto Translation Bot!</b>\n\n"
                    "Simply send me any text in any language and I will translate it to English."
                ),
                parse_mode=ParseMode.HTML,
            )
        else:
            missing = []
            if not membership["channel"] and config.FORCE_CHANNEL_ID:
                missing.append("📢 Channel")
            if not membership["group"] and config.FORCE_GROUP_ID:
                missing.append("👥 Group")
            await query.edit_message_text(
                f"🔒 <b>Still Missing Access</b>\n\n"
                f"Please join the following and try again:\n"
                f"{' • '.join(missing)}\n\n"
                f"After joining, press the verify button again.",
                reply_markup=force_join_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        return

    # ---- Admin callbacks ----
    if data.startswith("admin:"):
        if not is_admin(user.id):
            await query.edit_message_text("⛔ Access denied.")
            return

        action = data[6:]  # after "admin:"

        if action in ("panel", "refresh"):
            text = "🛠 <b>ADMIN PANEL</b>\n\nSelect an option below:"
            await query.edit_message_text(text, reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
            return

        if action == "close":
            await query.edit_message_text("Admin panel closed.")
            return

        if action in ("stats", "trans_stats"):
            uptime = datetime.now(timezone.utc) - stats["started_at"]
            hours, rem = divmod(int(uptime.total_seconds()), 3600)
            minutes = rem // 60
            text = (
                "📊 <b>BOT STATISTICS</b> <i>(in-memory)</i>\n\n"
                f"👥 Known users (since start): <b>{len(users)}</b>\n"
                f"🚫 Banned users: <b>{len(banned_users)}</b>\n"
                f"🌍 Total successful translations: <b>{stats['total_translations']}</b>\n"
                f"❌ Failed translations: <b>{stats['failed_translations']}</b>\n"
                f"📢 Total broadcasts: <b>{stats['total_broadcasts']}</b>\n"
                f"⏱ Uptime: <b>{hours}h {minutes}m</b>\n\n"
                "<i>Note: All counters reset when the bot restarts.</i>"
            )
            await query.edit_message_text(text, reply_markup=back_to_admin_keyboard(), parse_mode=ParseMode.HTML)
            return

        if action == "users":
            total = len(users)
            banned_count = len(banned_users)
            lines = [
                f"👥 <b>Known Users: {total}</b> (Banned: {banned_count})\n"
                f"<i>Only users seen since last restart are listed.</i>\n"
            ]
            recent = sorted(users.items(), key=lambda x: x[1]["last_activity"], reverse=True)[:15]
            for uid, u in recent:
                status = "🚫" if is_banned(uid) else "✅"
                uname = f"@{u['username']}" if u.get("username") else "—"
                lines.append(
                    f"{status} <code>{uid}</code> | {uname} | "
                    f"{u.get('first_name') or ''} | 🌍{u.get('translations', 0)}"
                )
            if total > 15:
                lines.append(f"\n... and {total - 15} more. Use /user ID for details.")
            text = "\n".join(lines)
            await query.edit_message_text(text, reply_markup=back_to_admin_keyboard(), parse_mode=ParseMode.HTML)
            return

        if action == "broadcast":
            pending_broadcasts.pop(user.id, None)
            text = (
                "📢 <b>BROADCAST MODE</b>\n\n"
                "Send me the message you want to broadcast to all known users.\n"
                "Supported: Text, Photo, Video, Document (with caption).\n\n"
                "After sending, you will be asked to confirm.\n"
                "Send /cancel to abort.\n\n"
                "<i>Note: Only users who have interacted since the bot started will receive the message.</i>"
            )
            context.user_data["awaiting_broadcast"] = True
            await query.edit_message_text(text, reply_markup=back_to_admin_keyboard(), parse_mode=ParseMode.HTML)
            return

        if action == "ban":
            context.user_data["awaiting_ban"] = True
            await query.edit_message_text(
                "🚫 <b>Ban User</b>\n\nSend the Telegram User ID to ban.\nSend /cancel to abort.",
                reply_markup=back_to_admin_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return

        if action == "unban":
            context.user_data["awaiting_unban"] = True
            await query.edit_message_text(
                "✅ <b>Unban User</b>\n\nSend the Telegram User ID to unban.\nSend /cancel to abort.",
                reply_markup=back_to_admin_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return

        if action == "search":
            context.user_data["awaiting_search"] = True
            await query.edit_message_text(
                "🔎 <b>Search User</b>\n\nSend a Telegram User ID or username (without @).\nSend /cancel to abort.",
                reply_markup=back_to_admin_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return

        if action == "usermgmt":
            text = (
                "👤 <b>User Management</b>\n\n"
                "Available commands:\n"
                "• /user USER_ID — View user details\n"
                "• /ban USER_ID — Ban a user\n"
                "• /unban USER_ID — Unban a user\n\n"
                "Or use the Ban / Unban / Search buttons.\n\n"
                "<i>Data is in-memory and resets on restart.</i>"
            )
            await query.edit_message_text(text, reply_markup=back_to_admin_keyboard(), parse_mode=ParseMode.HTML)
            return

        if action == "forcejoin":
            ch = config.FORCE_CHANNEL_ID or "Not set"
            gr = config.FORCE_GROUP_ID or "Not set"
            text = (
                "⚙️ <b>Force Join Settings</b>\n\n"
                f"📢 Channel ID: <code>{ch}</code>\n"
                f"🔗 Channel Link: {config.CHANNEL_LINK}\n\n"
                f"👥 Group ID: <code>{gr}</code>\n"
                f"🔗 Group Link: {config.GROUP_LINK}\n\n"
                "These values are loaded from environment variables.\n"
                "To change them, update the config vars and restart the bot."
            )
            await query.edit_message_text(text, reply_markup=back_to_admin_keyboard(), parse_mode=ParseMode.HTML)
            return

    # ---- Broadcast confirmation ----
    if data.startswith("confirm_bc:"):
        if not is_admin(user.id):
            return
        action = data[11:]
        if action == "yes":
            bc_data = pending_broadcasts.get(user.id)
            if not bc_data:
                await query.edit_message_text("⚠️ No pending broadcast found. Please start again.")
                return
            await query.edit_message_text("⏳ Starting broadcast... This may take a while.")
            asyncio.create_task(run_broadcast(context.bot, user.id, bc_data, query.message.chat_id))
            pending_broadcasts.pop(user.id, None)
        else:
            pending_broadcasts.pop(user.id, None)
            await query.edit_message_text("❌ Broadcast cancelled.")
        return


# -------------------- Admin Input Handlers --------------------

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text/media input when admin is in a special state. Returns True if handled."""
    user = update.effective_user
    message = update.effective_message
    if not user or not is_admin(user.id) or not message:
        return False

    # Broadcast content capture
    if context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        bc = {
            "type": "text",
            "text": message.text or message.caption,
            "photo": None,
            "video": None,
            "document": None,
        }
        if message.photo:
            bc["type"] = "photo"
            bc["photo"] = message.photo[-1].file_id
            bc["text"] = message.caption
        elif message.video:
            bc["type"] = "video"
            bc["video"] = message.video.file_id
            bc["text"] = message.caption
        elif message.document:
            bc["type"] = "document"
            bc["document"] = message.document.file_id
            bc["text"] = message.caption

        pending_broadcasts[user.id] = bc
        recipients = len(get_non_banned_user_ids())
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm Broadcast", callback_data="confirm_bc:yes"),
                InlineKeyboardButton("❌ Cancel", callback_data="confirm_bc:no"),
            ]
        ])
        preview = (bc.get("text") or "[Media]")[:200]
        await message.reply_text(
            f"📢 <b>Confirm Broadcast</b>\n\n"
            f"Type: <b>{bc['type']}</b>\n"
            f"Recipients: <b>{recipients}</b> known non-banned users\n"
            f"Preview:\n<code>{preview}</code>\n\n"
            f"Are you sure you want to send this?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return True

    if context.user_data.get("awaiting_ban"):
        context.user_data["awaiting_ban"] = False
        try:
            target_id = int(message.text.strip())
        except (ValueError, AttributeError):
            await message.reply_text("❌ Invalid ID. Please send a numeric Telegram user ID.")
            return True
        if target_id in config.ADMIN_IDS:
            await message.reply_text("⚠️ Cannot ban an administrator.")
            return True
        ban_user(target_id)
        await message.reply_text(f"✅ User <code>{target_id}</code> banned.", parse_mode=ParseMode.HTML)
        return True

    if context.user_data.get("awaiting_unban"):
        context.user_data["awaiting_unban"] = False
        try:
            target_id = int(message.text.strip())
        except (ValueError, AttributeError):
            await message.reply_text("❌ Invalid ID.")
            return True
        if unban_user(target_id):
            await message.reply_text(f"✅ User <code>{target_id}</code> unbanned.", parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(f"ℹ️ User {target_id} was not banned.")
        return True

    if context.user_data.get("awaiting_search"):
        context.user_data["awaiting_search"] = False
        query = (message.text or "").strip().lstrip("@")
        results = []
        if query.isdigit():
            uid = int(query)
            if uid in users:
                results.append((uid, users[uid]))
            elif is_banned(uid):
                results.append((uid, {"username": None, "first_name": "?", "translations": 0}))
        else:
            q = query.lower()
            for uid, u in users.items():
                if (u.get("username") and q in u["username"].lower()) or \
                   (u.get("first_name") and q in (u["first_name"] or "").lower()):
                    results.append((uid, u))
                    if len(results) >= 10:
                        break

        if not results:
            await message.reply_text("No users found (remember: only users seen since last restart).")
            return True

        lines = ["🔎 <b>Search Results</b>\n"]
        for uid, u in results:
            status = "🚫" if is_banned(uid) else "✅"
            lines.append(
                f"{status} <code>{uid}</code> | @{u.get('username') or '—'} | "
                f"{u.get('first_name') or ''} | 🌍{u.get('translations', 0)}"
            )
        await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return True

    return False


# -------------------- Broadcast Runner --------------------

async def run_broadcast(bot, admin_id: int, bc_data: dict, chat_id: int):
    recipients = get_non_banned_user_ids()
    total = len(recipients)
    success = 0
    failed = 0

    progress_msg = await bot.send_message(
        chat_id=chat_id,
        text=f"📢 Broadcasting to {total} users...\n✅ 0 | ❌ 0",
    )

    semaphore = asyncio.Semaphore(config.BROADCAST_CONCURRENCY)

    async def send_one(uid: int):
        nonlocal success, failed
        async with semaphore:
            try:
                if bc_data["type"] == "text":
                    await bot.send_message(chat_id=uid, text=bc_data["text"], parse_mode=ParseMode.HTML)
                elif bc_data["type"] == "photo":
                    await bot.send_photo(
                        chat_id=uid, photo=bc_data["photo"],
                        caption=bc_data.get("text"), parse_mode=ParseMode.HTML,
                    )
                elif bc_data["type"] == "video":
                    await bot.send_video(
                        chat_id=uid, video=bc_data["video"],
                        caption=bc_data.get("text"), parse_mode=ParseMode.HTML,
                    )
                elif bc_data["type"] == "document":
                    await bot.send_document(
                        chat_id=uid, document=bc_data["document"],
                        caption=bc_data.get("text"), parse_mode=ParseMode.HTML,
                    )
                success += 1
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                try:
                    if bc_data["type"] == "text":
                        await bot.send_message(chat_id=uid, text=bc_data["text"])
                    success += 1
                except Exception:
                    failed += 1
            except (Forbidden, BadRequest):
                failed += 1
            except Exception as e:
                logger.warning(f"Broadcast to {uid} failed: {e}")
                failed += 1
            await asyncio.sleep(config.BROADCAST_DELAY)

    batch_size = 25
    for i in range(0, total, batch_size):
        batch = recipients[i : i + batch_size]
        await asyncio.gather(*(send_one(uid) for uid in batch))
        try:
            await progress_msg.edit_text(
                f"📢 Broadcasting to {total} users...\n"
                f"✅ {success} | ❌ {failed}\n"
                f"Progress: {min(i + batch_size, total)}/{total}"
            )
        except Exception:
            pass

    stats["total_broadcasts"] += 1

    summary = (
        f"📢 <b>BROADCAST COMPLETED</b>\n\n"
        f"👥 Total recipients: <b>{total}</b>\n"
        f"✅ Successfully delivered: <b>{success}</b>\n"
        f"❌ Failed: <b>{failed}</b>"
    )
    try:
        await progress_msg.edit_text(summary, parse_mode=ParseMode.HTML)
    except Exception:
        await bot.send_message(chat_id=chat_id, text=summary, parse_mode=ParseMode.HTML)

    logger.info(f"Broadcast by {admin_id}: {success}/{total} success")


# -------------------- Combined Message Router --------------------

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await handle_admin_input(update, context):
        return
    await handle_message(update, context)


# -------------------- Error Handler --------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(context.error, RetryAfter):
        logger.warning(f"Flood control exceeded. Retry in {context.error.retry_after}s")


# -------------------- Main --------------------

def main():
    logger.info("Starting Auto Translation Bot (no database)...")

    if not config.BOT_TOKEN:
        logger.critical("BOT_TOKEN is missing!")
        sys.exit(1)
    if not config.ADMIN_IDS:
        logger.warning("No ADMIN_IDS configured. Admin panel will be inaccessible.")

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("user", user_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL)
            & ~filters.COMMAND,
            message_router,
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Bot is running with polling...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
