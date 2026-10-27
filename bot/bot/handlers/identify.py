"""
На любое входящее сообщение сообщаем журналу, какому нику соответствует chat_id.

Так журнал накапливает справочник аккаунтов, из которого админ выбирает при
привязке, — и админу не приходится набирать ник руками.

Группа хендлеров отдельная (group=-1), чтобы это срабатывало ДО обработки
команды и не мешало ей.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot import journal_api


async def identify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.effective_chat.type != 'private':
        return
    journal_api.identify(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )
