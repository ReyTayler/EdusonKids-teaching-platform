# journal_django/apps/sync/tests/test_prod_refresh.py
"""Кнопка «Обновить данные с прода»: RBAC, флаг, лок, запуск скрипта, разбор лога.
Процесс не запускается (Popen подменён), каталог снимков — tmp_path."""
import os
import time

import pytest

from apps.sync import prod_refresh

URL = '/api/admin/sync/prod-refresh'


@pytest.fixture
def snap(tmp_path, settings):
    settings.EDUSON_SNAPSHOT_DIR = tmp_path
    settings.EDUSON_PROD_REFRESH_BUTTON = True
    settings.EDUSON_REFRESH_BASH = 'bash-for-test'
    return tmp_path


@pytest.fixture
def popen(monkeypatch):
    calls = []
    monkeypatch.setattr(prod_refresh.subprocess, 'Popen', lambda args, **kw: calls.append((args, kw)))
    return calls


def _log(snap, *lines):
    (snap / 'refresh.log').write_text('\n'.join(lines) + '\n', encoding='utf-8')


@pytest.mark.django_db
def test_requires_superadmin(admin_client, snap):
    assert admin_client.get(URL).status_code == 403
    assert admin_client.post(URL).status_code == 403


@pytest.mark.django_db
def test_rejects_anonymous(anon_client, snap):
    assert anon_client.post(URL).status_code == 401


@pytest.mark.django_db
def test_disabled_flag(superadmin_client, snap, popen, settings):
    settings.EDUSON_PROD_REFRESH_BUTTON = False
    assert superadmin_client.get(URL).data['enabled'] is False
    assert superadmin_client.post(URL).status_code == 404
    assert popen == []


@pytest.mark.django_db
def test_start_runs_soft_script(superadmin_client, snap, popen, settings):
    _log(snap, '2026-10-20 06:30:00 === обновление eduson из прода Eduson (20261020-0630 МСК) ===',
         '2026-10-20 06:31:00 ОШИБКА: снимок на проде не снят')
    resp = superadmin_client.post(URL)
    assert resp.status_code == 202
    assert resp.data['running'] is True                 # отметка запроса — до появления лока
    assert resp.data['log_tail'] == []                  # лог прошлого запуска не выдаётся за текущий
    (args, kwargs), = popen
    assert args == ['bash-for-test', str(settings.EDUSON_REFRESH_SCRIPT), '--soft']
    assert kwargs['stdin'] is prod_refresh.subprocess.DEVNULL and 'shell' not in kwargs


@pytest.mark.django_db
def test_running_lock_gives_409(superadmin_client, snap, popen):
    (snap / '.refresh.lock').mkdir()
    status = superadmin_client.get(URL).data
    assert status['running'] is True and status['last'] is None
    assert superadmin_client.post(URL).status_code == 409
    assert popen == []


@pytest.mark.django_db
def test_stale_lock_is_ignored(superadmin_client, snap, popen):
    lock = snap / '.refresh.lock'
    lock.mkdir()
    old = time.time() - 3 * 3600
    os.utime(lock, (old, old))
    assert superadmin_client.get(URL).data['running'] is False
    assert superadmin_client.post(URL).status_code == 202


@pytest.mark.django_db
def test_status_reads_last_run_from_log(superadmin_client, snap):
    _log(snap,
         '2026-10-20 06:30:00 === обновление eduson из прода Eduson (20261020-0630 МСК) ===',
         '2026-10-20 06:33:00 === обновление завершено за 180 с ===',
         '2026-10-20 12:00:00 === мягкое обновление eduson из прода Eduson (20261020-1200 МСК) ===',
         '2026-10-20 12:00:05 1/4 снимок на проде (readOnly + VACUUM INTO)',
         '2026-10-20 12:00:40 === мягкое обновление завершено за 40 с ===')
    data = superadmin_client.get(URL).data
    assert data['running'] is False
    assert data['last'] == {'at': '2026-10-20 12:00:40', 'ok': True,
                            'text': '=== мягкое обновление завершено за 40 с ==='}
    assert data['log_tail'][0].endswith('(20261020-1200 МСК) ===')   # только последний запуск


@pytest.mark.django_db
def test_status_reports_failure(superadmin_client, snap):
    _log(snap,
         '2026-10-20 12:00:00 === мягкое обновление eduson из прода Eduson (20261020-1200 МСК) ===',
         '2026-10-20 12:00:30 ОШИБКА: снимок на проде не снят',
         '2026-10-20 12:00:30 обновление прервано (код 1)')
    last = superadmin_client.get(URL).data['last']
    assert last['ok'] is False and 'прервано' in last['text']


@pytest.mark.django_db
def test_status_without_log(superadmin_client, snap):
    data = superadmin_client.get(URL).data
    assert (data['running'], data['last'], data['log_tail']) == (False, None, [])
