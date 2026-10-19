"""
test_mailer.py — поведение send_otp_email.

Фаза 2: mailer.py перешёл на django.core.mail.send_mail.
В test-настройках EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend',
поэтому письма копятся в django.core.mail.outbox — реальная отправка не происходит.

Проверяем:
  - send_otp_email кладёт письмо в outbox
  - тема, адресат, код в теле
"""
from __future__ import annotations

import django.core.mail
import pytest

from apps.auth_app.mailer import send_otp_email


@pytest.mark.django_db
def test_send_otp_email_puts_message_in_outbox():
    """send_otp_email → одно письмо в mail.outbox."""
    django.core.mail.outbox.clear()

    send_otp_email('user@example.com', '424242')

    assert len(django.core.mail.outbox) == 1


@pytest.mark.django_db
def test_send_otp_email_recipient():
    """Письмо идёт нужному получателю."""
    django.core.mail.outbox.clear()

    send_otp_email('recipient@example.com', '123456')

    msg = django.core.mail.outbox[0]
    assert 'recipient@example.com' in msg.to


@pytest.mark.django_db
def test_send_otp_email_subject():
    """Тема письма — от имени школы Eduson Kids."""
    django.core.mail.outbox.clear()

    send_otp_email('user@example.com', '999888')

    msg = django.core.mail.outbox[0]
    assert msg.subject == 'Код входа Eduson Kids'


@pytest.mark.django_db
def test_send_otp_email_code_in_body():
    """Код отображается в теле письма."""
    django.core.mail.outbox.clear()

    send_otp_email('user@example.com', '777666')

    msg = django.core.mail.outbox[0]
    assert '777666' in msg.body
