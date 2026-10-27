"""Клиент журнала: заголовок с секретом, обработка ответов."""
from unittest.mock import patch

import pytest

from bot import journal_api


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    @property
    def text(self):
        return str(self._payload)

    def json(self):
        return self._payload


def test_identify_sends_secret_header(monkeypatch):
    monkeypatch.setattr(journal_api, 'SERVICE_TOKEN', 'secret-x')
    with patch('bot.journal_api.httpx.post', return_value=_Resp(204)) as post:
        journal_api.identify(telegram_id=1, username='anna', full_name='Анна')
    _args, kwargs = post.call_args
    assert kwargs['headers']['X-Bot-Token'] == 'secret-x'


def test_identify_never_raises_on_failure():
    """Журнал недоступен — бот обязан продолжать отвечать человеку."""
    import httpx
    with patch('bot.journal_api.httpx.post', side_effect=httpx.ConnectError('down')):
        assert journal_api.identify(telegram_id=1, username=None, full_name='Аноним') is False


def test_my_returns_none_when_not_linked():
    with patch('bot.journal_api.httpx.get', return_value=_Resp(404)):
        assert journal_api.my_events(telegram_id=1) is None


def test_my_returns_payload():
    payload = {'teacher_name': 'Анна', 'events': [{'date': '2026-08-05'}]}
    with patch('bot.journal_api.httpx.get', return_value=_Resp(200, payload)):
        assert journal_api.my_events(telegram_id=1) == payload


def test_identify_logs_rejection(caplog):
    """Отказ журнала обязан попадать в лог.

    Иначе снаружи это выглядит как «бот отвечает, но аккаунт не появляется
    в списке привязки», и причину искать негде.
    """
    with patch('bot.journal_api.httpx.post', return_value=_Resp(400)):
        assert journal_api.identify(telegram_id=1, username=None, full_name='И') is False
    assert 'отклонён журналом' in caplog.text
    assert '400' in caplog.text


def test_requests_carry_host_and_proto(monkeypatch):
    """Бот ходит в app:8000 мимо nginx: без Host — 400 DisallowedHost,
    без X-Forwarded-Proto — 301 на https, который httpx не проходит."""
    monkeypatch.setattr(journal_api, 'JOURNAL_HOST', 'eduson.example.test')
    with patch('bot.journal_api.httpx.post', return_value=_Resp(204)) as post:
        journal_api.identify(telegram_id=1, username=None, full_name='И')
    headers = post.call_args.kwargs['headers']
    assert headers['Host'] == 'eduson.example.test'
    assert headers['X-Forwarded-Proto'] == 'https'


def test_no_host_override_when_unset(monkeypatch):
    """Локально (runserver) JOURNAL_HOST пуст — Host не подменяем."""
    monkeypatch.setattr(journal_api, 'JOURNAL_HOST', '')
    with patch('bot.journal_api.httpx.get', return_value=_Resp(404)) as get:
        journal_api.my_events(telegram_id=1)
    assert 'Host' not in get.call_args.kwargs['headers']
