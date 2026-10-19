"""Тесты транспорта: разбор ответов Telegram. Сеть замокана."""
from __future__ import annotations

from unittest.mock import patch

from django.test import override_settings

from apps.notifications import telegram


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@override_settings(TELEGRAM_BOT_TOKEN='tkn')
def test_ok_response():
    with patch('apps.notifications.telegram.requests.post',
               return_value=_Resp(200, {'ok': True})):
        result = telegram.send_message(chat_id=1, text='привет')
    assert result.ok is True
    assert result.blocked is False


@override_settings(TELEGRAM_BOT_TOKEN='tkn')
def test_blocked_by_user_is_permanent():
    payload = {'ok': False, 'error_code': 403,
               'description': 'Forbidden: bot was blocked by the user'}
    with patch('apps.notifications.telegram.requests.post',
               return_value=_Resp(403, payload)):
        result = telegram.send_message(chat_id=1, text='привет')
    assert result.ok is False
    assert result.blocked is True
    assert result.retry_after is None


@override_settings(TELEGRAM_BOT_TOKEN='tkn')
def test_chat_not_found_is_permanent():
    payload = {'ok': False, 'error_code': 400, 'description': 'Bad Request: chat not found'}
    with patch('apps.notifications.telegram.requests.post',
               return_value=_Resp(400, payload)):
        result = telegram.send_message(chat_id=1, text='привет')
    assert result.blocked is True


@override_settings(TELEGRAM_BOT_TOKEN='tkn')
def test_rate_limit_reports_retry_after():
    payload = {'ok': False, 'error_code': 429, 'description': 'Too Many Requests',
               'parameters': {'retry_after': 7}}
    with patch('apps.notifications.telegram.requests.post',
               return_value=_Resp(429, payload)):
        result = telegram.send_message(chat_id=1, text='привет')
    assert result.ok is False
    assert result.blocked is False
    assert result.retry_after == 7


@override_settings(TELEGRAM_BOT_TOKEN='tkn')
def test_network_error_is_temporary():
    import requests as rq
    with patch('apps.notifications.telegram.requests.post',
               side_effect=rq.ConnectionError('boom')):
        result = telegram.send_message(chat_id=1, text='привет')
    assert result.ok is False
    assert result.blocked is False
    assert result.retry_after is None
    assert 'boom' in result.error


@override_settings(TELEGRAM_BOT_TOKEN='')
def test_missing_token_does_not_call_network():
    with patch('apps.notifications.telegram.requests.post') as post:
        result = telegram.send_message(chat_id=1, text='привет')
    post.assert_not_called()
    assert result.ok is False
    assert result.blocked is False
