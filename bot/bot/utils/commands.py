"""
Управление меню команд в Telegram.

Ролевых меню больше нет — у всех пользователей одно и то же меню из трёх команд.
"""
from __future__ import annotations

import logging

from telegram import Bot, BotCommand, BotCommandScopeAllGroupChats
from telegram.ext import Application

logger = logging.getLogger(__name__)

COMMANDS: list[BotCommand] = [
    BotCommand('start', 'Начать работу с ботом'),
    BotCommand('help', 'Список команд'),
    BotCommand('my', 'Мои ближайшие занятия'),
]


async def set_commands(application: Application) -> None:
    """post_init-хук: выставляет единое меню команд при старте бота."""
    bot: Bot = application.bot

    try:
        await bot.set_my_commands(commands=[], scope=BotCommandScopeAllGroupChats())
    except Exception as exc:
        logger.warning('Не удалось очистить команды в группах: %s', exc)

    try:
        await bot.set_my_commands(commands=COMMANDS)
        logger.info('Меню команд установлено (%d команд)', len(COMMANDS))
    except Exception as exc:
        logger.warning('Не удалось установить меню команд: %s', exc)
