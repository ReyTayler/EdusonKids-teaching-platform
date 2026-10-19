"""
Транспорт email: одно письмо через django.core.mail и разбор исхода.

Как и telegram.py, здесь нет ни бизнес-логики, ни работы с БД — только «отправь
письмо и скажи, чем кончилось». Решение, что делать с ответом, принимает
диспетчер; исход возвращается тем же SendResult, чтобы ветки каналов в
диспетчере разбирали его одинаково.

Транспорт выбирает настройка EMAIL_BACKEND: локально — консоль, в тестах —
locmem, в бою — SMTP (EMAIL_* из SMTP_* окружения, см. config/settings/base.py).
"""
from __future__ import annotations

import logging
import smtplib

from django.conf import settings
from django.core.mail import send_mail

from apps.notifications.telegram import SendResult

logger = logging.getLogger(__name__)


def send_email(*, to: str, subject: str, body: str) -> SendResult:
    """
    Отправить одно письмо. Исключения наружу не выпускаются.

    Адрес отвергнут сервером (SMTPRecipientsRefused) — постоянный отказ:
    повторять бессмысленно, сообщение сразу в failed. Всё остальное (обрыв
    соединения, таймаут, 4xx сервера) — временная ошибка, диспетчер повторит.
    """
    try:
        send_mail(
            subject=subject, message=body,
            from_email=settings.DEFAULT_FROM_EMAIL or None,
            recipient_list=[to], fail_silently=False,
        )
    except smtplib.SMTPRecipientsRefused as exc:
        return SendResult(ok=False, blocked=True, error=f'Адрес отклонён: {exc}')
    except Exception as exc:  # noqa: BLE001 — сеть/SMTP: любая ошибка = повтор
        logger.warning('Письмо не отправлено: %s', type(exc).__name__)
        return SendResult(ok=False, error=f'{type(exc).__name__}: {exc}')
    return SendResult(ok=True)
