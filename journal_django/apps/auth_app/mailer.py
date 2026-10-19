"""
mailer.py — отправка email-OTP через django.core.mail.

Фаза 2 (architecture_v2.md): заменён smtplib на django.core.mail.send_mail.
Транспорт определяется EMAIL_BACKEND в settings:
  - dev: django.core.mail.backends.console.EmailBackend (печать в консоль runserver)
  - prod: django.core.mail.backends.smtp.EmailBackend (реальный SMTP)
Флаг EMAIL_OTP_CONSOLE и его ветка удалены.

В тестах функция мокается через unittest.mock.patch('apps.auth_app.mailer.send_otp_email').
"""
from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail


def send_otp_email(to: str, code: str) -> None:
    """
    Отправить одноразовый код входа на email.

    Subject: 'Код входа Eduson Kids'
    Body: 'Ваш одноразовый код входа: {code}\\nКод действует 5 минут. Если вы не входили — проигнорируйте письмо.'
    From: DEFAULT_FROM_EMAIL (из settings, маппинг SMTP_FROM → DEFAULT_FROM_EMAIL в base.py).
    """
    body = (
        f'Ваш одноразовый код входа: {code}\n'
        'Код действует 5 минут. Если вы не входили — проигнорируйте письмо.'
    )
    send_mail(
        subject='Код входа Eduson Kids',
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[to],
        fail_silently=False,
    )
