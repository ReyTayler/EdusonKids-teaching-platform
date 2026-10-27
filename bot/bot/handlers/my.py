"""/my — ближайшие занятия преподавателя из журнала."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot import journal_api
from bot.utils import formatters


async def my(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != 'private':
        return  # в группах бот команды игнорирует
    payload = journal_api.my_events(telegram_id=update.effective_user.id)
    if payload is None:
        await update.message.reply_text(formatters.not_linked())
        return
    await update.message.reply_text(formatters.my_events(payload))
