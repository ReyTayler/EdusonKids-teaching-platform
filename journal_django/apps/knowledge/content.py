"""
Чистые функции над TipTap-JSON. В БД не ходят, HTTP не знают.

Это единственный барьер между клиентом и колонкой content: всё, что не прошло
validate_content, в базу не попадает. Безопасность рендера обеспечивается здесь,
на входе, а не санитайзером на выходе — читатель получает JSON, из которого
фронт строит React-элементы, а не HTML-строку.
"""
from __future__ import annotations

import json

# Типы узлов, которые умеет показать DocumentView. Расширять этот список можно
# только вместе с рендерером — иначе документ сохранится, но не отобразится.
ALLOWED_NODES = frozenset({
    'doc', 'paragraph', 'text', 'heading',
    'bulletList', 'orderedList', 'listItem',
    'taskList', 'taskItem',
    'blockquote', 'codeBlock', 'horizontalRule', 'hardBreak',
    'callout',
    'table', 'tableRow', 'tableHeader', 'tableCell',
    'knowledgeImage', 'knowledgeFile',
})

# Виды выноски (callout). Тон определяет цвет и значок при чтении, поэтому
# список закрытый: произвольная строка ушла бы в имя класса.
ALLOWED_CALLOUT_TONES = frozenset({'info', 'tip', 'important', 'error', 'warning'})

# Языки подсветки блока кода. Набор ограничен теми, что реально встречаются в
# наших регламентах, и обязан совпадать с набором, зарегистрированным в
# lowlight на фронте: незнакомое значение там просто не подсветится, а здесь
# лучше не пустить его в документ вовсе.
ALLOWED_CODE_LANGUAGES = frozenset({
    'plaintext', 'javascript', 'typescript', 'python', 'sql',
    'bash', 'json', 'html', 'css',
})

ALLOWED_MARKS = frozenset({
    'bold', 'italic', 'underline', 'strike', 'code', 'link',
    # textStyle несёт выбор шрифта (атрибут fontFamily).
    'textStyle',
})

# Схемы ссылок, разрешённые в mark link. javascript: и data: отсекаются.
ALLOWED_LINK_PREFIXES = ('https://', 'http://', 'mailto:', '/')

# Выравнивание абзаца/заголовка (атрибут textAlign).
ALLOWED_TEXT_ALIGN = frozenset({'left', 'center', 'right', 'justify'})

# Атрибуты, которые редактор ставит любому блоку независимо от его типа, — их
# проверяет отдельная функция, а закрытые списки конкретных узлов обязаны их
# пропускать. Забыть об этом здесь — значит запретить сохранение узла целиком:
# blockId вешается на картинку расширением UniqueID (editorExtensions.ts,
# ID_TYPES), и без этого исключения любой документ с картинкой отвергался.
COMMON_ATTRS = frozenset({'blockId'})

# Атрибуты узла картинки — закрытый список, как у textStyle.
# width/height — размеры оригинала в пикселях: читалка ставит их атрибутами
# <img>, чтобы браузер зарезервировал место до загрузки файла и текст не
# прыгал. Значения попадают в разметку, поэтому проверяются здесь.
ALLOWED_IMAGE_ATTRS = frozenset({'imageId', 'alt', 'width', 'height'}) | COMMON_ATTRS
MAX_IMAGE_ALT_CHARS = 300
MAX_IMAGE_SIDE = 100_000

# Атрибуты прикреплённого файла. name/size/mime продублированы в узле, чтобы
# читалка нарисовала карточку без дополнительного запроса — тот же приём, что у
# картинки с width/height. При СКАЧИВАНИИ они не используются: имя берётся из
# базы, поэтому подмена в JSON меняет лишь надпись на карточке.
ALLOWED_FILE_ATTRS = frozenset({'fileId', 'name', 'size', 'mime'}) | COMMON_ATTRS
MAX_FILE_NAME_CHARS = 255
# С запасом к KNOWLEDGE_MAX_FILE_BYTES: это подпись под карточкой, а не
# ограничение приёма — настоящий предел стоит на загрузке.
MAX_FILE_SIZE_BYTES = 1024 * 1024 * 1024
MAX_FILE_MIME_CHARS = 100

