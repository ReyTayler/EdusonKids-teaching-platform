"""
Загрузка и скачивание файлов, прикреплённых к документам базы знаний.

Отдача идёт тем же путём, что у картинок: X-Accel-Redirect, если nginx настроен,
иначе FileResponse. Права проверяются одинаково в обоих режимах.

Ключевое отличие от картинок — файл ВСЕГДА скачивается и никогда не
показывается в браузере. Причина не в удобстве: файл, отданный с нашего домена,
наследует доверие к домену. PDF с активным содержимым или разметка, открытые по
адресу нашего сайта, исполнялись бы в его контексте — видели бы куки и обходили
политику безопасности. Заголовки Content-Disposition: attachment и
X-Content-Type-Options: nosniff это снимают.
"""
from __future__ import annotations

from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.knowledge import file_types, repository, storage
from apps.knowledge.models import KnowledgeFile
from apps.knowledge.permissions import KnowledgeReadStaffWriteAdmin

# Каталог файлов внутри хранилища. Отдельный от картинок, чтобы уборка и
# резервное копирование могли обращаться с ними по-разному.
PREFIX = 'knowledge-files'


def _serialize(file: KnowledgeFile) -> dict:
    return {
        'id': file.id,
        'name': file.original_name,
        'mime': file.mime,
        'byte_size': file.byte_size,
    }


class FileUploadView(APIView):
    """POST — прикрепить файл. Только admin/superadmin (мутация)."""

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
            # Имя чистим ДО приёма: от него зависит проверка содержимого, и
            # незачем писать на диск то, что отвергнет проверка имени.
            name = file_types.check_name(upload.name)
            stored = storage.store_upload(
                upload,
                prefix=PREFIX,
                max_bytes=settings.KNOWLEDGE_MAX_FILE_BYTES,
                probe=file_types.probe_by_name(name),
                original_name=name,
            )
        except storage.UploadTooLarge as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        except storage.UploadRejected as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        existing = repository.get_file_by_sha256(stored.sha256)
        if existing is not None:
            # Тот же файл уже загружен — второй копии на диске не появилось.
            # Запись тоже одна: имя берётся то, под которым файл загрузили
            # впервые. Подпись на карточке при этом своя у каждой вставки —
            # она хранится в узле документа.
            return Response(_serialize(existing), status=status.HTTP_201_CREATED)

        file = KnowledgeFile.objects.create(
            sha256=stored.sha256,
            original_name=name,
            mime=stored.mime,
            byte_size=stored.byte_size,
            path=stored.relative_path,
            uploaded_by_id=request.user.id,
        )
        return Response(_serialize(file), status=status.HTTP_201_CREATED)


class FileDownloadView(APIView):
    """GET — отдать файл на скачивание с проверкой прав."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def get(self, request: Request, pk: int):
        file = repository.get_file(pk)
        if file is None:
            raise NotFound()
        if not repository.file_visible_to(request.user.role, pk):
            # 404, а не 403 — существование файла тоже не разглашаем.
            raise NotFound()

        return _download_response(file)


def _download_response(file: KnowledgeFile):
    prefix = settings.KNOWLEDGE_X_ACCEL_PREFIX
    if prefix:
        response = HttpResponse(content_type=file.mime)
        response['X-Accel-Redirect'] = f'{prefix.rstrip("/")}/{file.path}'
        # Content-Length ставит nginx; пустое тело здесь — это норма.
        del response['Content-Length']
    else:
        path = storage.absolute_path(file.path)
        if not path.exists():
            raise NotFound()
        response = FileResponse(path.open('rb'), content_type=file.mime)

    response['Content-Disposition'] = _disposition(file.original_name)
    # Без этого браузер угадывает тип по содержимому и может решить, что
    # «текстовый» файл на самом деле разметка.
    response['X-Content-Type-Options'] = 'nosniff'
    # Содержимое по конкретному пути неизменно (имя файла = хеш содержимого).
    # private, а не public: файл не должен осесть в общем прокси в обход
    # проверки прав.
    response['ETag'] = f'"{file.sha256}"'
    response['Cache-Control'] = 'private, max-age=86400'
    return response


def _disposition(name: str) -> str:
    """
    Заголовок скачивания с именем файла.

    Два представления имени по RFC 6266: `filename` из безопасных символов для
    старых клиентов и `filename*` в кодировке UTF-8 для всех остальных.
    Кириллическое имя, подставленное в заголовок напрямую, либо разваливается,
    либо (если в нём окажется перевод строки) позволяет дописать свои заголовки
    в ответ.
    """
    ascii_name = ''.join(
        ch if ch.isascii() and ch.isprintable() and ch not in '"\\' else '_'
        for ch in name
    ) or 'file'
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )
