"""
StudentsView — тонкие APIView для /api/admin/students.

Зеркалит Express routes/admin/students.js:
  GET    /api/admin/students           → список + пагинация → 200
  GET    /api/admin/students/:id       → один ученик → 200 | 404
  GET    /api/admin/students/:id/stats → посещаемость → 200 | 404
  GET    /api/admin/students/:id/balance → баланс → 200
  GET    /api/admin/students/:id/lessons → уроки ученика → 200 | 404
  POST   /api/admin/students           → создать → 201
  PATCH  /api/admin/students/:id       → обновить → 200 | 404

Права: только manager или admin (IsManagerOrAdmin).
"""
from __future__ import annotations

from datetime import date

from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.permissions import IsAdminOrSuperAdmin, IsManagerOrAdmin, ReadStaffWriteAdmin
from apps.payments import services as payment_services
from apps.students import lesson_history, services
from apps.students.models import StudentComment
from apps.students.serializers import (
    StudentCommentSerializer,
    StudentCommentWriteSerializer,
    StudentManagerSerializer,
    StudentUpdateSerializer,
    StudentWriteSerializer,
)

# Допустимые значения sort_by (whitelist)
ORDERING_FIELDS = [
    'id', 'full_name', 'birth_date', 'stage', 'created_at',
]


def _parse_list_params(request: Request) -> dict:
    """
    Извлечь и нормализовать параметры пагинации из query string.

    Поддерживаемые параметры:
      page, page_size, sort_by, sort_dir, filter[name], filter[stage_id], ...

    Зеркалит parsePaginationRequest() из services/pagination.js.
    Бросает ValidationError при невалидном sort_by или sort_dir.
    """
    qp = request.query_params

    page = max(1, int(qp.get('page', 1) or 1))
    page_size = min(500, max(1, int(qp.get('page_size', 50) or 50)))

    sort_by = qp.get('sort_by', 'full_name') or 'full_name'
    sort_dir = qp.get('sort_dir', 'asc') or 'asc'

    if sort_by not in ORDERING_FIELDS:
        raise ValidationError(
            f"Invalid sort_by '{sort_by}'. Allowed: {ORDERING_FIELDS}"
        )
    if sort_dir not in ('asc', 'desc'):
        raise ValidationError(
            f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'."
        )

    filters: dict = {}
    for key, value in qp.items():
        if key.startswith('filter[') and key.endswith(']'):
            filter_key = key[7:-1]
            filters[filter_key] = value

    return {
        'page': page,
        'page_size': page_size,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'filters': filters,
    }


class StudentListCreateView(APIView):
    """
    GET  /api/admin/students  — список учеников с пагинацией
    POST /api/admin/students  — создать ученика
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        params = _parse_list_params(request)
        result = services.list_students(**params)
        return Response(result)

    def post(self, request: Request) -> Response:
        serializer = StudentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        student = services.create_student(serializer.validated_data)
        return Response(student, status=status.HTTP_201_CREATED)


class StudentDetailView(APIView):
    """
    GET    /api/admin/students/:id  — получить ученика
    PATCH  /api/admin/students/:id  — обновить ученика

    DELETE нет: ученика не удаляют и не «деактивируют». Уход оформляется в воронке
    продлений — сделка переводится в стадию «Ушёл»; членства и расписание менеджер
    правит отдельно (спека 2026-07-25, статусы ученика удалены).
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        student = services.get_student(pk)
        if student is None:
            raise NotFound({'error': 'Not found'})
        return Response(student)

    def patch(self, request: Request, pk: int) -> Response:
        serializer = StudentUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated = services.update_student(pk, serializer.validated_data)
        if updated is None:
            raise NotFound({'error': 'Not found'})

        return Response(updated)


class StudentStatsView(APIView):
    """
    GET /api/admin/students/:id/stats — посещаемость ученика.

    404 если ученик не найден (в отличие от balance — там проверки нет).
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        # Только 404-гейт: сами поля ученика здесь не нужны, поэтому exists(),
        # а не get_student (тот тянет ещё и стадию — 4 подзапроса + справочник).
        if not services.student_exists(pk):
            raise NotFound({'error': 'Not found'})

        stats = services.student_stats(pk)
        return Response(stats)


class StudentBalanceView(APIView):
    """
    GET /api/admin/students/:id/balance — баланс ученика по направлениям.

    Express не проверяет существование ученика — просто возвращает данные.
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        balance = services.get_student_balance(pk)
        return Response(balance)


def _parse_optional_period(qp) -> tuple[str | None, str | None]:
    """
    Необязательный период вкладки «Уроки»: обе границы включительно.

    Пусто — период не задан (вся история). Невалидная дата — 400, а не тихий
    прогон в SQL: неразобранная строка там превращается в 500.
    """
    raw_from = qp.get('date_from') or ''
    raw_to = qp.get('date_to') or ''
    parsed: list[str | None] = []
    for raw in (raw_from, raw_to):
        if not raw:
            parsed.append(None)
            continue
        try:
            parsed.append(date.fromisoformat(raw).isoformat())
        except ValueError:
            raise ValidationError({'error': 'date_from/date_to must be YYYY-MM-DD'})
    if parsed[0] and parsed[1] and parsed[1] < parsed[0]:
        raise ValidationError({'error': 'date_to must not be earlier than date_from'})
    return parsed[0], parsed[1]


