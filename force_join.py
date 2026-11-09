# force_join.py — mandatory channel + group membership
import logging
from typing import Tuple

from telegram import Bot, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import ADMIN_IDS, CHANNEL_LINK, FORCE_CHANNEL_ID, FORCE_GROUP_ID, GROUP_LINK

logger = logging.getLogger(__name__)

_OK_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}


def force_join_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if CHANNEL_LINK:
        rows.append([InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)])
    if GROUP_LINK:
        rows.append([InlineKeyboardButton("👥 Join Group", url=GROUP_LINK)])
    rows.append([InlineKeyboardButton("✅ I’ve Joined – Verify", callback_data="fj:verify")])
    return InlineKeyboardMarkup(rows)


async def _is_member(bot: Bot, chat_id: int, user_id: int) -> Tuple[bool, str]:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramError as e:
        logger.warning("getChatMember failed for chat %s user %s: %s", chat_id, user_id, e)
        return False, f"error:{e.__class__.__name__}"
    if member.status in _OK_STATUSES:
        return True, "ok"
    return False, member.status


async def check_force_join(bot: Bot, user_id: int) -> Tuple[bool, str]:
    """
    Returns (allowed, reason).
    Admins bypass. Both destinations must confirm independently.
    """
    if user_id in ADMIN_IDS:
        return True, "admin"

    missing = []

    if FORCE_CHANNEL_ID is not None:
        ok, status = await _is_member(bot, FORCE_CHANNEL_ID, user_id)
        if not ok:
            missing.append("channel")

    if FORCE_GROUP_ID is not None:
        ok, status = await _is_member(bot, FORCE_GROUP_ID, user_id)
        if not ok:
            missing.append("group")

    if not missing:
        return True, "ok"

    if len(missing) == 2:
        return False, "both"
    return False, missing[0]
