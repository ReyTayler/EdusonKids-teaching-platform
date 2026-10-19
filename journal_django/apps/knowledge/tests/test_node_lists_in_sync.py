"""
Сторож трёх списков узлов.

Узел документа обязан существовать одновременно в трёх местах: редактор умеет
его создать, сервер — принять, читалка — показать. Расхождение даёт один из
двух отказов, и оба тихие:

  создаётся, но не сохраняется  → человек написал документ и потерял его;
  сохраняется, но не рисуется   → документ открывается пустым куском.

Тест сверяет белый список сервера с таблицей соответствий читалки. Проверка
идёт по исходнику фронта, потому что JS-тестов в проекте нет: это дешевле, чем
заводить ради одной сверки второй тестовый стек, и всё равно ловит главное —
забытый узел.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from apps.knowledge.content import (
    ALLOWED_CALLOUT_TONES,
    ALLOWED_HIGHLIGHT_COLORS,
    ALLOWED_NODES,
    ALLOWED_TEXT_COLORS,
)

RENDER_MAP = (
    Path(__file__).resolve().parents[3]
    / 'frontend' / 'admin-src' / 'src' / 'components' / 'knowledge'
    / 'documentRenderMap.tsx'
)
CALLOUT_META = RENDER_MAP.with_name('calloutMeta.ts')
EDITOR_COLORS = RENDER_MAP.with_name('editorColors.ts')

# Узлы, которых в таблице читалки нет и быть не должно:
#   doc  — корень, разворачивается самим рендерером;
#   text — печатается напрямую.
# Оба присутствуют в mapping, но перечислены здесь на случай, если исчезнут.
_ALWAYS_HANDLED = frozenset({'doc', 'text'})


def _mapping_keys(source: str, name: str) -> set[str]:
    """Ключи объекта nodeMapping/markMapping из исходника фронта."""
    start = source.index(f'export const {name}')
    body = source[start:source.index('\n};', start)]
    return set(re.findall(r'^\s{2}(\w+):', body, flags=re.MULTILINE))


@pytest.fixture(scope='module')
def render_map_source() -> str:
    assert RENDER_MAP.exists(), f'Не найден {RENDER_MAP}'
    return RENDER_MAP.read_text(encoding='utf-8')


def test_reader_handles_every_allowed_node(render_map_source):
    handled = _mapping_keys(render_map_source, 'nodeMapping') | _ALWAYS_HANDLED
    missing = ALLOWED_NODES - handled
    assert not missing, (
        f'Сервер принимает узлы, которых читалка не умеет показать: {sorted(missing)}. '
        'Добавьте их в documentRenderMap.tsx.'
    )


def test_reader_does_not_render_forbidden_nodes(render_map_source):
    handled = _mapping_keys(render_map_source, 'nodeMapping')
    extra = handled - ALLOWED_NODES - _ALWAYS_HANDLED
    assert not extra, (
        f'Читалка рисует узлы, которые сервер не принимает: {sorted(extra)}. '
        'Либо добавьте их в ALLOWED_NODES, либо уберите из рендера.'
    )


def test_callout_tones_match_frontend():
    source = CALLOUT_META.read_text(encoding='utf-8')
    listed = set(re.findall(r"'(\w+)'", source.split('CALLOUT_TONES = [')[1].split(']')[0]))
    assert listed == set(ALLOWED_CALLOUT_TONES), (
        'Виды выноски на фронте и на сервере разошлись: '
        f'{sorted(listed)} против {sorted(ALLOWED_CALLOUT_TONES)}.'
    )


def _palette(source: str, name: str) -> set[str]:
    """
    Непустые значения одного списка цветов из editorColors.ts.

    Пустая строка в списке — пункт «снять цвет», на сервере ей соответствует
    отсутствие атрибута, поэтому в сверке она не участвует.
    """
    body = source.split(f'export const {name}: ColorChoice[] = [')[1].split('];')[0]
    return {value for value in re.findall(r"value: '([^']*)'", body) if value}


@pytest.fixture(scope='module')
def colors_source() -> str:
    assert EDITOR_COLORS.exists(), f'Не найден {EDITOR_COLORS}'
    return EDITOR_COLORS.read_text(encoding='utf-8')


def test_text_colors_match_frontend(colors_source):
    listed = _palette(colors_source, 'TEXT_COLORS')
    assert listed == set(ALLOWED_TEXT_COLORS), (
        'Палитра цвета текста разошлась с сервером: '
        f'{sorted(listed)} против {sorted(ALLOWED_TEXT_COLORS)}. '
        'Выбранный в редакторе цвет, которого нет в белом списке, сделает '
        'документ несохраняемым.'
    )


def test_highlight_colors_match_frontend(colors_source):
    listed = _palette(colors_source, 'HIGHLIGHT_COLORS')
    assert listed == set(ALLOWED_HIGHLIGHT_COLORS), (
        'Палитра выделения разошлась с сервером: '
        f'{sorted(listed)} против {sorted(ALLOWED_HIGHLIGHT_COLORS)}.'
    )


@pytest.mark.parametrize(
    'palette', [*ALLOWED_TEXT_COLORS, *ALLOWED_HIGHLIGHT_COLORS],
)
def test_colors_are_token_references(palette):
    """
    Цвет хранится ссылкой на токен, а не кодом.

    Код цвета не знает, на каком фоне его покажут: набранный в светлой теме
    красный текст в тёмной оказался бы почти нечитаемым, а светлая заливка —
    невидимой.
    """
    assert palette.startswith('var(--') and palette.endswith(')'), (
        f'{palette!r} — не ссылка на токен.'
    )
