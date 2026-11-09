# broadcast.py — admin broadcast with confirmation and rate control
import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

from config import ADMIN_IDS, BROADCAST_CONCURRENCY, BROADCAST_DELAY
from database import get_all_active_user_ids, record_broadcast

logger = logging.getLogger(__name__)

_pending: dict[int, dict] = {}  # admin_id → {"message": Message, "text": str|None, ...}


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id not in ADMIN_IDS:
        return
    msg = update.message
    # We accept text or caption on media; media itself is forwarded later.
    if not msg.text and not msg.caption and not msg.photo and not msg.video and not msg.document:
        await msg.reply_text("Send `/broadcast <message>` or a media message with the command.")
        return

    admin_id = update.effective_user.id
    total = len(await get_all_active_user_ids())
    _pending[admin_id] = {"message": msg, "total": total}

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="bc:confirm")],
        [InlineKeyboardButton("❌ Cancel", callback_data="bc:cancel")],
    ])
    await msg.reply_text(
        f"📢 *Broadcast confirmation*\n\nRecipients: {total}\n\nSend this?",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not update.effective_user or update.effective_user.id not in ADMIN_IDS:
        return
    admin_id = update.effective_user.id

    if q.data == "bc:cancel":
        _pending.pop(admin_id, None)
        await q.edit_message_text("Broadcast cancelled.")
        return

    if q.data == "bc:confirm":
        entry = _pending.pop(admin_id, None)
        if not entry:
            await q.edit_message_text("No pending broadcast.")
            return
        await q.edit_message_text("📢 Broadcasting…")
        await _run_broadcast(context.bot, entry["message"], admin_id, q)


async def _safe_send(bot, user_id: int, message, sem: asyncio.Semaphore) -> bool:
    async with sem:
        await asyncio.sleep(BROADCAST_DELAY)
        try:
            # copy_message preserves media, captions, and formatting
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
            return True
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id,
                )
                return True
            except TelegramError:
                return False
        except TelegramError:
            return False


async def _run_broadcast(bot, message, admin_id: int, query) -> None:
    user_ids = await get_all_active_user_ids()
    total = len(user_ids)
    sem = asyncio.Semaphore(BROADCAST_CONCURRENCY)

    results = await asyncio.gather(
        *[_safe_send(bot, uid, message, sem) for uid in user_ids],
        return_exceptions=False,
    )
    success = sum(1 for r in results if r)
    failed = total - success

    await record_broadcast(admin_id, total, success, failed)

    await query.edit_message_text(
        "📢 *BROADCAST COMPLETED*\n\n"
        f"👥 Total recipients: {total}\n"
        f"✅ Successfully delivered: {success}\n"
        f"❌ Failed: {failed}",
        parse_mode="Markdown",
    )
