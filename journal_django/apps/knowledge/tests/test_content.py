"""
Тесты чистого модуля content.py — валидация TipTap-JSON и извлечение текста.

БД не нужна: модуль не ходит в базу, множество допустимых id картинок ему
передаётся аргументом.
"""
from __future__ import annotations

import pytest

from apps.knowledge import content


def _doc(*nodes) -> dict:
    return {'type': 'doc', 'content': list(nodes)}


def _para(text: str, marks: list | None = None) -> dict:
    node = {'type': 'text', 'text': text}
    if marks is not None:
        node['marks'] = marks
    return {'type': 'paragraph', 'content': [node]}


# ---------------------------------------------------------------------------
# validate_content
# ---------------------------------------------------------------------------

def test_valid_document_passes():
    content.validate_content(_doc(_para('Привет')))


def test_root_must_be_doc():
    with pytest.raises(content.ContentError, match='root'):
        content.validate_content({'type': 'paragraph'})


def test_unknown_node_type_rejected():
    with pytest.raises(content.ContentError, match='script'):
        content.validate_content(_doc({'type': 'script', 'content': []}))


def test_unknown_mark_rejected():
    node = {'type': 'text', 'text': 'x', 'marks': [{'type': 'evil'}]}
    with pytest.raises(content.ContentError, match='evil'):
        content.validate_content(_doc({'type': 'paragraph', 'content': [node]}))


def test_allowed_marks_pass():
    node = {'type': 'text', 'text': 'x', 'marks': [{'type': 'bold'}, {'type': 'italic'}]}
    content.validate_content(_doc({'type': 'paragraph', 'content': [node]}))


def test_link_href_must_be_http_or_relative():
    def linked(href):
        return _doc({'type': 'paragraph', 'content': [
            {'type': 'text', 'text': 'x',
             'marks': [{'type': 'link', 'attrs': {'href': href}}]},
        ]})

    content.validate_content(linked('https://example.com'))
    content.validate_content(linked('/admin/students'))
    with pytest.raises(content.ContentError, match='href'):
        content.validate_content(linked('javascript:alert(1)'))


def test_too_deep_rejected():
    node = {'type': 'paragraph', 'content': []}
    deep = node
    for _ in range(content.MAX_DEPTH + 2):
        deep = {'type': 'blockquote', 'content': [deep]}
    with pytest.raises(content.ContentError, match='глубин'):
        content.validate_content(_doc(deep))


def test_too_large_rejected():
    big = _doc(_para('я' * (content.MAX_CONTENT_BYTES)))
    with pytest.raises(content.ContentError, match='размер'):
        content.validate_content(big)


def test_text_color_and_highlight_accepted():
    doc = _doc(_para('важно', marks=[{'type': 'textStyle', 'attrs': {
        'color': 'var(--danger)', 'backgroundColor': 'var(--warning-soft)',
    }}]))
    content.validate_content(doc)


def test_arbitrary_color_rejected():
    """
    Свободная строка в цвете — это произвольный CSS у читателя: значение
    уходит в инлайновый стиль.
    """
    doc = _doc(_para('текст', marks=[{'type': 'textStyle', 'attrs': {
        'color': 'red; position: fixed; inset: 0',
    }}]))
    with pytest.raises(content.ContentError, match='цвет текста'):
        content.validate_content(doc)


def test_hex_color_rejected():
    """Код цвета не пройдёт: он не переживает смену темы, храним токены."""
    doc = _doc(_para('текст', marks=[{'type': 'textStyle', 'attrs': {'color': '#ff0000'}}]))
    with pytest.raises(content.ContentError, match='цвет текста'):
        content.validate_content(doc)


def test_highlight_outside_palette_rejected():
    doc = _doc(_para('текст', marks=[{'type': 'textStyle', 'attrs': {
        'backgroundColor': 'var(--accent)',
    }}]))
    with pytest.raises(content.ContentError, match='цвет выделения'):
        content.validate_content(doc)