class StudentLessonsView(APIView):
    """
    GET /api/admin/students/:id/lessons — уроки ученика (вкладка «Уроки»).

    Строки — записи посещаемости с present=true: обычный / бесплатный /
    доп.урок / сгоревший. Пропуски не показываются (спека 2026-09-14).
    Порядок — по убыванию даты сохранения урока.

    «Признано» считается точным FIFO по ВСЕЙ истории ученика независимо от
    запрошенной страницы: иначе неизвестно, какие абонементы к моменту урока
    уже погашены.

    Сортировка: sort_by ∈ lesson_history.ORDERING_FIELDS (дата занятия или дата
    сохранения), sort_dir ∈ asc|desc. Мусор в параметрах → 400, как в списке
    учеников: молча откатывать на умолчание значит показать не тот порядок,
    который просили, и не сказать об этом.

    404 если ученик не найден — единообразно со StudentStatsView.
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        if not services.student_exists(pk):
            raise NotFound({'error': 'Not found'})

        qp = request.query_params
        sort_by = qp.get('sort_by') or lesson_history.DEFAULT_SORT_BY
        sort_dir = qp.get('sort_dir') or lesson_history.DEFAULT_SORT_DIR
        allowed = sorted(lesson_history.ORDERING_FIELDS)
        if sort_by not in lesson_history.ORDERING_FIELDS:
            raise ValidationError(f"Invalid sort_by '{sort_by}'. Allowed: {allowed}")
        if sort_dir not in ('asc', 'desc'):
            raise ValidationError(
                f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'."
            )
        date_from, date_to = _parse_optional_period(qp)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(
            lesson_history.lesson_rows_queryset(pk, sort_by, sort_dir, date_from, date_to),
            request, view=self,
        )
        return paginator.get_paginated_response(lesson_history.serialize_rows(page, pk))


class StudentCommentListView(generics.ListAPIView):
    """
    GET  /api/admin/students/:id/comments — список комментариев, пагинация
    POST /api/admin/students/:id/comments — добавить комментарий → 201

    404 если ученик не найден (единообразно с StudentStatsView).
    """

    permission_classes = [IsManagerOrAdmin]
    pagination_class = StandardPagination
    serializer_class = StudentCommentSerializer

    def get_queryset(self):
        return (
            StudentComment.objects
            .filter(student_id=self.kwargs['pk'])
            .select_related('author')
            .order_by('-created_at')
        )

    def get(self, request: Request, pk: int) -> Response:
        if not services.student_exists(pk):
            raise NotFound({'error': 'Not found'})
        return super().get(request, pk)

    def post(self, request: Request, pk: int) -> Response:
        if not services.student_exists(pk):
            raise NotFound({'error': 'Not found'})
        ser = StudentCommentWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        comment = services.add_comment(
            pk, ser.validated_data['body'], getattr(request.user, 'id', None))
        return Response(StudentCommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class StudentCommentDetailView(APIView):
    """DELETE /api/admin/students/:id/comments/:comment_id — только admin/superadmin."""

    permission_classes = [ReadStaffWriteAdmin]

    def delete(self, request: Request, pk: int, comment_id: int) -> Response:
        ok = services.delete_comment(pk, comment_id)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)


class StudentRefundView(APIView):
    """POST /api/admin/students/{id}/refund — возврат неотработанного остатка (admin/superadmin).

    Ответ 201: {refunds: [строка на каждое направление остатка], new_balance: 0,
    refunded_amount}. Строк несколько, потому что лимит курса считается
    per-direction: возврат без направления его не освобождал бы (см.
    apps/payments/repository.py::refund_student).
    """

    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        user = request.user
        author = (getattr(user, 'full_name', None) or getattr(user, 'email', None)) if user else None
        result = payment_services.refund_student(pk, created_by=author)
        if result.get('error') == 'student_not_found':
            raise NotFound({'error': 'Not found'})
        if result.get('error') == 'nothing_to_refund':
            return Response({'error': 'nothing_to_refund'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


class StudentManagerView(APIView):
    """PATCH /api/admin/students/:id/manager — сменить ответственного менеджера.

    В отличие от общего PATCH /students/:id (IsManagerOrAdmin, редактирует
    любой manager/admin/superadmin), это поле доступно только admin/superadmin:
    смена ответственного синхронно переписывает assignee активной (открытой)
    сделки продления ученика — закрытые сделки не трогаются
    (services.set_student_manager)."""

    permission_classes = [IsAdminOrSuperAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        ser = StudentManagerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            updated = services.set_student_manager(
                pk, ser.validated_data['manager_id'], actor=request.user)
        except ValueError as exc:
            raise ValidationError({'error': str(exc)})
        if updated is None:
            raise NotFound({'error': 'Not found'})
        return Response(updated)
