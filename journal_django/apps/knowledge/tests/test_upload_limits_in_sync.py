"""
Сторож пределов загрузки.

Один и тот же предел записан в четырёх местах: настройка Django, константа
фронта и по одному `client_max_body_size` в продовом и локальном конфигах
nginx. Разойдутся — отказ приходит НЕ от приложения, а от nginx, и приходит он
чужой страницей вместо нашего текста с числами. Именно так и вышло 2026-08-12:
предел файлов подняли в трёх местах из четырёх, и файл на 13 МБ отвергался с
бессмысленным «Файл слишком большой».

Сверка идёт по исходникам — так же, как в test_node_lists_in_sync: JS-тестов в
проекте нет, а конфиги nginx вообще не исполняются в тестах. Дёшево и ловит
главное — забытое место.

Проверяется не равенство, а порядок: nginx обязан пропускать БОЛЬШЕ приложения,
иначе отказ формулирует он. Но и не сильно больше — иначе смысл лимита теряется,
и лишние мегабайты успевают доехать до диска, прежде чем их отвергнут.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

REPO_ROOT = Path(__file__).resolve().parents[4]
NGINX_CONFIGS = {
    'прод': REPO_ROOT / 'deploy' / 'nginx' / 'journal-eduson.conf',
    'локальный': REPO_ROOT / 'deploy' / 'nginx' / 'local' / 'nginx.conf',
}
FILE_KINDS = (
    REPO_ROOT / 'journal_django' / 'frontend' / 'admin-src' / 'src'
    / 'components' / 'knowledge' / 'fileKinds.ts'
)

# Прод-конфиг nginx Eduson появится только на этапе развёртывания
# (docs/eduson/stage4-deploy-backlog.md). До тех пор его сверку пропускаем —
# но ТОЛЬКО его: локальный конфиг уже есть и проверяется по-настоящему, а
# пропуск срабатывает лишь пока файла нет. Положили шаблон — проверка
# включится сама, без правки теста.
PENDING_UNTIL_DEPLOY = {'прод': 'прод-шаблон nginx Eduson — этап 4'}

MB = 1024 * 1024

# Сколько сверх предела приложения допустимо отдать nginx. Запас нужен на
# служебные данные формы (границы частей, заголовки), но пара мегабайт — это
# уже не запас, а второй лимит, о котором никто не помнит.
MAX_HEADROOM_BYTES = 2 * MB

_SIZE_UNITS = {'': 1, 'k': 1024, 'm': MB, 'g': 1024 * MB}


def _location_block(source: str, path: str) -> str:
    """
    Тело точного location по адресу.

    Внутри этих блоков вложенных фигурных скобок нет, поэтому конец блока —
    первая же закрывающая скобка. Полноценный разбор конфига здесь был бы
    дороже задачи.
    """
    header = f'location = {path} {{'
    start = source.find(header)
    assert start != -1, f'В конфиге нет точного location для {path}'
    end = source.index('}', start)
    return source[start:end]


def _body_size(block: str) -> int:
    match = re.search(r'client_max_body_size\s+(\d+)([kKmMgG]?)\s*;', block)
    assert match, 'В блоке не задан client_max_body_size'
    return int(match.group(1)) * _SIZE_UNITS[match.group(2).lower()]


@pytest.mark.parametrize('where', sorted(NGINX_CONFIGS))
@pytest.mark.parametrize(
    ('endpoint', 'setting_name'),
    [
        ('/api/admin/knowledge/files', 'KNOWLEDGE_MAX_FILE_BYTES'),
        ('/api/admin/knowledge/images', 'KNOWLEDGE_MAX_IMAGE_BYTES'),
    ],
)
def test_nginx_limit_covers_application_limit(where, endpoint, setting_name):
    config = NGINX_CONFIGS[where]
    if not config.exists() and where in PENDING_UNTIL_DEPLOY:
        pytest.skip(f'{PENDING_UNTIL_DEPLOY[where]}: нет {config.name}')
    assert config.exists(), f'Не найден {config}'

    app_limit = getattr(settings, setting_name)
    nginx_limit = _body_size(_location_block(config.read_text(encoding='utf-8'), endpoint))

    assert nginx_limit >= app_limit, (
        f'{where} nginx режет {endpoint} раньше приложения: '
        f'{nginx_limit / MB:.0f} МБ против {app_limit / MB:.0f} МБ. '
        'Пользователь получит отказ от nginx — чужой страницей, без объяснения.'
    )
    assert nginx_limit - app_limit <= MAX_HEADROOM_BYTES, (
        f'{where} nginx пропускает на {(nginx_limit - app_limit) / MB:.0f} МБ больше '
        f'приложения ({setting_name}). Такой запас — это второй, забытый лимит.'
    )


def test_frontend_file_limit_matches_backend():
    """
    Предел на фронте обязан совпадать ТОЧНО, а не «покрывать».

    Он не защищает сервер — он опережает его: проверка размера до отправки
    избавляет от ожидания, пока файл целиком уедет по сети. Будь он больше —
    ожидание вернётся; меньше — часть допустимых файлов окажется недоступна
    без всякой причины.
    """
    assert FILE_KINDS.exists(), f'Не найден {FILE_KINDS}'
    match = re.search(
        r'MAX_FILE_BYTES\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024',
        FILE_KINDS.read_text(encoding='utf-8'),
    )
    assert match, 'В fileKinds.ts не найдена константа MAX_FILE_BYTES'

    frontend_limit = int(match.group(1)) * MB
    assert frontend_limit == settings.KNOWLEDGE_MAX_FILE_BYTES, (
        f'Предел на фронте {frontend_limit / MB:.0f} МБ, '
        f'на сервере {settings.KNOWLEDGE_MAX_FILE_BYTES / MB:.0f} МБ.'
    )
