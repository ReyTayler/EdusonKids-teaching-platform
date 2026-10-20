"""Кнопка «Обновить данные с прода»: запуск scripts/refresh_from_prod.sh --soft и его статус.

ПОЧЕМУ СКРИПТ, А НЕ CELERY-ЗАДАЧА. Снимок прода снимается по SSH (ретраи, проверка
sha256, уборка временного файла на сервере), таблицы Eduson качаются curl — всё это
уже есть в скрипте и работает каждое утро. Вторая копия той же логики на Python
разошлась бы с ним. Кнопка лишь запускает скрипт отдельным процессом (он живёт дольше
HTTP-запроса и не зависит от того, запущен ли Celery) и читает его лок и лог.

СТАТУС. «Идёт» — существует каталог-лок скрипта (его же проверяет ночная задача, так
что кнопка видит и её). Лок старше STALE_AFTER — зависший (скрипт убили): сам скрипт
перехватывает мёртвый лок по PID, поэтому кнопка снова доступна. Между нажатием и
появлением лока проходит секунда-другая — её покрывает отметка запроса REQUEST_MARK.
Итог и хвост — из refresh.log последнего запуска. Лог по устройству скрипта без ПДн
(id Eduson, коды групп, числа), а читает его только superadmin.

БЕЗОПАСНОСТЬ. Команда фиксирована (bash, путь к скрипту, --soft), ввода пользователя в
ней нет; shell=False.
"""
from __future__ import annotations

import datetime
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.utils import timezone

LOCK_NAME = '.refresh.lock'
LOG_NAME = 'refresh.log'
REQUEST_MARK = '.prod-refresh-requested'
STALE_AFTER = datetime.timedelta(hours=2)
REQUEST_GRACE = datetime.timedelta(seconds=60)
TAIL_LINES = 40
_TAIL_BYTES = 256 * 1024
# Git for Windows ставит bash сюда; «bash» из PATH в Windows бывает WSL.
_GIT_BASH = Path(r'C:\Program Files\Git\bin\bash.exe')

# Маркеры строк лога (scripts/refresh_from_prod.sh, log()): начало, успех, ошибка.
_START = ' из прода Eduson ('
_DONE = ('=== мягкое обновление завершено', '=== обновление завершено')
_FAIL = ('ОШИБКА:', 'обновление прервано')


class RefreshAlreadyRunning(Exception):
    """Обновление уже идёт (кнопкой или ночной задачей)."""


def _dir() -> Path:
    return Path(settings.EDUSON_SNAPSHOT_DIR)


def _mtime(path: Path) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)
    except OSError:
        return None


def running_since() -> datetime.datetime | None:
    """Момент начала идущего обновления или None."""
    now = timezone.now()
    started = _mtime(_dir() / LOCK_NAME)
    if started is not None and now - started < STALE_AFTER:
        return started
    requested = _mtime(_dir() / REQUEST_MARK)
    if requested is not None and now - requested < REQUEST_GRACE:
        return requested
    return None


def _last_run_lines() -> list[str]:
    """Строки лога последнего запуска (с его строки «=== … из прода Eduson (…) ===»)."""
    path = _dir() / LOG_NAME
    try:
        with path.open('rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - _TAIL_BYTES))
            text = f.read().decode('utf-8', errors='replace')
    except OSError:
        return []
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _START in line and '===' in line]
    return lines[starts[-1]:] if starts else lines[-TAIL_LINES:]


def _result(lines: list[str]) -> dict | None:
    """Итог последнего запуска по логу: {'at', 'ok', 'text'} или None (ещё идёт / нет лога)."""
    for line in reversed(lines):
        ok = any(marker in line for marker in _DONE)
        if ok or any(marker in line for marker in _FAIL):
            # Строка лога: «ГГГГ-ММ-ДД ЧЧ:ММ:СС текст».
            return {'at': line[:19], 'ok': ok, 'text': line[20:].strip()}
    return None


def status() -> dict:
    since = running_since()
    # Запуск запрошен, но скрипт ещё не взял лок: в логе пока прошлый запуск — не показываем.
    lines = [] if since is not None and not (_dir() / LOCK_NAME).exists() else _last_run_lines()
    return {
        'enabled': settings.EDUSON_PROD_REFRESH_BUTTON,
        'running': since is not None,
        'started_at': since.isoformat() if since else None,
        'last': None if since else _result(lines),
        'log_tail': lines[-TAIL_LINES:],
    }


def _bash() -> str:
    if settings.EDUSON_REFRESH_BASH:
        return settings.EDUSON_REFRESH_BASH
    if os.name == 'nt' and _GIT_BASH.exists():
        return str(_GIT_BASH)
    return shutil.which('bash') or 'bash'


def start() -> None:
    """Запустить мягкое обновление отдельным процессом; идёт — RefreshAlreadyRunning."""
    if running_since() is not None:
        raise RefreshAlreadyRunning
    directory = _dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / REQUEST_MARK).touch()
    kwargs: dict = {}
    if os.name == 'nt':
        # Своя группа процессов и без окна консоли: процесс переживает запрос и не
        # получает Ctrl+C от консоли runserver.
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs['start_new_session'] = True
    subprocess.Popen(  # noqa: S603 — команда фиксирована, ввода пользователя нет
        [_bash(), str(settings.EDUSON_REFRESH_SCRIPT), '--soft'],
        cwd=str(settings.EDUSON_REFRESH_SCRIPT.parent.parent),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, **kwargs,
    )