# Шрифты, доступные в редакторе. Значение уходит в style="font-family: …",
# поэтому список закрытый: произвольную строку клиент подставить не может.
# Список обязан совпадать с FONT_CHOICES на фронте (editorFonts.ts).
ALLOWED_FONTS = frozenset({
    'Inter, sans-serif',
    'Georgia, serif',
    'ui-monospace, SFMono-Regular, Menlo, monospace',
    'Arial, Helvetica, sans-serif',
    'Times New Roman, Times, serif',
    'Verdana, Geneva, sans-serif',
})

# Цвет букв и цвет выделения. Хранятся ССЫЛКАМИ НА ТОКЕНЫ, а не кодами цвета:
# токены разные в светлой и тёмной теме, и сохранённый код превратил бы красный
# текст из светлой темы в нечитаемый на тёмной. Значение уходит в инлайновый
# стиль, поэтому список закрытый — произвольную строку клиент подставить не
# может. Обязан совпадать с editorColors.ts (сторож — test_node_lists_in_sync).
ALLOWED_TEXT_COLORS = frozenset({
    'var(--accent)',
    'var(--danger)',
    'var(--success)',
    'var(--warning)',
    'var(--info)',
    'var(--text3)',
})

# Только полупрозрачные заливки: плотный фон потребовал бы менять и цвет букв,
# иначе тёмное по тёмному.
ALLOWED_HIGHLIGHT_COLORS = frozenset({
    'var(--warning-soft)',
    'var(--success-soft)',
    'var(--danger-soft)',
    'var(--info-soft)',
    'var(--accent-soft)',
})

MAX_CONTENT_BYTES = 512 * 1024
MAX_DEPTH = 20


class ContentError(ValueError):
    """Контент не прошёл проверку. Вьюха превращает это в 400 invalid_content."""


def validate_content(
    value, *, allowed_image_ids=frozenset(), allowed_file_ids=frozenset(),
) -> None:
    """
    Проверить документ. Бросает ContentError с человекочитаемым текстом.

    allowed_image_ids и allowed_file_ids — множества id существующих картинок и
    файлов; передаются сервисом, сам модуль в БД не ходит.
    """
    if not isinstance(value, dict) or value.get('type') != 'doc':
        raise ContentError('Корневой узел (root) документа должен быть doc.')

    size = len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    if size > MAX_CONTENT_BYTES:
        raise ContentError(
            f'Превышен размер документа: {size} байт при лимите {MAX_CONTENT_BYTES}.'
        )

    _walk(
        value, depth=0,
        allowed_image_ids=set(allowed_image_ids),
        allowed_file_ids=set(allowed_file_ids),
    )


def _walk(node, *, depth: int, allowed_image_ids: set, allowed_file_ids: set) -> None:
    if depth > MAX_DEPTH:
        raise ContentError(f'Превышена глубина вложенности: максимум {MAX_DEPTH}.')
    if not isinstance(node, dict):
        raise ContentError('Узел документа должен быть объектом.')

    node_type = node.get('type')
    if node_type not in ALLOWED_NODES:
        raise ContentError(f'Неподдерживаемый тип узла: {node_type!r}.')

    # Текст обязан быть строкой. Объект или список тут пройдут дальше и упадут
    # уже на читателе: рендер отдаёт node.text прямо в React, а тот на не-строке
    # роняет всю страницу — один документ ломает просмотр для всех.
    if node_type == 'text' and not isinstance(node.get('text'), str):
        raise ContentError('Текстовый узел должен содержать строку.')

    if node_type == 'heading':
        _check_heading(node)
    if node_type == 'callout':
        _check_callout(node)
    if node_type == 'codeBlock':
        _check_code_block(node)
    if node_type == 'taskItem':
        _check_task_item(node)

    _check_block_id(node)
    _check_align(node)
    _check_block_background(node)
    _check_col_width(node)

    for mark in node.get('marks') or []:
        _check_mark(mark)

    if node_type == 'knowledgeImage':
        _check_image(node, allowed_image_ids)
    if node_type == 'knowledgeFile':
        _check_file(node, allowed_file_ids)

    for child in node.get('content') or []:
        _walk(
            child, depth=depth + 1,
            allowed_image_ids=allowed_image_ids,
            allowed_file_ids=allowed_file_ids,
        )


