"""
Вьюхи раздела «База знаний»: разделы и документы.

Права — KnowledgeReadStaffWriteAdmin на каждой вьюхе. DRF по умолчанию AllowAny,
поэтому пропуск permission_classes открыл бы эндпоинт всем.

Создание/переименование раздела оборачивается в transaction.atomic(): без
savepoint пойманный IntegrityError (unique-violation по title) оставляет
транзакцию БД в aborted-состоянии, и все последующие запросы в том же
тесте/запросе падают с «current transaction is aborted». atomic() ставит
savepoint и откатывает именно к нему при выходе с исключением — до того,
как мы его поймаем и превратим в 409.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.knowledge import repository, services
from apps.knowledge.content import ContentError
from apps.knowledge.permissions import (
    KnowledgePersonalMark,
    KnowledgeReadStaffWriteAdmin,
)
from apps.knowledge.serializers import (
    DocumentCreateSerializer,
    DocumentUpdateSerializer,
    ReorderSerializer,
    SectionUpdateSerializer,
    SectionWriteSerializer,
)


def _is_unique_violation(exc: Exception) -> bool:
    # Проверка по SQLSTATE (23505), а не по тексту сообщения: сообщение
    # локализовано (PostgreSQL на русской локали), 'unique' в нём не встретится.
    pgcode = getattr(exc, 'pgcode', None)
    if pgcode == '23505':
        return True
    cause = getattr(exc, '__cause__', None)
    if cause and getattr(cause, 'pgcode', None) == '23505':
        return True
    return False


class SectionListCreateView(APIView):
    """GET — список разделов, POST — создать раздел."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def get(self, request: Request) -> Response:
        include_inactive = request.query_params.get('include_inactive') == '1'
        sections = repository.list_sections(
            include_inactive=include_inactive, role=request.user.role,
        )
        # total — счётчик псевдопапки «Все документы». Считаем здесь, а не
        # суммой по разделам: документ из погашенного раздела в сумму не
        # попадёт, а видимость документов раздел учитывает (visible_documents_qs).
        return Response({
            'sections': sections,
            'total': repository.count_visible_documents(request.user.role),
        })

    def post(self, request: Request) -> Response:
        serializer = SectionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                section = repository.create_section(serializer.validated_data['title'])
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                return Response({'error': 'Already exists'}, status=status.HTTP_409_CONFLICT)
            raise
        return Response(section, status=status.HTTP_201_CREATED)


class SectionDetailView(APIView):
    """GET/PATCH/DELETE одного раздела."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def get(self, request: Request, pk: int) -> Response:
        # Видимость решает репозиторий: недоступный роли раздел неотличим от
        # несуществующего (404), иначе ответ подтверждал бы, что такая папка
        # в школе есть.
        section = repository.get_visible_section(request.user.role, pk)
        if section is None:
            raise NotFound()
        return Response(section)

    def patch(self, request: Request, pk: int) -> Response:
        serializer = SectionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Погасить раздел через active=false — то же самое, что удалить его, и
        # проверка нужна та же. Иначе документы остались бы доступны по прямой
        # ссылке, а папки, в которой они лежат, уже не было бы ни в одном списке.
        if serializer.validated_data.get('active') is False:
            if repository.count_active_documents(pk) > 0:
                return Response(
                    {'error': 'has_documents',
                     'message': 'В разделе есть документы — сначала удалите их.'},
                    status=status.HTTP_409_CONFLICT,
                )
        try:
            with transaction.atomic():
                section = repository.update_section(pk, serializer.validated_data)
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                return Response({'error': 'Already exists'}, status=status.HTTP_409_CONFLICT)
            raise
        if section is None:
            raise NotFound()
        return Response(section)

    def delete(self, request: Request, pk: int) -> Response:
        if repository.count_active_documents(pk) > 0:
            return Response(
                {'error': 'has_documents',
                 'message': 'В разделе есть документы — сначала удалите их.'},
                status=status.HTTP_409_CONFLICT,
            )
        if not repository.soft_delete_section(pk):
            raise NotFound()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SectionReorderView(APIView):
    """POST — задать порядок разделов списком id."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def post(self, request: Request) -> Response:
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        repository.reorder_sections(serializer.validated_data['order'])
        return Response({'ok': True})


