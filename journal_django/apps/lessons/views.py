"""
Тонкие APIView для /api/admin/lessons.

Зеркалит Express routes/admin/lessons.js:
  GET    /api/admin/lessons                                   → список {rows,total,page,page_size}
  GET    /api/admin/lessons/:id                               → полный урок | 404
  POST   /api/admin/lessons                                   → 201 (полный урок)
  PATCH  /api/admin/lessons/:id                               → 200 | 404
  DELETE /api/admin/lessons/:id                               → 204 | 404
  PATCH  /api/admin/lessons/:lessonId/attendance/:studentId   → 200 {ok:true} | 404

Права (ReadStaffWriteAdmin): GET — manager/admin/superadmin; POST/PATCH/DELETE
и toggle посещаемости — только admin/superadmin (manager — read-only).
Зарплата за урок (payroll: вложенный объект в detail, плоские поля payroll_id/
total_students/present_count/payment/penalty в списке) видна ТОЛЬКО superadmin —
вырезается из GET-ответов для остальных ролей (_strip_payroll_for_role).

Сортировка: тихий fallback на default (как Express paginate()), без 400.
Back-compat: top-level query group_id/teacher_id/date_from/date_to мержатся
в filters, если соответствующий filter[*] не передан (зеркало lessons.js:18-21).
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ReadStaffWriteAdmin
from apps.lessons import services
from apps.lessons.exceptions import (
    AttendanceCompensatedElsewhere, AttendanceHasScheduledMakeup, AttendanceLockedByTransfer,
    CoursePositionVanished,
    LessonAlreadyRecorded, LessonHasMakeupResolutions, SystemLessonProtected,
    UnpaidAttendanceBlocked,
)
from apps.lessons.serializers import (
    AttendanceUpdateSerializer,
    LessonCreateSerializer,
    LessonUpdateSerializer,
    UnpaidSkipUpdateSerializer,
)

_DEFAULT_SORT_BY = 'lesson_date'
_DEFAULT_SORT_DIR = 'desc'
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 500

# Машиночитаемый код конфликта — тот же, что у teacher SPA
# (apps.teacher_spa.views.LESSON_ALREADY_RECORDED): урок за это занятие уже записан.
LESSON_ALREADY_RECORDED = 'lesson_already_recorded'

# У ученика нет оплаченных уроков. Код нужен фронту, чтобы отличить этот отказ от
# прочих 400 и поднять суперадмину модалку «записать в долг» вместо красной
# ошибки (обычная запись остаётся заблокированной для всех остальных ролей).
UNPAID_ATTENDANCE_BLOCKED = 'unpaid_attendance_blocked'


def _reject_debt_override(request: Request, data: dict):
    """
    Проверка права на `allow_debt` («записать, невзирая на долг»).

    Возвращает Response 403, если флаг передан кем-то кроме superadmin, иначе
    None. Право проверяется ЗДЕСЬ, а не в permission_classes: вьюхи открыты
    admin'у на запись (ReadStaffWriteAdmin), сужается только эта возможность.

    Молча игнорировать чужой флаг нельзя — клиент решил бы, что занятие записано
    в долг, тогда как сервер его отверг бы по балансу с другой формулировкой.
    """
    if not data.get('allow_debt'):
        return None
    if getattr(request.user, 'role', None) != 'superadmin':
        return Response(
            {'error': 'Записать занятие в долг может только суперадмин'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _parse_list_params(request: Request) -> dict:
    """
    Извлечь параметры пагинации/фильтров из query string.

    Зеркалит parsePaginationRequest() (services/pagination.js) + back-compat merge
    top-level фильтров из routes/admin/lessons.js.
    sort_by/sort_dir НЕ валидируются здесь — fallback делает repository (как Express).
    """
    qp = request.query_params

    # Зеркалим JS-семантику Number(x) || default (0/NaN — falsy → default),
    # см. parsePaginationRequest (services/pagination.js:49-52).
    try:
        page = max(1, int(qp.get('page') or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        raw_page_size = int(qp.get('page_size') or 0)
    except (TypeError, ValueError):
        raw_page_size = 0
    page_size = min(_MAX_PAGE_SIZE, max(1, raw_page_size or _DEFAULT_PAGE_SIZE))

    sort_by = qp.get('sort_by') or _DEFAULT_SORT_BY
    sort_dir = qp.get('sort_dir')
    if sort_dir not in ('asc', 'desc'):
        sort_dir = _DEFAULT_SORT_DIR

    # filter[key]=value
    filters: dict = {}
    for key, value in qp.items():
        if key.startswith('filter[') and key.endswith(']'):
            filters[key[7:-1]] = value

    # Back-compat: top-level фильтры (lessons.js:18-21), filter[*] имеет приоритет.
    if qp.get('group_id') and 'group_id' not in filters:
        filters['group_id'] = qp['group_id']
    if qp.get('teacher_id') and 'teacher_id' not in filters:
        filters['teacher_id'] = qp['teacher_id']
    if qp.get('date_from') and 'lesson_date_from' not in filters:
        filters['lesson_date_from'] = qp['date_from']
    if qp.get('date_to') and 'lesson_date_to' not in filters:
        filters['lesson_date_to'] = qp['date_to']

    return {
        'page': page,
        'page_size': page_size,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'filters': filters,
    }


_PAYROLL_ROW_KEYS = ('payroll_id', 'total_students', 'present_count', 'payment', 'penalty')


def _row_without_payroll(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in _PAYROLL_ROW_KEYS}


def _strip_payroll_for_role(data: dict, role: str) -> dict:
    """Зарплата за урок (payroll) видна только superadmin — вырезаем для остальных."""
    if role == 'superadmin':
        return data
    if 'payroll' in data:
        data = {**data, 'payroll': None}
    if isinstance(data.get('rows'), list):
        data = {**data, 'rows': [_row_without_payroll(r) for r in data['rows']]}
    return data


class LessonListCreateView(APIView):
    """
    GET  /api/admin/lessons  — список уроков
    POST /api/admin/lessons  — создать урок (транзакция)
    """

    permission_classes = [ReadStaffWriteAdmin]

    def get(self, request: Request) -> Response:
        params = _parse_list_params(request)
        data = services.list_lessons(**params)
        return Response(_strip_payroll_for_role(data, request.user.role))

    def post(self, request: Request) -> Response:
        serializer = LessonCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        denied = _reject_debt_override(request, serializer.validated_data)
        if denied is not None:
            return denied

        try:
            result = services.create_lesson_full(serializer.validated_data)
        except UnpaidAttendanceBlocked as e:
            # code — чтобы суперадмин увидел модалку «записать в долг», а не
            # красную ошибку; остальным ролям фронт покажет обычный отказ.
            return Response({'error': str(e), 'code': UNPAID_ATTENDANCE_BLOCKED},
                            status=status.HTTP_400_BAD_REQUEST)
        except LessonAlreadyRecorded as e:
            # Тот же конфликт, что у преподавателя: занятие уже записано. Без этой
            # ветки исключение уходило наверх необработанным и админ видел голый
            # 500 вместо «урок уже записан». code — чтобы фронт мог показать это
            # спокойным сообщением, а не красной ошибкой (как в teacher SPA).
            return Response(
                {'error': str(e), 'code': LESSON_ALREADY_RECORDED},
                status=status.HTTP_409_CONFLICT,
            )
        except CoursePositionVanished as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)

        full = services.get_lesson_full(result['lesson_id'])
        return Response(
            _strip_payroll_for_role(full, request.user.role),
            status=status.HTTP_201_CREATED,
        )


class LessonDetailView(APIView):
    """
    GET    /api/admin/lessons/:id  — полный урок
    PATCH  /api/admin/lessons/:id  — обновить meta
    DELETE /api/admin/lessons/:id  — удалить (CASCADE)
    """

    permission_classes = [ReadStaffWriteAdmin]

    def get(self, request: Request, pk: int) -> Response:
        lesson = services.get_lesson_full(pk)
        if lesson is None:
            raise NotFound({'error': 'Not found'})
        return Response(_strip_payroll_for_role(lesson, request.user.role))

    def patch(self, request: Request, pk: int) -> Response:
        serializer = LessonUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated = services.update_lesson(pk, serializer.validated_data)
        except SystemLessonProtected as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if updated is None:
            raise NotFound({'error': 'Not found'})
        return Response(updated)

    def delete(self, request: Request, pk: int) -> Response:
        try:
            ok = services.delete_lesson_full(pk)
        except (SystemLessonProtected, LessonHasMakeupResolutions) as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)


class AttendanceCellView(APIView):
    """
    PATCH /api/admin/lessons/:lessonId/attendance/:studentId — toggle одной ячейки.
    """

    permission_classes = [ReadStaffWriteAdmin]

    def patch(self, request: Request, lesson_id: int, student_id: int) -> Response:
        serializer = AttendanceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        denied = _reject_debt_override(request, serializer.validated_data)
        if denied is not None:
            return denied

        try:
            ok = services.update_attendance_cell(
                lesson_id, student_id, serializer.validated_data['present'],
                is_free=serializer.validated_data['is_free'],
                allow_debt=serializer.validated_data['allow_debt'],
            )
        except UnpaidAttendanceBlocked as e:
            return Response({'error': str(e), 'code': UNPAID_ATTENDANCE_BLOCKED},
                            status=status.HTTP_400_BAD_REQUEST)
        except (SystemLessonProtected, AttendanceCompensatedElsewhere,
                AttendanceHasScheduledMakeup, AttendanceLockedByTransfer) as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response({'ok': True})


class AttendanceUnpaidSkipView(APIView):
    """
    PATCH /api/admin/lessons/:lessonId/unpaid-skip/:studentId — ставит/снимает
    исход «неоплачиваемый пропуск» для ученика (в т.ч. на проведённом уроке и
    вновь добавленному ученику). Body: {value: bool}. 200 {ok:true} | 404 | 409.
    """

    permission_classes = [ReadStaffWriteAdmin]

    def patch(self, request: Request, lesson_id: int, student_id: int) -> Response:
        serializer = UnpaidSkipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            ok = services.set_unpaid_skip(
                lesson_id, student_id, serializer.validated_data['value'])
        except SystemLessonProtected as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response({'ok': True})
