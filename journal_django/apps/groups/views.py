"""
GroupsView — тонкий ViewSet для /api/admin/groups.

Зеркалит Express routes/admin/groups.js и Nest GroupsController:
  GET    /api/admin/groups        → list()    → 200 {rows, total, page, page_size}
  GET    /api/admin/groups/:id    → retrieve() → 200 | 404 {error: 'Not found'}
  POST   /api/admin/groups        → create()   → 201 | 409
  PATCH  /api/admin/groups/:id    → partial_update() → 200 | 404
  DELETE /api/admin/groups/:id    → destroy()  → 204 | 404

Права: только manager или admin (IsManagerOrAdmin).
Пагинация и сортировка: параметры sort_by / sort_dir / page / page_size / filter[*].
"""
from __future__ import annotations

from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsManagerOrAdmin
from apps.groups import services
from apps.lessons import services as lessons_services
from apps.groups.exceptions import ImmutableGroupFormat
from apps.groups.serializers import (
    GroupReadSerializer, GroupUpdateSerializer, GroupWriteSerializer,
    ScheduleChangeSerializer,
)
from apps.scheduling.exceptions import PlanHasRecordedLessons


# Допустимые значения sort_by (whitelist)
ORDERING_FIELDS = [
    'id', 'name', 'direction_id', 'teacher_id',
    'lesson_duration_minutes', 'lessons_per_week', 'group_start_date', 'active',
    # Аннотация списка, не колонка таблицы: пройдено уроков курса группой
    # (repository._lessons_done_subquery).
    'lessons_done',
]


def _parse_list_params(request: Request) -> dict:
    """
    Извлечь и нормализовать параметры пагинации из query string.

    Поддерживаемые параметры:
      page, page_size, sort_by, sort_dir, filter[name], filter[active], ...

    Зеркалит parsePaginationRequest() из services/pagination.js.
    Бросает ValidationError при невалидном sort_by или sort_dir.
    """
    qp = request.query_params

    page = max(1, int(qp.get('page', 1) or 1))
    page_size = min(500, max(1, int(qp.get('page_size', 50) or 50)))

    sort_by = qp.get('sort_by', 'name') or 'name'
    sort_dir = qp.get('sort_dir', 'asc') or 'asc'

    if sort_by not in ORDERING_FIELDS:
        raise ValidationError(
            f"Invalid sort_by '{sort_by}'. Allowed: {ORDERING_FIELDS}"
        )
    if sort_dir not in ('asc', 'desc'):
        raise ValidationError(
            f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'."
        )

    # Express передаёт фильтры как filter[key]=value
    # DRF QueryDict делает их доступными через filter[name] ключи напрямую
    filters: dict = {}
    for key, value in qp.items():
        if key.startswith('filter[') and key.endswith(']'):
            filter_key = key[7:-1]
            filters[filter_key] = value

    # По умолчанию список — только активные группы (архив скрыт, как у
    # teachers/directions). include_inactive=1 возвращает и архивные (напр.
    # useGroupsAll(true) для маппинга имён групп на detail-страницах); явный
    # filter[active] имеет приоритет над обоими (см. repository._apply_filters).
    include_inactive = qp.get('include_inactive') == '1'

    return {
        'page': page,
        'page_size': page_size,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'filters': filters,
        'include_inactive': include_inactive,
    }


