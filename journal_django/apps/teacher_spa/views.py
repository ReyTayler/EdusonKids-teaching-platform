"""
Тонкие APIView для teacher SPA (role=teacher).

Эндпоинты (все под /api, НЕ /api/admin):
  POST /api/getData          → данные учителя
  POST /api/getAllData        → свои группы + чужие с назначенным занятием (для замен)
  POST /api/submitLesson     → атомарная запись урока
  POST /api/refreshData      → {success:true}

Права: только role='teacher' (IsTeacher).

Вся бизнес-логика — в services.py.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import F, Prefetch

from apps.core.pagination import StandardPagination
from apps.core.permissions import IsTeacher
from apps.groups.course_length import effective_total_lessons_expr
from apps.groups.models import Group
from apps.lessons.exceptions import CoursePositionVanished, LessonAlreadyRecorded
from apps.lessons.models import Lesson, LessonAttendance
from apps.teacher_spa import services
from apps.teacher_spa.serializers import MyLessonSerializer, SubmitLessonSerializer

# Машиночитаемый код конфликта для фронта (тот же приём, что
# MEMBERSHIP_HAS_SCHEDULED_MAKEUPS в admin SPA): урок за это занятие уже записан.
LESSON_ALREADY_RECORDED = 'lesson_already_recorded'

# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class GetDataView(APIView):
    """POST /api/getData — данные учителя."""

    permission_classes = [IsTeacher]

    def post(self, request: Request) -> Response:
        result = services.get_data(request.user.id)
        if '_error' in result:
            return Response(
                {'error': result['_error']},
                status=result['_status'],
            )
        return Response(result)


class GetAllDataView(APIView):
    """POST /api/getAllData — свои группы + чужие с назначенным занятием (для замен)."""

    permission_classes = [IsTeacher]

    def post(self, request: Request) -> Response:
        result = services.get_all_data(request.user.id)
        if '_error' in result:
            return Response(
                {'error': result['_error']},
                status=result['_status'],
            )
        return Response(result)


class SubmitLessonView(APIView):
    """POST /api/submitLesson — атомарная запись урока."""

    permission_classes = [IsTeacher]

    def post(self, request: Request) -> Response:
        serializer = SubmitLessonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = services.submit_lesson(request.user.id, serializer.validated_data)
        except LessonAlreadyRecorded as e:
            # Урок за это занятие уже записан — почти всегда повторная отправка
            # после потерянного ответа (учитель не увидел подтверждения и нажал
            # «Сохранить» ещё раз). 409, а не 400: конфликт состояния, а не
            # ошибка ввода. code — чтобы фронт мог показать это спокойным
            # сообщением «уже записано», а не красной ошибкой валидации.
            return Response(
                {'error': str(e), 'code': LESSON_ALREADY_RECORDED},
                status=status.HTTP_409_CONFLICT,
            )
        except CoursePositionVanished as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if '_error' in result:
            return Response(
                {'error': result['_error']},
                status=result['_status'],
            )
        return Response(result)


class RefreshDataView(APIView):
    """POST /api/refreshData — legacy no-op, возвращает {success:true}."""

    permission_classes = [IsTeacher]

    def post(self, request: Request) -> Response:
        return Response({'success': True})


class MyLessonsView(ListAPIView):
    """
    GET /api/lessons — история проведённых уроков ТЕКУЩЕГО преподавателя.

    Скоуп по teacher_id из JWT (request.user.teacher_id), НИКОГДА из запроса —
    иначе RBAC-дыра. Пагинация StandardPagination ({rows,total,page,page_size}).
    Порядок: свежие сверху (-lesson_date, -id). Опциональные фильтры:
      ?from=YYYY-MM-DD  ?to=YYYY-MM-DD  ?group=<точное имя>
    Каждый урок несёт students — ученики с статусом посещения (prefetch, без запроса на урок).
    """

    permission_classes = [IsTeacher]
    pagination_class = StandardPagination
    serializer_class = MyLessonSerializer

    def get_queryset(self):
        qs = (
            Lesson.objects
            .filter(teacher_id=self.request.user.teacher_id)
            .select_related('group', 'group__direction', 'original_teacher', 'payroll')
            .prefetch_related(
                Prefetch(
                    'attendance',
                    queryset=LessonAttendance.objects
                    .select_related('student')
                    .order_by('student__full_name', 'student_id'),
                )
            )
            .order_by('-lesson_date', '-id')
        )
        p = self.request.query_params
        d_from = p.get('from')
        d_to = p.get('to')
        group = p.get('group')
        if d_from:
            qs = qs.filter(lesson_date__gte=d_from)
        if d_to:
            qs = qs.filter(lesson_date__lte=d_to)
        if group:
            qs = qs.filter(group__name=group)
        return qs


class GroupProgressView(APIView):
    """
    GET /api/group-progress?group=<name> — матрица посещаемости группы для
    страницы группы в teacher SPA. Контракт ответа = admin
    /api/admin/groups/:id/progress; доступ гейтит services.get_group_progress
    (владелец группы или назначенный заменщик).
    """

    permission_classes = [IsTeacher]

    def get(self, request: Request) -> Response:
        group_name = request.query_params.get('group')
        if not group_name:
            return Response({'error': 'Параметр group обязателен'}, status=status.HTTP_400_BAD_REQUEST)
        result = services.get_group_progress(request.user.id, group_name)
        if '_error' in result:
            return Response({'error': result['_error']}, status=result['_status'])
        return Response(result)


class GroupDirectionsView(APIView):
    """
    GET /api/group-directions — карта {имя группы → направление+цвет} для ВСЕХ
    активных групп. Точный источник предмета/цвета (из БД `directions.color`)
    для календаря/отчёта, где /api/report (заморожен) направление не отдаёт.
    Фронт джойнит по имени группы. Роль teacher (данные не чувствительные —
    только справочник направлений; имена групп фронт и так видит в /api/report).
    """

    permission_classes = [IsTeacher]

    def get(self, request: Request) -> Response:
        rows = (
            Group.objects
            .filter(active=True)
            .values(
                'name',
                dir_name=F('direction__name'),
                color=F('direction__color'),
                is_ind=F('is_individual'),
                duration=F('lesson_duration_minutes'),
                total=effective_total_lessons_expr(),
            )
        )
        groups = {
            r['name']: {
                'direction': r['dir_name'],
                'color': r['color'],
                'isIndividual': r['is_ind'],
                # Ф4: half-lesson и лимит курса — структурно (не regex по имени).
                'lessonDurationMinutes': r['duration'],
                # Длина КУРСА ЭТОЙ ГРУППЫ: groups.lessons_total, если задано
                # (группа-остаток курса другой длины), иначе — длина курса
                # направления. См. apps.groups.course_length.
                'totalLessons': r['total'],
            }
            for r in rows
        }
        return Response({'groups': groups})
