"""/start, /help и ответ на неизвестную команду."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot.utils import formatters


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != 'private':
        return
    await update.message.reply_text(formatters.start(update.effective_user.full_name))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != 'private':
        return
    await update.message.reply_text(formatters.help_text())


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != 'private':
        return
    await update.message.reply_text(formatters.help_text())