def test_block_background_accepted():
    doc = _doc({'type': 'paragraph',
                'attrs': {'blockBackground': 'var(--info-soft)'},
                'content': [{'type': 'text', 'text': 'выделенный абзац'}]})
    content.validate_content(doc)


def test_block_background_outside_palette_rejected():
    """
    Палитра фона блока — та же, что у выделения текста. Плотный цвет сюда не
    проходит: под ним текст стал бы нечитаемым.
    """
    doc = _doc({'type': 'heading',
                'attrs': {'level': 2, 'blockBackground': 'var(--danger)'},
                'content': [{'type': 'text', 'text': 'заголовок'}]})
    with pytest.raises(content.ContentError, match='фон блока'):
        content.validate_content(doc)


def test_block_background_arbitrary_css_rejected():
    doc = _doc({'type': 'paragraph',
                'attrs': {'blockBackground': 'url(https://example.com/x.png)'},
                'content': [{'type': 'text', 'text': 'x'}]})
    with pytest.raises(content.ContentError, match='фон блока'):
        content.validate_content(doc)


def _cell(**attrs) -> dict:
    return {'type': 'table', 'content': [
        {'type': 'tableRow', 'content': [
            {'type': 'tableCell', 'attrs': attrs, 'content': [_para('x')]},
        ]},
    ]}


def test_col_width_accepted():
    """Ширины пишет штатное изменение ширины столбцов, читалка их разворачивает."""
    content.validate_content(_doc(_cell(colwidth=[220, 380])))


def test_col_width_allows_none_entries():
    """None на месте столбца, ширину которого не задавали, — законное значение."""
    content.validate_content(_doc(_cell(colwidth=[220, None])))


def test_col_width_must_be_list():
    with pytest.raises(content.ContentError, match='списком'):
        content.validate_content(_doc(_cell(colwidth=220)))


@pytest.mark.parametrize('width', [0, -10, '220', 12.5, True, content.MAX_COLWIDTH_PX + 1])
def test_col_width_rejects_bad_value(width):
    with pytest.raises(content.ContentError, match='ширина столбца'):
        content.validate_content(_doc(_cell(colwidth=[width])))


def test_col_width_rejects_absurd_length():
    """Массив разворачивается в теги col — длину ограничиваем."""
    huge = [100] * (content.MAX_COLWIDTH_ENTRIES + 1)
    with pytest.raises(content.ContentError, match='ширин столбцов'):
        content.validate_content(_doc(_cell(colwidth=huge)))


def test_cell_align_still_checked():
    """Выравнивание в ячейке появилось в меню — проверка значения на месте."""
    content.validate_content(_doc(_cell(align='center')))
    with pytest.raises(content.ContentError, match='ыравнивание'):
        content.validate_content(_doc(_cell(align='middle')))


def test_image_must_reference_known_id():
    doc = _doc({'type': 'knowledgeImage', 'attrs': {'imageId': 7}})
    with pytest.raises(content.ContentError, match='7'):
        content.validate_content(doc, allowed_image_ids={1, 2})
    content.validate_content(doc, allowed_image_ids={7})


def test_image_without_id_rejected():
    doc = _doc({'type': 'knowledgeImage', 'attrs': {}})
    with pytest.raises(content.ContentError, match='imageId'):
        content.validate_content(doc, allowed_image_ids={1})


def test_image_id_true_is_not_a_number():
    """bool — подкласс int, а True == 1: без явной проверки узел прошёл бы."""
    doc = _doc({'type': 'knowledgeImage', 'attrs': {'imageId': True}})
    with pytest.raises(content.ContentError, match='imageId'):
        content.validate_content(doc, allowed_image_ids={1})


def test_image_keeps_size_and_alt():
    doc = _doc({'type': 'knowledgeImage', 'attrs': {
        'imageId': 7, 'alt': 'схема', 'width': 1200, 'height': 800,
    }})
    content.validate_content(doc, allowed_image_ids={7})


