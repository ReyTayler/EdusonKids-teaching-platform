"""Тесты файлового слоя картинок: пути, хеш, приём файла, оптимизация, API."""
from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from PIL import Image

from apps.knowledge import images


def _png_bytes(width: int = 20, height: int = 10, color=(255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, format='PNG')
    return buf.getvalue()


def _rgba_png_bytes(width: int = 40, height: int = 20, *, transparent: bool = True) -> bytes:
    """
    Левая половина — красная и непрозрачная, правая — фон. Прозрачный фон
    намеренно (0, 0, 0, 0): так его отдаёт большинство редакторов, и именно
    этот чёрный проступал после отбрасывания прозрачности.
    """
    background = (0, 0, 0, 0) if transparent else (0, 0, 255, 255)
    im = Image.new('RGBA', (width, height), background)
    im.paste((255, 0, 0, 255), (0, 0, width // 2, height))
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    return buf.getvalue()


# --- пути ------------------------------------------------------------------

def test_relative_path_shards_by_first_hex_pairs():
    path = images.relative_path('abcdef' + '0' * 58, 'png')
    assert path == 'knowledge/ab/cd/' + 'abcdef' + '0' * 58 + '.png'


def test_variant_path_appends_suffix():
    sha = 'ab' + '0' * 62
    assert images.variant_path(sha, 'w400') == f'knowledge/ab/00/{sha}.w400.webp'


# --- приём файла -----------------------------------------------------------

def test_store_upload_writes_file_and_returns_metadata(tmp_path):
    payload = _png_bytes(30, 15)
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        meta = images.store_upload(io.BytesIO(payload), 'скрин.png')

    assert meta.mime == 'image/png'
    assert meta.width == 30 and meta.height == 15
    assert meta.byte_size == len(payload)
    assert len(meta.sha256) == 64
    assert (tmp_path / meta.relative_path).exists()


def test_store_upload_is_deterministic_by_content(tmp_path):
    payload = _png_bytes()
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        first = images.store_upload(io.BytesIO(payload), 'a.png')
        second = images.store_upload(io.BytesIO(payload), 'b.png')
    assert first.sha256 == second.sha256


def test_store_upload_rejects_non_image(tmp_path):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        with pytest.raises(images.ImageRejected, match='формат'):
            images.store_upload(io.BytesIO(b'not an image at all'), 'x.png')


def test_store_upload_rejects_svg(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        with pytest.raises(images.ImageRejected, match='формат'):
            images.store_upload(io.BytesIO(svg), 'x.svg')


def test_store_upload_rejects_oversized(tmp_path):
    payload = _png_bytes()
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_MAX_IMAGE_BYTES=10):
        with pytest.raises(images.ImageTooLarge):
            images.store_upload(io.BytesIO(payload), 'x.png')


# --- оптимизация -----------------------------------------------------------

def test_build_variants_creates_webp_files(tmp_path):
    payload = _png_bytes(2000, 1000)
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        meta = images.store_upload(io.BytesIO(payload), 'big.png')
        result = images.build_variants(meta.sha256, meta.relative_path)

    optimized = tmp_path / result.optimized_path
    thumb = tmp_path / result.thumb_path
    assert optimized.exists() and thumb.exists()
    with Image.open(optimized) as im:
        assert im.width == 1600          # ужато по длинной стороне
        assert im.format == 'WEBP'
    with Image.open(thumb) as im:
        assert im.width == 400


def test_build_variants_keeps_transparency(tmp_path):
    """Прозрачный фон остаётся прозрачным, а не становится чёрным."""
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        meta = images.store_upload(io.BytesIO(_rgba_png_bytes()), 'logo.png')
        result = images.build_variants(meta.sha256, meta.relative_path)

    for rel in (result.optimized_path, result.thumb_path):
        with Image.open(tmp_path / rel) as out:
            assert out.mode == 'RGBA'
            assert out.getpixel((39, 0))[3] == 0         # фон прозрачен
            assert out.getpixel((0, 0))[3] == 255        # рисунок непрозрачен
    # Своё имя файла у варианта с прозрачностью — иначе браузер, закэшировавший
    # прежний чёрный вариант по тому же адресу и ETag, так бы его и показывал.
    assert result.optimized_path.endswith('.w1600a.webp')
    assert result.thumb_path.endswith('.w400a.webp')


def test_build_variants_opaque_rgba_stays_rgb(tmp_path):
    """Альфа-канал без единого прозрачного пикселя — обычная картинка."""
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        meta = images.store_upload(
            io.BytesIO(_rgba_png_bytes(transparent=False)), 'flat.png',
        )
        result = images.build_variants(meta.sha256, meta.relative_path)

    with Image.open(tmp_path / result.optimized_path) as out:
        assert out.mode == 'RGB'
    assert result.optimized_path.endswith('.w1600.webp')


def test_build_variants_keeps_palette_transparency(tmp_path):
    """PNG с палитрой хранит прозрачность не каналом, а номером цвета."""
    im = Image.new('P', (20, 10), 0)
    im.putpalette([0, 0, 0, 255, 0, 0] + [0] * (256 * 3 - 6))
    im.paste(1, (0, 0, 10, 10))
    buf = io.BytesIO()
    im.save(buf, format='PNG', transparency=0)

    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        meta = images.store_upload(io.BytesIO(buf.getvalue()), 'icon.png')
        result = images.build_variants(meta.sha256, meta.relative_path)

    with Image.open(tmp_path / result.optimized_path) as out:
        assert out.mode == 'RGBA'
        assert out.getpixel((19, 0))[3] == 0


def test_build_variants_strips_exif(tmp_path):
    buf = io.BytesIO()
    im = Image.new('RGB', (100, 50), (0, 128, 0))
    exif = im.getexif()
    exif[271] = 'TestCamera'             # Make
    im.save(buf, format='JPEG', exif=exif)

    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        meta = images.store_upload(io.BytesIO(buf.getvalue()), 'photo.jpg')
        result = images.build_variants(meta.sha256, meta.relative_path)

    with Image.open(tmp_path / result.optimized_path) as out:
        assert dict(out.getexif()) == {}


# ---------------------------------------------------------------------------
# API загрузки и отдачи
# ---------------------------------------------------------------------------

IMAGES = '/api/admin/knowledge/images'
DOCS = '/api/admin/knowledge/documents'
SECTIONS = '/api/admin/knowledge/sections'


def _upload(client, tmp_path, name='__test_kb_a.png', payload=None):
    file = SimpleUploadedFile(name, payload or _png_bytes(), content_type='image/png')
    return client.post(IMAGES, {'file': file}, format='multipart')


@pytest.mark.django_db
def test_upload_returns_pending_image(admin_client, tmp_path, kb_clean):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        resp = _upload(admin_client, tmp_path)
    assert resp.status_code == 201
    body = resp.json()
    assert body['optimize_state'] in ('pending', 'ready')
    assert body['width'] == 20 and body['height'] == 10


@pytest.mark.django_db
def test_upload_deduplicates_by_content(admin_client, tmp_path, kb_clean):
    payload = _png_bytes(33, 22)
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        first = _upload(admin_client, tmp_path, '__test_kb_1.png', payload).json()
        second = _upload(admin_client, tmp_path, '__test_kb_2.png', payload).json()
    assert first['id'] == second['id']


@pytest.mark.django_db
def test_manager_cannot_upload(manager_client, tmp_path, kb_clean):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        assert _upload(manager_client, tmp_path).status_code == 403


@pytest.mark.django_db
def test_upload_rejects_svg(admin_client, tmp_path, kb_clean):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    file = SimpleUploadedFile('__test_kb_x.svg', svg, content_type='image/svg+xml')
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        resp = admin_client.post(IMAGES, {'file': file}, format='multipart')
    assert resp.status_code == 415


@pytest.mark.django_db
def test_upload_rejects_oversized(admin_client, tmp_path, kb_clean):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_MAX_IMAGE_BYTES=10):
        resp = _upload(admin_client, tmp_path)
    assert resp.status_code == 413


@pytest.mark.django_db
def test_serve_uses_x_accel_redirect(admin_client, tmp_path, kb_clean):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        image = _upload(admin_client, tmp_path).json()
        section = admin_client.post(
            SECTIONS, {'title': '__test_kb_раздел'}, format='json',
        ).json()
        doc = admin_client.post(
            DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
        ).json()
        admin_client.patch(
            f"{DOCS}/{doc['id']}",
            {'content': {'type': 'doc', 'content': [
                {'type': 'knowledgeImage', 'attrs': {'imageId': image['id']}},
            ]}},
            format='json',
        )
        with override_settings(KNOWLEDGE_X_ACCEL_PREFIX='/internal-media'):
            resp = admin_client.get(f"{IMAGES}/{image['id']}")

    assert resp.status_code == 200
    assert resp['X-Accel-Redirect'].startswith('/internal-media/knowledge/')
    assert resp.content == b''          # байты отдаёт nginx, не Python


@pytest.mark.django_db
def test_serve_denied_when_no_visible_document(
    admin_client, teacher_client, tmp_path, kb_clean,
):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        image = _upload(admin_client, tmp_path).json()
        section = admin_client.post(
            SECTIONS, {'title': '__test_kb_раздел'}, format='json',
        ).json()
        doc = admin_client.post(
            DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
        ).json()
        admin_client.patch(
            f"{DOCS}/{doc['id']}",
            {'content': {'type': 'doc', 'content': [
                {'type': 'knowledgeImage', 'attrs': {'imageId': image['id']}},
            ]}},
            format='json',
        )
        # Документ остался черновиком без ролей → преподавателю картинка не видна.
        resp = teacher_client.get(f"{IMAGES}/{image['id']}")

    assert resp.status_code == 404


@pytest.mark.django_db
def test_serve_falls_back_to_original_when_not_optimized(
    admin_client, tmp_path, kb_clean,
):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        image = _upload(admin_client, tmp_path).json()
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE knowledge_images SET optimize_state='pending',"
                ' optimized_path=NULL WHERE id = %s', [image['id']],
            )
        section = admin_client.post(
            SECTIONS, {'title': '__test_kb_раздел'}, format='json',
        ).json()
        doc = admin_client.post(
            DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
        ).json()
        admin_client.patch(
            f"{DOCS}/{doc['id']}",
            {'content': {'type': 'doc', 'content': [
                {'type': 'knowledgeImage', 'attrs': {'imageId': image['id']}},
            ]}},
            format='json',
        )
        with override_settings(KNOWLEDGE_X_ACCEL_PREFIX='/internal-media'):
            resp = admin_client.get(f"{IMAGES}/{image['id']}?variant=optimized")

    assert resp.status_code == 200
    # Отдан оригинал: путь заканчивается на .png, а не на .webp
    assert resp['X-Accel-Redirect'].endswith('.png')
    # Фолбэк живёт до готовности WebP — сутки его кэшировать нельзя.
    assert resp['Cache-Control'] == 'private, max-age=60'


@pytest.mark.django_db
def test_etag_differs_between_fallback_and_ready_variant(
    admin_client, tmp_path, kb_clean,
):
    """
    ETag обязан меняться, когда по тому же адресу поехали другие байты.

    ?variant=optimized сначала отдаёт оригинал, а после Celery — WebP. С ETag
    из одного лишь хеша оригинала браузер на проверке получал бы «не
    изменилось» и оставался с тяжёлым PNG навсегда.
    """
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        image = _upload(admin_client, tmp_path).json()
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE knowledge_images SET optimize_state='pending',"
                ' optimized_path=NULL WHERE id = %s', [image['id']],
            )
        fallback = admin_client.get(f"{IMAGES}/{image['id']}?variant=optimized")

        variants = images.build_variants(
            *_sha_and_path(image['id']),
        )
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE knowledge_images SET optimize_state='ready',"
                ' optimized_path=%s WHERE id = %s',
                [variants.optimized_path, image['id']],
            )
        ready = admin_client.get(f"{IMAGES}/{image['id']}?variant=optimized")

    assert fallback.status_code == ready.status_code == 200
    assert fallback['ETag'] != ready['ETag']
    assert ready['Cache-Control'] == 'private, max-age=86400'


