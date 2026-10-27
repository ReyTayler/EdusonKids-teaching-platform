"""
Настройки бота из окружения и логирование.

Ролей и собственного хранилища у бота больше нет — кому слать уведомления,
решает журнал. Здесь остаётся только то, что нужно самому боту: токен,
таймзона (для отображения времени) и уровень логирования.
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f'Обязательная переменная окружения не задана: {key}')
    return value


BOT_TOKEN: str = _require('BOT_TOKEN')

TIMEZONE: str = os.getenv('TIMEZONE', 'Europe/Moscow')

LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO').upper()


def setup_logging() -> None:
    """Настраивает логирование в stdout (Docker собирает и ротирует логи сам)."""
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    fmt = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    handlers = [logging.StreamHandler()]
    logging.basicConfig(level=level, format=fmt, handlers=handlers)

    # httpx на уровне INFO печатает полный URL каждого запроса, а токен бота —
    # часть URL обращений к api.telegram.org. То есть при LOG_LEVEL=INFO секрет
    # утекает в логи контейнера целиком. Глушим до WARNING: ошибки
    # запросов по-прежнему видны, а рутинные обращения — нет.
    logging.getLogger('httpx').setLevel(logging.WARNING)