def test_image_rejects_unknown_attribute():
    doc = _doc({'type': 'knowledgeImage', 'attrs': {'imageId': 7, 'style': 'x'}})
    with pytest.raises(content.ContentError, match='атрибут'):
        content.validate_content(doc, allowed_image_ids={7})


def test_image_keeps_block_id():
    """
    Редактор вешает blockId на картинку (UniqueID, ID_TYPES) — закрытый список
    атрибутов картинки обязан его пропускать.

    Без этого документ с любой картинкой не сохранялся вовсе: PATCH отвечал 400
    invalid_content, и вся работа автора оставалась только во вкладке.
    """
    doc = _doc({'type': 'knowledgeImage', 'attrs': {
        'imageId': 7, 'blockId': 'a1b2c3d4-0000-4000-8000-000000000000',
    }})
    content.validate_content(doc, allowed_image_ids={7})


def test_image_block_id_still_validated():
    """Пропускаем атрибут, но не его содержимое: id уходит в разметку."""
    doc = _doc({'type': 'knowledgeImage', 'attrs': {
        'imageId': 7, 'blockId': 'плохой id!',
    }})
    with pytest.raises(content.ContentError, match='дентификатор блока'):
        content.validate_content(doc, allowed_image_ids={7})


@pytest.mark.parametrize('size', [0, -5, content.MAX_IMAGE_SIDE + 1, '1200', 12.5])
def test_image_rejects_bad_size(size):
    doc = _doc({'type': 'knowledgeImage', 'attrs': {'imageId': 7, 'width': size}})
    with pytest.raises(content.ContentError, match='азмер'):
        content.validate_content(doc, allowed_image_ids={7})


def test_image_rejects_non_string_alt():
    doc = _doc({'type': 'knowledgeImage', 'attrs': {'imageId': 7, 'alt': {'x': 1}}})
    with pytest.raises(content.ContentError, match='alt'):
        content.validate_content(doc, allowed_image_ids={7})


# ---------------------------------------------------------------------------
# collect_image_ids
# ---------------------------------------------------------------------------

def test_collect_image_ids_finds_nested():
    doc = _doc(
        {'type': 'knowledgeImage', 'attrs': {'imageId': 3}},
        {'type': 'blockquote', 'content': [
            {'type': 'knowledgeImage', 'attrs': {'imageId': 5}},
        ]},
    )
    assert content.collect_image_ids(doc) == {3, 5}


def test_collect_image_ids_empty():
    assert content.collect_image_ids(_doc(_para('нет картинок'))) == set()


# ---------------------------------------------------------------------------
# extract_plain_text
# ---------------------------------------------------------------------------

def test_extract_plain_text_joins_blocks():
    doc = _doc(
        {'type': 'heading', 'attrs': {'level': 2},
         'content': [{'type': 'text', 'text': 'Заголовок'}]},
        _para('Первый абзац'),
        _para('Второй абзац'),
    )
    assert content.extract_plain_text(doc) == 'Заголовок\nПервый абзац\nВторой абзац'


def test_extract_plain_text_handles_empty_doc():
    assert content.extract_plain_text({'type': 'doc', 'content': []}) == ''


def test_extract_plain_text_keeps_inline_text_together():
    doc = _doc({'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'жирный '},
        {'type': 'text', 'text': 'и обычный'},
    ]})
    assert content.extract_plain_text(doc) == 'жирный и обычный'


# ---------------------------------------------------------------------------
# Подчёркивание, выравнивание, шрифты
# ---------------------------------------------------------------------------

def _marked(mark: dict) -> dict:
    return _doc({'type': 'paragraph', 'content': [
        {'type': 'text', 'text': 'x', 'marks': [mark]},
    ]})


def test_underline_mark_allowed():
    content.validate_content(_marked({'type': 'underline'}))


def test_text_align_allowed_values_pass():
    for align in ('left', 'center', 'right', 'justify'):
        content.validate_content(_doc({
            'type': 'paragraph', 'attrs': {'textAlign': align},
            'content': [{'type': 'text', 'text': 'x'}],
        }))


