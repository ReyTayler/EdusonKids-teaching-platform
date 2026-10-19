"""
Файловый слой картинок базы знаний.

Приём файла делает общий слой (storage.py) — там хеширование, лимит и
раскладка по каталогам. Здесь остаётся то, что специфично для картинок:
проверка через Pillow и перекодирование в WebP.

Перекодирование живёт в build_variants и вызывается из Celery — воркеру
gunicorn нечего делать с 2-мегабайтным PNG.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageOps

from apps.knowledge import storage

# Историчные имена ошибок: вьюхи и тесты ловят именно их. Ошибки общие для
# картинок и файлов, поэтому объявлены в storage, а здесь только псевдонимы.
ImageRejected = storage.UploadRejected
ImageTooLarge = storage.UploadTooLarge

# Каталог картинок внутри хранилища.
PREFIX = 'knowledge'

# Форматы Pillow → mime. SVG отсутствует намеренно: это XML со встраиваемым
# скриптом, отдача с нашего origin обошла бы CSP.
ALLOWED_FORMATS = {
    'PNG': ('image/png', 'png'),
    'JPEG': ('image/jpeg', 'jpg'),
    'WEBP': ('image/webp', 'webp'),
}

OPTIMIZED_WIDTH = 1600
THUMB_WIDTH = 400
WEBP_QUALITY = 82


@dataclass(frozen=True)
class StoredImage:
    sha256: str
    relative_path: str
    mime: str
    byte_size: int
    width: int
    height: int


@dataclass(frozen=True)
class Variants:
    optimized_path: str
    thumb_path: str


def media_root() -> Path:
    return storage.media_root()


def relative_path(sha256: str, ext: str) -> str:
    """knowledge/ab/cd/<sha256>.<ext> — два уровня шардирования по hex."""
    return storage.sharded_path(PREFIX, sha256, ext)


def variant_path(sha256: str, suffix: str) -> str:
    return storage.sharded_path(PREFIX, sha256, f'{suffix}.webp')


def absolute_path(rel_path: str) -> Path:
    return storage.absolute_path(rel_path)


def store_upload(stream, original_name: str) -> StoredImage:
    """
    Принять картинку. Бросает ImageRejected / ImageTooLarge.

    original_name не используется намеренно: у картинки имя декоративно, а тип
    определяется содержимым. Параметр оставлен ради совместимости вызова с
    приёмом файлов, где имя, наоборот, значимо.
    """
    blob = storage.store_upload(
        stream,
        prefix=PREFIX,
        max_bytes=settings.KNOWLEDGE_MAX_IMAGE_BYTES,
        probe=_probe,
        original_name=original_name,
    )
    return StoredImage(
        sha256=blob.sha256,
        relative_path=blob.relative_path,
        mime=blob.mime,
        byte_size=blob.byte_size,
        width=blob.extra['width'],
        height=blob.extra['height'],
    )


def _probe(path: str) -> tuple[str, str, dict]:
    """Определить формат и размеры. Расширению файла не верим — только Pillow."""
    try:
        with Image.open(path) as im:
            fmt = im.format
            width, height = im.size
    except Exception as exc:                       # noqa: BLE001 — любой сбой = отказ
        raise ImageRejected('Не удалось распознать формат изображения.') from exc

    if fmt not in ALLOWED_FORMATS:
        raise ImageRejected(
            f'Неподдерживаемый формат {fmt!r}: поддерживаются PNG, JPEG, WebP.'
        )
    if width * height > settings.KNOWLEDGE_MAX_IMAGE_PIXELS:
        raise ImageTooLarge('Слишком большое изображение по числу пикселей.')

    mime, ext = ALLOWED_FORMATS[fmt]
    return mime, ext, {'width': width, 'height': height}


def build_variants(sha256: str, source_rel_path: str) -> Variants:
    """
    Сделать WebP-варианты. Вызывается из Celery, в запросе не используется.

    EXIF срезается: в нём геотеги и модель устройства. Ориентация при этом
    применяется до сброса — иначе фото с телефона осталось бы лежать боком.

    Прозрачность сохраняется (WebP её умеет). Раньше всё переводилось в RGB,
    и прозрачный фон PNG превращался в чёрный: у прозрачных пикселей цвет
    обычно (0, 0, 0), и без альфа-канала он проступал.
    """
    source = absolute_path(source_rel_path)

    with Image.open(source) as im:
        im = ImageOps.exif_transpose(im)
        alpha = _has_transparency(im)
        im = im.convert('RGBA' if alpha else 'RGB')
        # Вариант с прозрачностью — под своим именем. Картинки, у которых
        # вариант уже построен по-старому (чёрный фон), браузеры держат в кэше
        # по имени файла и ETag; новое имя гарантирует, что они увидят новый
        # файл. Непрозрачные картинки сохраняют прежние имена — пересобирать и
        # перекачивать их незачем.
        mark = 'a' if alpha else ''
        optimized_rel = variant_path(sha256, f'w{OPTIMIZED_WIDTH}{mark}')
        thumb_rel = variant_path(sha256, f'w{THUMB_WIDTH}{mark}')
        _save_resized(im, absolute_path(optimized_rel), OPTIMIZED_WIDTH)
        _save_resized(im, absolute_path(thumb_rel), THUMB_WIDTH)

    return Variants(optimized_path=optimized_rel, thumb_path=thumb_rel)


def has_transparency(rel_path: str) -> bool:
    """Есть ли в картинке хоть один прозрачный пиксель."""
    with Image.open(absolute_path(rel_path)) as im:
        return _has_transparency(im)


def _has_transparency(im: Image.Image) -> bool:
    """
    Прозрачность бывает двух видов: альфа-канал (RGBA, LA, PA) и номер
    прозрачного цвета в палитре или в RGB/L-картинке (info['transparency']).
    Альфа-канал, где все пиксели непрозрачны, прозрачностью не считается:
    такие PNG сохраняют многие редакторы, и тащить для них лишний канал в
    WebP — пустая трата байтов.
    """
    if im.mode not in ('RGBA', 'LA', 'PA') and 'transparency' not in im.info:
        return False
    low, _high = im.convert('RGBA').getchannel('A').getextrema()
    return low < 255


def _save_resized(im: Image.Image, target: Path, max_width: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if im.width > max_width:
        height = round(im.height * max_width / im.width)
        resized = im.resize((max_width, height), Image.LANCZOS)
    else:
        resized = im.copy()
    # exif не передаём — метаданные не попадают в результат.
    resized.save(target, format='WEBP', quality=WEBP_QUALITY, method=4)
