"""
Общий приём загружаемого файла для раздела «База знаний».

Хранилище адресуется содержимым: имя файла — sha256 загруженных байтов, каталог
шардируется по первым двум парам hex-символов. Отсюда два свойства, на которые
опирается всё остальное: одинаковые файлы не дублируются на диске, а содержимое
по конкретному пути никогда не меняется — поэтому ETag можно ставить
неизменяемый.

Модуль ничего не знает ни про картинки, ни про документы: он принимает поток,
считает хеш, проверяет размер и кладёт файл на место. Чем именно является
принятое — решает `probe`, переданный вызывающим. Картинки проверяют себя через
Pillow (`images.py`), файлы — по расширению и сигнатуре (`file_types.py`).

Вынесено сюда, а не продублировано: разойдись две копии — получим разное
поведение при переполнении диска и при обрыве загрузки, то есть ровно в тех
случаях, которые никто не проверяет руками.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from django.conf import settings

_CHUNK = 64 * 1024

# Режим файла в хранилище: чтение владельцу (приложение) и группе —
# на проде это www-data, под которым nginx отдаёт байты по
# X-Accel-Redirect. Мир не должен читать: каталог всё равно закрыт (2750),
# но полагаться только на права каталога — лишнее допущение.
STORED_FILE_MODE = 0o640


class UploadRejected(ValueError):
    """Содержимое не подошло — вьюха отдаёт 415."""


class UploadTooLarge(ValueError):
    """Файл больше лимита — вьюха отдаёт 413."""


@dataclass(frozen=True)
class StoredBlob:
    """Принятый файл: где лежит, чем является, сколько весит."""

    sha256: str
    relative_path: str
    mime: str
    byte_size: int
    # Что вернул probe сверх mime и расширения: картинки кладут сюда размеры.
    extra: dict


def media_root() -> Path:
    return Path(settings.KNOWLEDGE_MEDIA_ROOT)


def absolute_path(rel_path: str) -> Path:
    return media_root() / rel_path


def sharded_path(prefix: str, sha256: str, ext: str) -> str:
    """<prefix>/ab/cd/<sha256>.<ext> — два уровня шардирования по hex."""
    return f'{prefix}/{sha256[0:2]}/{sha256[2:4]}/{sha256}.{ext}'


def store_upload(
    stream,
    *,
    prefix: str,
    max_bytes: int,
    probe: Callable[[str], tuple[str, str, dict]],
    original_name: Optional[str] = None,
) -> StoredBlob:
    """
    Принять файл: записать во временный, посчитать sha256 потоково, проверить
    содержимое, переложить на постоянное место.

    probe получает путь к временному файлу и возвращает `(mime, ext, extra)`.
    Бросить он должен UploadRejected — тогда файл не сохраняется.

    Размер проверяется ПО ХОДУ чтения, а не после: иначе, чтобы отказать в
    приёме гигабайта, пришлось бы сперва записать этот гигабайт на диск.
    """
    digest = hashlib.sha256()
    size = 0

    # Временный файл — ВНУТРИ хранилища, не в /tmp. Две причины. Во-первых,
    # перенос на постоянное место становится rename в пределах одной ФС —
    # атомарным и без копирования: под systemd с PrivateTmp каталог /tmp это
    # tmpfs, то есть каждая загрузка иначе проходит через оперативную память
    # целиком. Во-вторых, файл сразу наследует группу от setgid-каталога
    # хранилища (на проде www-data), и nginx может его прочитать.
    media_root().mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix='kb-upload-', dir=str(media_root()))
    try:
        with os.fdopen(tmp_fd, 'wb') as tmp:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge(
                        f'Файл больше {max_bytes // (1024 * 1024)} МБ.'
                    )
                digest.update(chunk)
                tmp.write(chunk)

        if size == 0:
            raise UploadRejected('Файл пустой.')

        mime, ext, extra = probe(tmp_name)
        sha256 = digest.hexdigest()
        rel = sharded_path(prefix, sha256, ext)
        target = absolute_path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Файл с таким содержимым мог быть загружен раньше — перезапись
        # безопасна, содержимое то же самое по построению.
        shutil.move(tmp_name, target)
        tmp_name = None
        # Явный режим обязателен: tempfile.mkstemp создаёт файл доступным ТОЛЬКО
        # владельцу (0600 по документации), и shutil.move этот режим сохраняет.
        # Без chmod готовый файл читает лишь процесс приложения. Локально это
        # незаметно — при пустом KNOWLEDGE_X_ACCEL_PREFIX файл отдаёт сам Django,
        # то есть тот же пользователь. На проде байты отдаёт nginx под www-data:
        # он упирается в Permission denied, и картинка не грузится, хотя загрузка
        # прошла успешно (инцидент 22.08.2026). Производные варианты (webp) этим
        # не страдали — их пишет Pillow под обычной umask.
        os.chmod(target, STORED_FILE_MODE)
        return StoredBlob(
            sha256=sha256, relative_path=rel, mime=mime,
            byte_size=size, extra=extra,
        )
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)