def _attrs(node) -> dict:
    """
    Атрибуты узла или марки.

    Без проверки типа паттерн `(node.get('attrs') or {}).get(...)` падает с
    AttributeError на любом не-словаре — а вьюха ловит только ContentError,
    и клиент получал бы 500 вместо внятного отказа.
    """
    attrs = node.get('attrs')
    if attrs is None:
        return {}
    if not isinstance(attrs, dict):
        raise ContentError('Атрибуты узла должны быть объектом.')
    return attrs


def _check_heading(node) -> None:
    """Уровни заголовка — только те, что умеет показать рендер (h1–h3)."""
    level = _attrs(node).get('level')
    if level is None:
        return
    if level not in (1, 2, 3):
        raise ContentError(f'Недопустимый уровень заголовка: {level!r}.')


def _check_callout(node) -> None:
    tone = _attrs(node).get('tone')
    if tone in (None, ''):
        return
    if tone not in ALLOWED_CALLOUT_TONES:
        raise ContentError(f'Недопустимый вид выноски: {tone!r}.')


def _check_code_block(node) -> None:
    language = _attrs(node).get('language')
    if language in (None, ''):
        return
    if language not in ALLOWED_CODE_LANGUAGES:
        raise ContentError(f'Недопустимый язык блока кода: {language!r}.')


def _check_task_item(node) -> None:
    checked = _attrs(node).get('checked')
    if checked is not None and not isinstance(checked, bool):
        raise ContentError('Отметка пункта чеклиста должна быть true или false.')


# Идентификатор блока (UniqueID на фронте) — якорь оглавления и опора
# перетаскивания. Формат не задаём, но длину ограничиваем: значение уходит в
# атрибут id разметки.
MAX_BLOCK_ID_CHARS = 64


def _check_block_id(node) -> None:
    block_id = _attrs(node).get('blockId')
    if block_id is None:
        return
    if not isinstance(block_id, str) or not block_id:
        raise ContentError('Идентификатор блока должен быть непустой строкой.')
    if len(block_id) > MAX_BLOCK_ID_CHARS:
        raise ContentError(
            f'Слишком длинный идентификатор блока: максимум {MAX_BLOCK_ID_CHARS}.'
        )
    # Значение попадает в id/href — оставляем только безопасный алфавит.
    if not all(ch.isalnum() or ch in '-_' for ch in block_id):
        raise ContentError(f'Недопустимый идентификатор блока: {block_id!r}.')


def _check_align(node) -> None:
    """Выравнивание — из закрытого списка: значение уходит в инлайновый стиль."""
    attrs = _attrs(node)
    for key in ('textAlign', 'align'):
        # textAlign — у абзацев и заголовков, align — у ячеек таблицы. Оба
        # попадают в инлайновый стиль при чтении, поэтому проверяются одинаково.
        value = attrs.get(key)
        if value in (None, ''):
            continue
        if value not in ALLOWED_TEXT_ALIGN:
            raise ContentError(f'Недопустимое выравнивание: {value!r}.')


def _check_block_background(node) -> None:
    """
    Заливка целого блока (абзац, заголовок).

    Палитра та же, что у выделения текста: и там, и там подложка обязана
    оставаться читаемой под любым цветом букв и в любой теме. Значение уходит в
    инлайновый стиль, поэтому список закрытый.
    """
    value = _attrs(node).get('blockBackground')
    if value in (None, ''):
        return
    if value not in ALLOWED_HIGHLIGHT_COLORS:
        raise ContentError(f'Недопустимый фон блока: {value!r}.')


# Сколько столбцов может накрывать одна ячейка. Ограничение техническое:
# массив ширин приходит от клиента и разворачивается в теги col при чтении.
MAX_COLWIDTH_ENTRIES = 100
MAX_COLWIDTH_PX = 10_000