def test_text_align_rejects_unknown_value():
    with pytest.raises(content.ContentError, match='outside'):
        content.validate_content(_doc({
            'type': 'paragraph', 'attrs': {'textAlign': 'outside'},
            'content': [{'type': 'text', 'text': 'x'}],
        }))


def test_font_family_from_whitelist_passes():
    font = sorted(content.ALLOWED_FONTS)[0]
    content.validate_content(_marked({'type': 'textStyle', 'attrs': {'fontFamily': font}}))


def test_font_family_outside_whitelist_rejected():
    with pytest.raises(content.ContentError, match='[Шш]рифт'):
        content.validate_content(
            _marked({'type': 'textStyle', 'attrs': {'fontFamily': 'url(evil.css)'}}),
        )


def test_text_style_rejects_unknown_attribute():
    """
    Через textStyle нельзя протащить произвольный инлайновый стиль.

    Проверяется атрибутом, которого у нас нет вовсе. Раньше здесь стоял
    `color` — он стал поддерживаемым, когда появился цвет текста, но правило не
    изменилось: разрешён закрытый список атрибутов, и у каждого свой закрытый
    список значений (см. тесты цвета выше).
    """
    with pytest.raises(content.ContentError, match='fontSize'):
        content.validate_content(
            _marked({'type': 'textStyle', 'attrs': {'fontSize': '48px'}}),
        )


# ---------------------------------------------------------------------------
# Устойчивость к кривой форме узла (иначе 500 вместо 400)
# ---------------------------------------------------------------------------

def test_non_dict_attrs_raise_content_error():
    # Раньше падало AttributeError мимо обработчика — клиент получал 500.
    with pytest.raises(content.ContentError, match='[Аа]трибуты'):
        content.validate_content(_doc({
            'type': 'paragraph', 'attrs': [1, 2],
            'content': [{'type': 'text', 'text': 'x'}],
        }))


def test_non_string_text_rejected():
    # Объект в text проходил валидацию и ронял рендер у ВСЕХ читателей.
    with pytest.raises(content.ContentError, match='[Тт]екстовый'):
        content.validate_content(_doc({
            'type': 'paragraph', 'content': [{'type': 'text', 'text': {'a': 1}}],
        }))


def test_protocol_relative_link_rejected():
    # `//чужой-домен` начинается со слэша, но ведёт наружу.
    for href in ('//evil.example/phish', '/\\evil.example'):
        with pytest.raises(content.ContentError, match='href'):
            content.validate_content(_doc({'type': 'paragraph', 'content': [
                {'type': 'text', 'text': 'x',
                 'marks': [{'type': 'link', 'attrs': {'href': href}}]},
            ]}))


def test_table_cell_align_validated():
    # У ячеек атрибут называется align, а не textAlign — проверяется так же.
    content.validate_content(_doc({'type': 'table', 'content': [
        {'type': 'tableRow', 'content': [
            {'type': 'tableCell', 'attrs': {'align': 'center'}, 'content': []},
        ]},
    ]}))
    with pytest.raises(content.ContentError, match='[Вв]ыравнивание'):
        content.validate_content(_doc({'type': 'table', 'content': [
            {'type': 'tableRow', 'content': [
                {'type': 'tableCell', 'attrs': {'align': 'evil'}, 'content': []},
            ]},
        ]}))


def test_heading_level_limited_to_three():
    content.validate_content(_doc({
        'type': 'heading', 'attrs': {'level': 3},
        'content': [{'type': 'text', 'text': 'x'}],
    }))
    # Уровни 4-6 рендер схлопывал в h3: что набрано, то не показано.
    with pytest.raises(content.ContentError, match='[Уу]ровень'):
        content.validate_content(_doc({
            'type': 'heading', 'attrs': {'level': 4},
            'content': [{'type': 'text', 'text': 'x'}],
        }))
