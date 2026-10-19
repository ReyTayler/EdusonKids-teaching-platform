"""
Что разрешено прикреплять к документу базы знаний.

Проверка двойная: расширение И сигнатура в первых байтах. По отдельности каждая
дырявая — расширение пропускает исполняемый файл, переименованный в `.pdf`, а
сигнатура пропускает `.pdf`, внутри которого разметка. Совпасть должны обе.

Список белый, а не чёрный, сознательно: забытое в чёрном списке расширение —
это дыра, забытое в белом — всего лишь неудобство, о котором сразу скажут.

Формы Office с макросами (.docm, .xlsm, .pptm) сюда не входят: это те же
документы, но со встроенным исполняемым кодом, и раздавать их через справочник
регламентов нельзя.
"""
from __future__ import annotations

import os

from apps.knowledge.storage import UploadRejected

# Сигнатуры (magic bytes) семейств форматов.
_PDF = b'%PDF-'
# OOXML (docx/xlsx/pptx) и OpenDocument — это zip-архивы, отличить их от
# обычного zip по сигнатуре нельзя. Это не проблема: zip разрешён и так.
_ZIP = b'PK\x03\x04'
# Старые форматы Office (doc/xls/ppt) — контейнер OLE2.
_OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_RTF = b'{\\rtf'

# Расширение → (mime, требуемые сигнатуры).
# Пустой кортеж сигнатур означает «проверить нечем»: у обычного текста
# опознавательных байтов не существует. При принудительном скачивании
# (Content-Disposition: attachment) текстовый файл безопасен.
ALLOWED: dict[str, tuple[str, tuple[bytes, ...]]] = {
    'pdf':  ('application/pdf', (_PDF,)),

    'docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', (_ZIP,)),
    'xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', (_ZIP,)),
    'pptx': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', (_ZIP,)),

    'doc':  ('application/msword', (_OLE,)),
    'xls':  ('application/vnd.ms-excel', (_OLE,)),
    'ppt':  ('application/vnd.ms-powerpoint', (_OLE,)),

    'odt':  ('application/vnd.oasis.opendocument.text', (_ZIP,)),
    'ods':  ('application/vnd.oasis.opendocument.spreadsheet', (_ZIP,)),
    'odp':  ('application/vnd.oasis.opendocument.presentation', (_ZIP,)),

    'rtf':  ('application/rtf', (_RTF,)),
    'zip':  ('application/zip', (_ZIP,)),

    'txt':  ('text/plain', ()),
    'csv':  ('text/csv', ()),
}

# Сколько байт читаем ради сигнатуры. Все известные нам сигнатуры короче.
_HEAD_BYTES = 16

# Потолок длины имени: значение уходит в колонку и в заголовок ответа.
MAX_NAME_CHARS = 255


def extension_of(name: str) -> str:
    """Расширение в нижнем регистре, без точки. Пустая строка, если его нет."""
    return os.path.splitext(name or '')[1].lstrip('.').lower()


def probe_by_name(original_name: str):
    """
    Собрать проверку содержимого под конкретное имя файла.

    Возвращает функцию в том виде, какой ждёт storage.store_upload: она
    получает путь к временному файлу и отдаёт `(mime, ext, extra)`.

    Проверка привязана к имени, потому что тип определяется парой
    «расширение + сигнатура», а имя известно только вызывающему.
    """
    ext = extension_of(original_name)

    def probe(path: str) -> tuple[str, str, dict]:
        if ext not in ALLOWED:
            raise UploadRejected(
                f'Нельзя прикрепить файл с расширением {ext or "без расширения"!r}. '
                f'Разрешены: {", ".join(sorted(ALLOWED))}.'
            )
        mime, signatures = ALLOWED[ext]
        if signatures:
            with open(path, 'rb') as fh:
                head = fh.read(_HEAD_BYTES)
            # Сигнатура обязана стоять в НАЧАЛЕ файла. Поиск по всему
            # содержимому пропустил бы что угодно с «%PDF-» где-то в середине.
            if not any(head.startswith(sig) for sig in signatures):
                raise UploadRejected(
                    f'Содержимое файла не похоже на {ext.upper()}. '
                    'Проверьте, что расширение соответствует формату.'
                )
        return mime, ext, {}

    return probe


def check_name(original_name: str) -> str:
    """
    Привести имя файла к безопасному для хранения виду.

    Путь отбрасывается целиком: браузеры некоторых версий шлют полный путь, а
    из него можно собрать выход за пределы каталога. Управляющие символы
    вырезаются — они попадают в заголовок ответа при скачивании.
    """
    name = (original_name or '').replace('\\', '/').split('/')[-1]
    name = ''.join(ch for ch in name if ch.isprintable())
    name = name.strip()
    if not name:
        raise UploadRejected('У файла нет имени.')
    return name[:MAX_NAME_CHARS]