class GroupListCreateView(APIView):
    """
    GET  /api/admin/groups  — список групп с пагинацией
    POST /api/admin/groups  — создать группу
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        params = _parse_list_params(request)
        result = services.list_groups(**params)
        return Response(result)

    def post(self, request: Request) -> Response:
        serializer = GroupWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            group = services.create_group(serializer.validated_data)
        except (IntegrityError, ValidationError) as exc:
            if _is_unique_violation(exc):
                return Response(
                    {'error': 'Already exists'},
                    status=status.HTTP_409_CONFLICT,
                )
            raise

        return Response(group, status=status.HTTP_201_CREATED)


class GroupDetailView(APIView):
    """
    GET    /api/admin/groups/:id  — получить группу
    PATCH  /api/admin/groups/:id  — обновить группу
    DELETE /api/admin/groups/:id  — мягкое удаление
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        group = services.get_group(pk)
        if group is None:
            raise NotFound({'error': 'Not found'})
        return Response(group)

    def patch(self, request: Request, pk: int) -> Response:
        serializer = GroupUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = dict(serializer.validated_data)
        # Архивация/разархивация группы (смена active) — только суперадмин.
        # Обычную правку менеджерам/админам оставляем, но поле active игнорируем,
        # чтобы они не могли (раз)архивировать группу через PATCH.
        if getattr(request.user, 'role', None) != 'superadmin':
            data.pop('active', None)

        try:
            updated = services.update_group(pk, data)
        except ImmutableGroupFormat as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except PlanHasRecordedLessons as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if updated is None:
            raise NotFound({'error': 'Not found'})

        return Response(updated)

    def delete(self, request: Request, pk: int) -> Response:
        # Архивация группы (soft-delete) — только суперадмин.
        if getattr(request.user, 'role', None) != 'superadmin':
            raise PermissionDenied('Архивировать группу может только суперадмин.')
        ok = services.soft_delete_group(pk)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupProgressView(APIView):
    """
    GET /api/admin/groups/:id/progress — обзорная матрица посещаемости
    (слоты уроков × ученики) для вкладки «Прогресс». Read-only.
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        data = services.get_group_progress(pk)
        if data is None:
            raise NotFound({'error': 'Not found'})
        return Response(data)


class GroupLessonSkipView(APIView):
    """
    GET   /api/admin/groups/:id/lesson-skips?lesson_number=N — student_id с пометкой
          «неоплачиваемый пропуск» на слот N (в т.ч. ещё не проведённый).
    PATCH /api/admin/groups/:id/lesson-skips — поставить/снять пометку на слот.
          Body: {student_id, lesson_number, value}. Работает и на будущем слоте
          (урока-записи/даты не требуется). См. Вариант A lesson-outcomes-spec.
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        raw = request.query_params.get('lesson_number')
        if raw is None:
            raise ValidationError({'error': 'lesson_number required'})
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise ValidationError({'error': 'lesson_number must be a number'})
        return Response({'student_ids': lessons_services.list_lesson_skips(pk, num)})

    def patch(self, request: Request, pk: int) -> Response:
        data = request.data
        try:
            student_id = int(data['student_id'])
            lesson_number = float(data['lesson_number'])
            value = bool(data['value'])
        except (KeyError, TypeError, ValueError):
            raise ValidationError({'error': 'student_id, lesson_number, value required'})
        # В журнал ИБ не пишем — доменное действие покрыто «Журналом изменений»
        # (правило group.lesson_skip в apps/changelog/labels.py).
        lessons_services.set_lesson_skip(pk, student_id, lesson_number, value)
        return Response({'ok': True})


class GroupScheduleView(APIView):
    """
    GET  /api/admin/groups/:id/schedule         — слоты (с датами действия) + исключения
    POST /api/admin/groups/:id/schedule-change  — постоянная смена расписания
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        sched = services.get_schedule(pk)
        if sched is None:
            raise NotFound({'error': 'Not found'})
        return Response(sched)


class GroupScheduleChangeView(APIView):
    """POST /api/admin/groups/:id/schedule-change — постоянная смена времени."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        serializer = ScheduleChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.apply_schedule_change(pk, serializer.validated_data)
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                return Response({'error': 'Slot already exists'}, status=status.HTTP_409_CONFLICT)
            raise
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_unique_violation(exc: Exception) -> bool:
    """Проверить, является ли ошибка нарушением уникальности (pgcode 23505)."""
    pgcode = getattr(exc, 'pgcode', None)
    if pgcode == '23505':
        return True
    cause = getattr(exc, '__cause__', None)
    if cause and getattr(cause, 'pgcode', None) == '23505':
        return True
    return False
