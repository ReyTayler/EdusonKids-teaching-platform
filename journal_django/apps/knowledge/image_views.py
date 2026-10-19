"""
Загрузка и отдача картинок базы знаний.

Отдача идёт через X-Accel-Redirect: Django проверяет права и отвечает пустым
телом с заголовком, дальше файл отдаёт nginx через sendfile. Воркер
освобождается сразу, а не держится на всё время передачи.

Если KNOWLEDGE_X_ACCEL_PREFIX пуст (локальный runserver без nginx), файл
отдаётся через FileResponse. Права проверяются одинаково в обоих режимах.
"""
from __future__ import annotations

from pathlib import PurePosixPath

from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.knowledge import images, repository
from apps.knowledge.models import KnowledgeImage
from apps.knowledge.permissions import KnowledgeReadStaffWriteAdmin

VARIANTS = ('optimized', 'thumb', 'original')


def _serialize(image: KnowledgeImage) -> dict:
    return {
        'id': image.id,
        'mime': image.mime,
        'byte_size': image.byte_size,
        'width': image.width,
        'height': image.height,
        'optimize_state': image.optimize_state,
    }


class ImageUploadView(APIView):
    """POST — загрузить картинку. Только admin/superadmin (мутация)."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'knowledge_upload'

    def post(self, request: Request) -> Response:
        upload = request.FILES.get('file')
        if upload is None:
            return Response(
                {'error': 'Файл не передан (поле file).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            stored = images.store_upload(upload, upload.name)
        except images.ImageTooLarge as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        except images.ImageRejected as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        existing = repository.get_image_by_sha256(stored.sha256)
        if existing is not None:
            # Тот же файл уже загружен — второй записи и второй копии не будет.
            return Response(_serialize(existing), status=status.HTTP_201_CREATED)

        image = KnowledgeImage.objects.create(
            sha256=stored.sha256,
            original_name=upload.name[:255],
            mime=stored.mime,
            byte_size=stored.byte_size,
            width=stored.width,
            height=stored.height,
            original_path=stored.relative_path,
            uploaded_by_id=request.user.id,
        )
        _enqueue_optimize(image.id)
        image.refresh_from_db()
        return Response(_serialize(image), status=status.HTTP_201_CREATED)


def _enqueue_optimize(image_id: int) -> None:
    """
    Поставить задачу оптимизации. Недоступность Celery/Redis не должна ронять
    загрузку: картинка останется в pending и будет отдаваться в оригинале, пока
    её не догонит knowledge_optimize_pending.
    """
    try:
        from apps.knowledge.tasks import optimize_image
        optimize_image.delay(image_id)
    except Exception:                                  # noqa: BLE001
        pass


class ImageServeView(APIView):
    """GET — отдать файл картинки с проверкой прав."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def get(self, request: Request, pk: int):
        variant = request.query_params.get('variant', 'optimized')
        if variant not in VARIANTS:
            return Response(
                {'error': f'Неизвестный variant: {variant!r}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image = repository.get_image(pk)
        if image is None:
            raise NotFound()
        if not repository.image_visible_to(request.user.role, pk):
            # 404, а не 403 — существование файла тоже не разглашаем.
            raise NotFound()

        rel_path, mime, served = _resolve_variant(image, variant)
        return _file_response(rel_path, mime, served == variant)


def _resolve_variant(image: KnowledgeImage, variant: str) -> tuple[str, str, str]:
    """
    Путь, mime и фактически отданный вариант; фолбэк на оригинал, если
    запрошенный ещё не готов. Вызывающему важно знать, что именно ушло:
    от этого зависят ETag и срок кэша.
    """
    if variant == 'optimized' and image.optimized_path:
        return image.optimized_path, 'image/webp', 'optimized'
    if variant == 'thumb' and image.thumb_path:
        return image.thumb_path, 'image/webp', 'thumb'
    return image.original_path, image.mime, 'original'


def _file_response(rel_path: str, mime: str, exact: bool):
    prefix = settings.KNOWLEDGE_X_ACCEL_PREFIX
    if prefix:
        response = HttpResponse(content_type=mime)
        response['X-Accel-Redirect'] = f'{prefix.rstrip("/")}/{rel_path}'
        # Content-Length ставит nginx; пустое тело здесь — это норма.
        del response['Content-Length']
    else:
        path = images.absolute_path(rel_path)
        if not path.exists():
            raise NotFound()
        response = FileResponse(path.open('rb'), content_type=mime)

    # Содержимое по конкретному пути неизменно (имя файла = хеш содержимого),
    # поэтому кэш безопасен. private, а не public: файл не должен осесть в общем
    # прокси в обход проверки прав.
    #
    # ETag — имя отданного файла, а не только хеш оригинала: байты по адресу
    # ?variant=optimized меняются, когда Celery дожал WebP (до этого отдаётся
    # оригинал) и когда вариант пересобран под новым именем (прозрачность, см.
    # images.build_variants). С неизменным ETag браузер на проверке получал
    # бы «не изменилось» и навсегда оставался со старыми байтами. Имя файла
    # построено от хеша содержимого и суффикса варианта — оно меняется ровно
    # тогда, когда меняются байты.
    response['ETag'] = f'"{PurePosixPath(rel_path).name}"'
    # Пока отдан фолбэк, долгий кэш вреден по той же причине: готовый WebP
    # появится через секунды, а картинка висела бы в кэше сутки.
    response['Cache-Control'] = (
        'private, max-age=86400' if exact else 'private, max-age=60'
    )
    return response
