# admin.py — inline admin panel
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS, CHANNEL_LINK, FORCE_CHANNEL_ID, FORCE_GROUP_ID, GROUP_LINK
from database import get_stats, get_user, set_banned

logger = logging.getLogger(__name__)


def _admin_only(update: Update) -> bool:
    return update.effective_user and update.effective_user.id in ADMIN_IDS


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="adm:stats")],
        [InlineKeyboardButton("👥 Total Users", callback_data="adm:users")],
        [InlineKeyboardButton("🌍 Translation Stats", callback_data="adm:trstats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm:broadcast")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="adm:ban")],
        [InlineKeyboardButton("✅ Unban User", callback_data="adm:unban")],
        [InlineKeyboardButton("🔎 Search User", callback_data="adm:search")],
        [InlineKeyboardButton("👤 User Management", callback_data="adm:usermgmt")],
        [InlineKeyboardButton("⚙️ Force Join Settings", callback_data="adm:fj")],
        [InlineKeyboardButton("🔄 Refresh Statistics", callback_data="adm:stats")],
    ])


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text(
        "🛠 *Admin Dashboard*", reply_markup=admin_keyboard(), parse_mode=ParseMode.MARKDOWN
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not _admin_only(update):
        await q.edit_message_text("Unauthorized.")
        return

    data = q.data

    if data == "adm:stats":
        s = await get_stats()
        text = (
            "📊 *BOT STATISTICS*\n\n"
            f"👥 Total registered users: {s['total_users']}\n"
            f"🟢 Active users: {s['active_users']}\n"
            f"🚫 Banned users: {s['banned_users']}\n"
            f"🌍 Total successful translations: {s['total_translations']}\n"
            f"📅 Today’s translations: {s['today_translations']}\n"
            f"📈 Today’s new users: {s['today_new_users']}\n"
            f"📢 Total broadcasts: {s['total_broadcasts']}\n"
            f"❌ Failed translations: {s['failed_translations']}"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())

    elif data == "adm:users":
        s = await get_stats()
        await q.edit_message_text(
            f"👥 Total registered users: {s['total_users']}",
            reply_markup=admin_keyboard(),
        )

    elif data == "adm:trstats":
        s = await get_stats()
        await q.edit_message_text(
            f"🌍 Successful: {s['total_translations']}\n"
            f"📅 Today: {s['today_translations']}\n"
            f"❌ Failed: {s['failed_translations']}",
            reply_markup=admin_keyboard(),
        )

    elif data == "adm:fj":
        await q.edit_message_text(
            "⚙️ *Force Join Settings*\n\n"
            f"Channel ID: `{FORCE_CHANNEL_ID or 'not set'}`\n"
            f"Channel Link: {CHANNEL_LINK or 'not set'}\n"
            f"Group ID: `{FORCE_GROUP_ID or 'not set'}`\n"
            f"Group Link: {GROUP_LINK or 'not set'}\n\n"
            "Update these via Heroku config vars.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_keyboard(),
        )

    elif data == "adm:ban":
        await q.edit_message_text(
            "Send `/ban USER_ID` to ban a user.\n\n"
            "Or reply to a user’s message with `/ban`.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_keyboard(),
        )

    elif data == "adm:unban":
        await q.edit_message_text(
            "Send `/unban USER_ID` to unban a user.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_keyboard(),
        )

    elif data == "adm:search":
        await q.edit_message_text(
            "Send `/user USER_ID` to look up a user.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_keyboard(),
        )

    elif data == "adm:broadcast":
        await q.edit_message_text(
            "Send `/broadcast` followed by your message to start a broadcast.\n"
            "You will see a confirmation before sending.",
            reply_markup=admin_keyboard(),
        )

    elif data == "adm:usermgmt":
        await q.edit_message_text(
            "Use `/user USER_ID`, `/ban USER_ID`, `/unban USER_ID`.",
            reply_markup=admin_keyboard(),
        )

    else:
        await q.edit_message_text("Unknown action.", reply_markup=admin_keyboard())


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/ban USER_ID`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    if uid in ADMIN_IDS:
        await update.message.reply_text("Cannot ban an administrator.")
        return
    user = await get_user(uid)
    if not user:
        await update.message.reply_text("User not found.")
        return
    await set_banned(uid, True)
    await update.message.reply_text(f"🚫 User `{uid}` banned.", parse_mode=ParseMode.MARKDOWN)


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/unban USER_ID`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    user = await get_user(uid)
    if not user:
        await update.message.reply_text("User not found.")
        return
    await set_banned(uid, False)
    await update.message.reply_text(f"✅ User `{uid}` unbanned.", parse_mode=ParseMode.MARKDOWN)


async def user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/user USER_ID`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    u = await get_user(uid)
    if not u:
        await update.message.reply_text("User not found.")
        return
    await update.message.reply_text(
        f"👤 *User Info*\n\n"
        f"ID: `{u['user_id']}`\n"
        f"Username: @{u['username'] or '—'}\n"
        f"First name: {u['first_name'] or '—'}\n"
        f"Registered: {u['registered_at']}\n"
        f"Last activity: {u['last_activity']}\n"
        f"Translations: {u['translations']}\n"
        f"Banned: {'🚫 Yes' if u['banned'] else '✅ No'}",
        parse_mode=ParseMode.MARKDOWN,
    )