def _check_col_width(node) -> None:
    """
    Ширины столбцов (атрибут colwidth ячейки).

    Пишет их штатное изменение ширины из @tiptap/extension-table, читалка
    разворачивает в colgroup. Значения уходят в разметку, поэтому проверяются:
    список чисел либо None на месте незаданной ширины.
    """
    value = _attrs(node).get('colwidth')
    if value is None:
        return
    if not isinstance(value, list):
        raise ContentError('Ширины столбцов должны быть списком.')
    if len(value) > MAX_COLWIDTH_ENTRIES:
        raise ContentError(
            f'Слишком много ширин столбцов: максимум {MAX_COLWIDTH_ENTRIES}.'
        )
    for width in value:
        # None — законное значение: столбец, ширину которого не задавали.
        if width is None:
            continue
        if isinstance(width, bool) or not isinstance(width, int):
            raise ContentError(f'Недопустимая ширина столбца: {width!r}.')
        if not 0 < width <= MAX_COLWIDTH_PX:
            raise ContentError(f'Недопустимая ширина столбца: {width!r}.')


def _check_mark(mark) -> None:
    if not isinstance(mark, dict):
        raise ContentError('Марка должна быть объектом.')
    mark_type = mark.get('type')
    if mark_type not in ALLOWED_MARKS:
        raise ContentError(f'Неподдерживаемая марка: {mark_type!r}.')
    if mark_type == 'link':
        _check_link(mark)
    if mark_type == 'textStyle':
        _check_text_style(mark)


def _check_link(mark) -> None:
    href = _attrs(mark).get('href') or ''
    href = str(href)
    # `//чужой-домен` начинается со слэша, но ведёт наружу: браузер трактует
    # такой адрес как внешний с текущей схемой. Пропускать его как «внутреннюю
    # относительную ссылку» — значит дать замаскировать чужой сайт под свой.
    # Обратный слэш часть браузеров нормализует в прямой, поэтому тоже отсекаем.
    if href.startswith(('//', '/\\')):
        raise ContentError(f'Недопустимый href в ссылке: {href!r}.')
    if not href.startswith(ALLOWED_LINK_PREFIXES):
        raise ContentError(f'Недопустимый href в ссылке: {href!r}.')


def _check_text_style(mark) -> None:
    """
    В textStyle пропускаем только выбор из закрытых списков.

    Все три значения попадают в инлайновый стиль. Свободная строка здесь
    означала бы, что автор документа подставляет читателям произвольный CSS.
    """
    attrs = _attrs(mark)
    unknown = set(attrs) - {'fontFamily', 'color', 'backgroundColor'}
    if unknown:
        raise ContentError(f'Неподдерживаемые атрибуты стиля: {sorted(unknown)}.')

    font = attrs.get('fontFamily')
    if font not in (None, '') and font not in ALLOWED_FONTS:
        raise ContentError(f'Недопустимый шрифт: {font!r}.')

    color = attrs.get('color')
    if color not in (None, '') and color not in ALLOWED_TEXT_COLORS:
        raise ContentError(f'Недопустимый цвет текста: {color!r}.')

    highlight = attrs.get('backgroundColor')
    if highlight not in (None, '') and highlight not in ALLOWED_HIGHLIGHT_COLORS:
        raise ContentError(f'Недопустимый цвет выделения: {highlight!r}.')


def _check_image(node, allowed_image_ids: set) -> None:
    attrs = _attrs(node)
    unknown = set(attrs) - ALLOWED_IMAGE_ATTRS
    if unknown:
        raise ContentError(f'Неподдерживаемые атрибуты картинки: {sorted(unknown)}.')

    image_id = attrs.get('imageId')
    # bool — подкласс int, а True == 1: без отдельной проверки узел с
    # imageId: true прошёл бы и как «число», и как ссылка на картинку №1.
    if isinstance(image_id, bool) or not isinstance(image_id, int):
        raise ContentError('У картинки отсутствует числовой imageId.')
    if image_id not in allowed_image_ids:
        raise ContentError(f'Картинка {image_id} не найдена.')

    alt = attrs.get('alt')
    if alt is not None and not isinstance(alt, str):
        raise ContentError('Подпись картинки (alt) должна быть строкой.')
    if isinstance(alt, str) and len(alt) > MAX_IMAGE_ALT_CHARS:
        raise ContentError(
            f'Слишком длинная подпись картинки: максимум {MAX_IMAGE_ALT_CHARS} символов.'
        )

    for key in ('width', 'height'):
        value = attrs.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContentError(f'Размер картинки ({key}) должен быть целым числом.')
        if not 0 < value <= MAX_IMAGE_SIDE:
            raise ContentError(f'Недопустимый размер картинки ({key}): {value!r}.')