def _sha_and_path(image_id: int) -> tuple[str, str]:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT sha256, original_path FROM knowledge_images WHERE id = %s',
            [image_id],
        )
        return cur.fetchone()


@pytest.mark.django_db
def test_etag_changes_when_variant_file_changes(admin_client, tmp_path, kb_clean):
    """
    Пересобранный вариант — новый файл, и ETag обязан смениться вместе с ним.
    Иначе браузер на проверке получил бы «не изменилось» и навсегда остался со
    старыми байтами — так застрял бы чёрный фон у прозрачных картинок.
    """
    with override_settings(
        KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_X_ACCEL_PREFIX='/internal-media',
    ):
        image = _upload(admin_client, tmp_path).json()
        etags = []
        for rel in ('knowledge/aa/bb/x.w1600.webp', 'knowledge/aa/bb/x.w1600a.webp'):
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_images SET optimize_state='ready',"
                    ' optimized_path=%s WHERE id = %s', [rel, image['id']],
                )
            etags.append(admin_client.get(f"{IMAGES}/{image['id']}")['ETag'])

    assert etags[0] != etags[1]


@pytest.mark.django_db
def test_rebuild_transparent_command(admin_client, tmp_path, kb_clean):
    """
    --rebuild-transparent пересобирает варианты только у картинок с
    прозрачностью и убирает с диска прежние, испорченные.
    """
    from django.core.management import call_command

    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        clear = _upload(admin_client, tmp_path, '__test_kb_clear.png', _rgba_png_bytes()).json()
        flat = _upload(admin_client, tmp_path, '__test_kb_flat.png', _png_bytes(30, 30)).json()
        # Состояние «до починки»: варианты построены по-старому, без прозрачности.
        stale = {}
        for image_id in (clear['id'], flat['id']):
            sha, _original = _sha_and_path(image_id)
            old = {
                'optimized': images.variant_path(sha, f'w{images.OPTIMIZED_WIDTH}'),
                'thumb': images.variant_path(sha, f'w{images.THUMB_WIDTH}'),
            }
            for rel in old.values():
                target = tmp_path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'old')
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_images SET optimize_state='ready',"
                    ' optimized_path=%s, thumb_path=%s WHERE id = %s',
                    [old['optimized'], old['thumb'], image_id],
                )
            stale[image_id] = old

        call_command('knowledge_optimize_pending', '--rebuild-transparent', stdout=io.StringIO())

        with connection.cursor() as cur:
            cur.execute(
                'SELECT id, optimized_path, thumb_path FROM knowledge_images'
                ' WHERE id IN (%s, %s)', [clear['id'], flat['id']],
            )
            paths = {row[0]: row[1:] for row in cur.fetchall()}

        assert paths[clear['id']][0].endswith('.w1600a.webp')
        assert paths[clear['id']][1].endswith('.w400a.webp')
        assert not (tmp_path / stale[clear['id']]['optimized']).exists()
        assert not (tmp_path / stale[clear['id']]['thumb']).exists()
        with Image.open(tmp_path / paths[clear['id']][0]) as im:
            assert im.mode == 'RGBA'
        # Непрозрачная не тронута.
        assert paths[flat['id']] == (stale[flat['id']]['optimized'], stale[flat['id']]['thumb'])
        assert (tmp_path / stale[flat['id']]['optimized']).read_bytes() == b'old'


