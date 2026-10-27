"""
Точка входа. Бот только отвечает на три команды и сообщает журналу о контактах;
напоминания рассылает журнал напрямую через Telegram Bot API.
"""
from __future__ import annotations

import logging

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.config import BOT_TOKEN, setup_logging
from bot.handlers.common import help_command, start, unknown
from bot.handlers.identify import identify
from bot.handlers.my import my
from bot.utils.commands import set_commands

logger = logging.getLogger(__name__)


def build_application():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(set_commands).build()

    # group=-1: срабатывает раньше команд и им не мешает.
    app.add_handler(MessageHandler(filters.ALL, identify), group=-1)

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('my', my))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    return app


def main() -> None:
    setup_logging()
    logger.info('Бот запускается')
    build_application().run_polling()


if __name__ == '__main__':
    main()
