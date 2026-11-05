"""
Транспорт Telegram: один HTTP-вызов sendMessage и осмысленный разбор ответа.

Здесь нет ни бизнес-логики, ни работы с БД — только «отправь строку в чат и
скажи, что ответил Telegram». Решение, что делать с ответом, принимает диспетчер.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Обычный ответ приходит за доли секунды. Таймаут держит слот Celery-воркера,
# общего с письмами входа, поэтому долго ждать зависший запрос нельзя.
TIMEOUT_SECONDS = 5

# Описания, означающие «этому адресату писать бесполезно, пока он сам не вернётся».
_PERMANENT_MARKERS = (
    'bot was blocked by the user',
    'chat not found',
    'user is deactivated',
    'bot was kicked',
)


@dataclass(frozen=True)
class SendResult:
    ok: bool
    # Постоянный отказ: адресат недоступен, повторять бессмысленно —
    # привязку надо деактивировать.
    blocked: bool = False
    # Telegram просит подождать столько секунд (код 429).
    retry_after: int | None = None
    error: str = ''


def send_message(*, chat_id: int, text: str) -> SendResult:
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        # Локальная разработка без токена: не ходим в сеть, честно сообщаем.
        return SendResult(ok=False, error='TELEGRAM_BOT_TOKEN не задан')

    try:
        response = requests.post(
            f'{settings.TELEGRAM_API_BASE.rstrip("/")}/bot{token}/sendMessage',
            json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # Текст исключения requests содержит полный URL, а в URL — токен.
        # error уходит в last_error, то есть в БД и раздел «Уведомления».
        return SendResult(ok=False, error=str(exc).replace(token, '***'))

    try:
        payload = response.json()
    except ValueError:
        return SendResult(ok=False, error=f'HTTP {response.status_code}: не JSON')

    if payload.get('ok'):
        return SendResult(ok=True)

    description = str(payload.get('description', ''))
    retry_after = (payload.get('parameters') or {}).get('retry_after')
    if retry_after:
        return SendResult(ok=False, retry_after=int(retry_after), error=description)

    lowered = description.lower()
    blocked = any(marker in lowered for marker in _PERMANENT_MARKERS)
    return SendResult(ok=False, blocked=blocked, error=description)
