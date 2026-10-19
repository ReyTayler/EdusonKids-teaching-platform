"""
Celery-задачи auth_app (Celery-спека 2026-07-13, фаза A).

send_otp_email_task — фоновая отправка OTP-письма: SMTP-рукопожатие (1–5с в
норме, десятки секунд при деградации почтовика) не должно занимать
gunicorn-воркер в запросе /login. Очередь interactive (CELERY_TASK_ROUTES) —
письмо входа не стоит за тяжёлыми фоновыми задачами.

Модуль импортируется только там, где установлен celery (прод). Локальный dev
без celery работает через синхронный fallback в services.send_otp_email.
Логика отправки — в mailer (тестируется без Celery); задача лишь делегирует.
Обращение через модуль (mailer.send_otp_email) — чтобы mock.patch по
'apps.auth_app.mailer.send_otp_email' действовал и на задачу.
"""
from __future__ import annotations

from celery import shared_task

from apps.auth_app import mailer


@shared_task(
    name='apps.auth_app.tasks.send_otp_email_task',
    # SMTPException — подкласс OSError (Python ≥3.4): покрывает и SMTP-ошибки,
    # и сетевые (timeout, connection refused).
    autoretry_for=(OSError,),
    retry_backoff=True,        # 1с, 2с, 4с …
    retry_backoff_max=60,
    max_retries=3,             # код живёт 5 минут — больше ретраить бессмысленно
    acks_late=True,
    time_limit=30,
)
def send_otp_email_task(to: str, code: str) -> None:
    """Отправить одноразовый код на email. Код в лог не пишется."""
    mailer.send_otp_email(to, code)