@pytest.mark.django_db
def test_serve_rejects_unknown_variant(admin_client, tmp_path, kb_clean):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        image = _upload(admin_client, tmp_path).json()
        resp = admin_client.get(f"{IMAGES}/{image['id']}?variant=huge")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Лимит пикселей, уборка сирот, доступ автора к ещё не сохранённой картинке
# ---------------------------------------------------------------------------

def test_store_upload_rejects_pixel_bomb(tmp_path):
    # Файл может быть крошечным по байтам и огромным по пикселям — на VPS с
    # 2 ГБ RAM его разворачивание кладёт весь сервер.
    payload = _png_bytes(4000, 4000)
    with override_settings(
        KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_MAX_IMAGE_PIXELS=1000,
    ):
        with pytest.raises(images.ImageTooLarge):
            images.store_upload(io.BytesIO(payload), 'bomb.png')


@pytest.mark.django_db
def test_admin_sees_image_before_it_is_saved_into_document(admin_client, tmp_path, kb_clean):
    """Свежезагруженная картинка ещё не привязана к документу — но видна автору.

    Иначе только что вставленный в редактор скриншот показывался бы битым до
    первого сохранения.
    """
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        image = _upload(admin_client, tmp_path).json()
        resp = admin_client.get(f"{IMAGES}/{image['id']}")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_teacher_cannot_see_unused_image(teacher_client, admin_client, tmp_path, kb_clean):
    """Послабление действует только для тех, кто и так видит все документы."""
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        image = _upload(admin_client, tmp_path).json()
        resp = teacher_client.get(f"{IMAGES}/{image['id']}")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_orphan_ignores_images_used_by_live_documents(admin_client, tmp_path, kb_clean):
    from datetime import timedelta

    from django.utils import timezone

    from apps.knowledge import repository

    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        image = _upload(admin_client, tmp_path).json()
        section = admin_client.post(
            SECTIONS, {'title': '__test_kb_раздел'}, format='json',
        ).json()
        doc = admin_client.post(
            DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
        ).json()
        admin_client.patch(
            f"{DOCS}/{doc['id']}",
            {'content': {'type': 'doc', 'content': [
                {'type': 'knowledgeImage', 'attrs': {'imageId': image['id']}},
            ]}},
            format='json',
        )
        future = timezone.now() + timedelta(days=1)
        assert image['id'] not in repository.orphan_image_ids(future)

        # Документ удаляют мягко — использование остаётся строкой в БД, но
        # картинка обязана стать кандидатом на уборку, иначе файлы копятся вечно.
        admin_client.delete(f"{DOCS}/{doc['id']}")
        assert image['id'] in repository.orphan_image_ids(future)