def _check_file(node, allowed_file_ids: set) -> None:
    attrs = _attrs(node)
    unknown = set(attrs) - ALLOWED_FILE_ATTRS
    if unknown:
        raise ContentError(f'Неподдерживаемые атрибуты файла: {sorted(unknown)}.')

    file_id = attrs.get('fileId')
    # bool — подкласс int, как и у картинки: True прошёл бы и как число,
    # и как ссылка на файл №1.
    if isinstance(file_id, bool) or not isinstance(file_id, int):
        raise ContentError('У файла отсутствует числовой fileId.')
    if file_id not in allowed_file_ids:
        raise ContentError(f'Файл {file_id} не найден.')

    name = attrs.get('name')
    if name is not None:
        if not isinstance(name, str):
            raise ContentError('Имя файла должно быть строкой.')
        if len(name) > MAX_FILE_NAME_CHARS:
            raise ContentError(
                f'Слишком длинное имя файла: максимум {MAX_FILE_NAME_CHARS} символов.'
            )

    size = attrs.get('size')
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, int):
            raise ContentError('Размер файла должен быть целым числом.')
        if not 0 < size <= MAX_FILE_SIZE_BYTES:
            raise ContentError(f'Недопустимый размер файла: {size!r}.')

    mime = attrs.get('mime')
    if mime is not None:
        if not isinstance(mime, str):
            raise ContentError('Тип файла должен быть строкой.')
        if len(mime) > MAX_FILE_MIME_CHARS:
            raise ContentError('Слишком длинный тип файла.')


def collect_image_ids(value) -> set[int]:
    """Собрать id всех картинок документа — для пересборки таблицы использований."""
    return _collect_ids(value, 'knowledgeImage', 'imageId')


def collect_file_ids(value) -> set[int]:
    """Собрать id всех прикреплённых файлов — для той же пересборки."""
    return _collect_ids(value, 'knowledgeFile', 'fileId')


def _collect_ids(value, node_type: str, attr: str) -> set[int]:
    found: set[int] = set()
    _collect(value, found, node_type, attr)
    return found


def _collect(node, found: set[int], node_type: str, attr: str) -> None:
    if not isinstance(node, dict):
        return
    if node.get('type') == node_type:
        # Сбор идёт и до валидации (чтобы узнать, какие записи проверять),
        # поэтому здесь нельзя рассчитывать на корректную форму узла.
        attrs = node.get('attrs')
        value = attrs.get(attr) if isinstance(attrs, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            found.add(value)
    for child in node.get('content') or []:
        _collect(child, found, node_type, attr)


def extract_plain_text(value) -> str:
    """
    Плоский текст документа: блочные узлы разделяются переводом строки,
    инлайновые куски внутри блока склеиваются.

    Используется для превью в списке; заодно это готовый источник для поиска,
    если он понадобится позже.
    """
    lines: list[str] = []
    _lines(value, lines)
    return '\n'.join(line for line in lines if line)


# Узлы, после которых текст переносится на новую строку.
_BLOCK_NODES = frozenset({
    'paragraph', 'heading', 'blockquote', 'codeBlock', 'listItem',
    'tableCell', 'tableHeader',
})


def _lines(node, lines: list[str]) -> None:
    if not isinstance(node, dict):
        return
    if node.get('type') in _BLOCK_NODES:
        text = _inline_text(node)
        if text:
            lines.append(text)
        # Внутри блока могут быть вложенные блоки (список в списке, цитата
        # в цитате) — их надо обойти отдельно, иначе текст потеряется.
        for child in node.get('content') or []:
            if isinstance(child, dict) and child.get('type') in _BLOCK_NODES:
                _lines(child, lines)
        return
    for child in node.get('content') or []:
        _lines(child, lines)


def _inline_text(node) -> str:
    parts: list[str] = []
    for child in node.get('content') or []:
        if not isinstance(child, dict):
            continue
        if child.get('type') == 'text':
            parts.append(child.get('text') or '')
        elif child.get('type') == 'hardBreak':
            parts.append(' ')
    return ''.join(parts).strip()