class DocumentListCreateView(APIView):
    """GET — список документов (без content), POST — создать черновик."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]
    pagination_class = StandardPagination

    def get(self, request: Request) -> Response:
        raw_section = request.query_params.get('section_id')
        section_id = None
        if raw_section:
            # Параметр приходит из адресной строки: нечисловое значение — это
            # кривой запрос, а не повод для 500.
            try:
                section_id = int(raw_section)
            except ValueError:
                return Response(
                    {'error': 'section_id должен быть числом.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        scope = request.query_params.get('scope', 'all')
        if scope not in ('all', 'favorites', 'archive'):
            return Response(
                {'error': f'Неизвестный scope: {scope!r}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        doc_status = request.query_params.get('status', '')
        if doc_status and doc_status not in ('draft', 'published'):
            return Response(
                {'error': f'Неизвестный статус: {doc_status!r}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = repository.list_documents(
            request.user.role,
            section_id=section_id,
            account_id=request.user.id,
            query=request.query_params.get('q', '').strip(),
            status=doc_status,
            scope=scope,
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(list(page))

    def post(self, request: Request) -> Response:
        serializer = DocumentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            document = services.create_document(
                section_id=serializer.validated_data['section_id'],
                title=serializer.validated_data['title'],
                account_id=request.user.id,
            )
        except services.SectionNotFound:
            raise NotFound('Раздел не найден.')
        return Response(document, status=status.HTTP_201_CREATED)


class DocumentDetailView(APIView):
    """GET/PATCH/DELETE одного документа."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def get(self, request: Request, pk: int) -> Response:
        document = services.get_document(request.user.role, pk)
        if document is None:
            # Именно 404: 403 сообщил бы, что документ с таким id существует.
            raise NotFound()
        document['is_favorite'] = repository.is_favorite(pk, request.user.id)
        return Response(document)

    def patch(self, request: Request, pk: int) -> Response:
        serializer = DocumentUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            document = services.update_document(
                pk, serializer.validated_data, account_id=request.user.id,
            )
        except services.DocumentNotFound:
            raise NotFound()
        except services.SectionNotFound:
            raise NotFound('Раздел не найден.')
        except services.StaleDocument as exc:
            return Response(
                {'error': 'Документ изменили в другом месте.',
                 'code': 'stale', 'updated_at': exc.updated_at},
                status=status.HTTP_409_CONFLICT,
            )
        except ContentError as exc:
            return Response(
                {'error': str(exc), 'code': 'invalid_content'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(document)

    def delete(self, request: Request, pk: int) -> Response:
        if not repository.soft_delete_document(pk):
            raise NotFound()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DocumentPublishView(APIView):
    """POST — опубликовать документ."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]
    published = True

    def post(self, request: Request, pk: int) -> Response:
        try:
            document = services.set_published(
                pk, self.published, account_id=request.user.id,
            )
        except services.DocumentNotFound:
            raise NotFound()
        return Response(document)


class DocumentUnpublishView(DocumentPublishView):
    """POST — снять документ с публикации."""

    published = False


class DocumentRestoreView(APIView):
    """POST — вернуть документ из архива."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def post(self, request: Request, pk: int) -> Response:
        if not repository.restore_document(pk):
            raise NotFound()
        document = services.get_document(request.user.role, pk)
        if document is None:
            raise NotFound()
        return Response(document)


class DocumentDuplicateView(APIView):
    """POST — создать копию документа черновиком."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def post(self, request: Request, pk: int) -> Response:
        try:
            document = services.duplicate_document(
                pk, role=request.user.role, account_id=request.user.id,
            )
        except services.DocumentNotFound:
            raise NotFound()
        return Response(document, status=status.HTTP_201_CREATED)


class DocumentFavoriteView(APIView):
    """
    POST — в избранное, DELETE — из избранного.

    Избранное личное, поэтому доступно всем, кто видит документ, а не только
    администраторам: это не правка документа, а закладка сотрудника.
    """

    permission_classes = [KnowledgePersonalMark]

    def post(self, request: Request, pk: int) -> Response:
        return self._set(request, pk, True)

    def delete(self, request: Request, pk: int) -> Response:
        return self._set(request, pk, False)

    def _set(self, request: Request, pk: int, value: bool) -> Response:
        if services.get_document(request.user.role, pk) is None:
            raise NotFound()
        repository.set_favorite(pk, request.user.id, value)
        return Response({'is_favorite': value})


class DocumentReorderView(APIView):
    """POST — задать порядок документов внутри раздела."""

    permission_classes = [KnowledgeReadStaffWriteAdmin]

    def post(self, request: Request) -> Response:
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            section_id = int(request.data.get('section_id'))
        except (TypeError, ValueError):
            return Response(
                {'error': 'section_id обязателен и должен быть числом.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        repository.reorder_documents(section_id, serializer.validated_data['order'])
        return Response({'ok': True})