@pytest.mark.skipif("sys.platform == 'win32'",
                    reason='POSIX-режим файлов на Windows не выставляется: '
                           'os.chmod там управляет только атрибутом «только чтение». '
                           'Проверка осмысленна на Linux — там и работает прод.')
def test_store_upload_sets_group_readable_mode(tmp_path):
    """Файл в хранилище обязан быть читаем ГРУППОЙ, а не только владельцем.

    На проде байты отдаёт nginx под www-data по X-Accel-Redirect, тогда как
    пишет файл приложение под своим пользователем — общая у них только группа
    (её даёт setgid на каталоге хранилища). tempfile.mkstemp создаёт файл с
    режимом 0600, и shutil.move этот режим сохраняет; без явного chmod картинки
    загружаются, но не отображаются — Permission denied у nginx.

    Локально дефект невидим: при пустом KNOWLEDGE_X_ACCEL_PREFIX файл отдаёт сам
    Django, то есть тот же пользователь, что его записал. Поэтому проверяем
    режим, а не факт чтения (инцидент 22.08.2026).
    """
    payload = _png_bytes()
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        meta = images.store_upload(io.BytesIO(payload), 'x.png')

    mode = (tmp_path / meta.relative_path).stat().st_mode & 0o777
    assert mode == 0o640, f'ожидался режим 0640, получен {mode:o}'


def test_store_upload_leaves_no_temp_files(tmp_path):
    """Временный файл создаётся внутри хранилища (ради атомарного rename и
    наследования группы), поэтому обязан быть убран за собой — иначе каталог
    засоряется кусками kb-upload-*, которые уборщик осиротевших не знает."""
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        images.store_upload(io.BytesIO(_png_bytes()), 'x.png')

    assert list(tmp_path.glob('kb-upload-*')) == []
